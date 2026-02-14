"""Tests for UI workflows: themes, navigation, staging, settings persistence."""

import pytest
from PyQt5.QtWidgets import QApplication

from app import (
    MainWindow, generate_stylesheet, THEMES, set_theme, get_theme,
    ConfigurePage, SearchPage,
)
from core import ConfigManager, PackageInfo, PackageStagedEvent


@pytest.fixture
def main_window(qapp, tmp_path):
    """Create a MainWindow with temp config and DB paths."""
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "packages.db")

    # Patch the paths before construction
    import core
    orig_config_init = ConfigManager.__init__

    def patched_config_init(self, path):
        orig_config_init(self, config_path)

    import unittest.mock as mock
    with mock.patch.object(ConfigManager, '__init__', patched_config_init):
        with mock.patch('app.SearchEngine') as MockSE:
            MockSE.return_value.db_path = db_path
            MockSE.return_value.conn = None
            MockSE.return_value.threadpool = None
            win = MainWindow()

    yield win
    win.close()


# ── Theme Tests ──

class TestThemeSwitching:
    def test_all_themes_generate_valid_stylesheet(self):
        for name, theme_dict in THEMES.items():
            ss = generate_stylesheet(theme_dict)
            assert isinstance(ss, str)
            assert len(ss) > 100
            # Should reference theme colors
            assert theme_dict["bg_primary"] in ss
            assert theme_dict["accent"] in ss

    def test_set_theme_updates_current(self):
        original = get_theme()
        set_theme("Dark")
        assert get_theme() == THEMES["Dark"]
        set_theme("Nord")
        assert get_theme() == THEMES["Nord"]
        # Restore
        set_theme("Light")

    def test_invalid_theme_falls_back_to_light(self):
        set_theme("NonExistent")
        assert get_theme() == THEMES["Light"]


# ── Page Navigation ──

class TestPageNavigation:
    def test_sidebar_click_changes_page(self, main_window):
        main_window._go_to_page(2)
        assert main_window.stack.currentIndex() == 2

    def test_go_to_page_marks_previous_complete(self, main_window):
        main_window._go_to_page(2)
        assert main_window.sidebar.steps[0].completed is True
        assert main_window.sidebar.steps[1].completed is True
        assert main_window.sidebar.steps[2].completed is False


# ── Staging Events ──

class TestStagingWorkflow:
    def test_staged_event_adds_row(self, main_window):
        pkg = PackageInfo(
            name="flask", version="3.0.0", description="Web framework",
        )
        event = PackageStagedEvent(pkg, is_dependency=False)
        main_window.customEvent(event)

        assert "flask" in main_window.staged_packages
        # Check the staged_list_layout has a row
        count = main_window.search_page.staged_list_layout.count()
        assert count >= 1

    def test_staged_dependency_marked(self, main_window):
        pkg = PackageInfo(
            name="werkzeug", version="3.0.0", description="WSGI toolkit",
        )
        event = PackageStagedEvent(pkg, is_dependency=True)
        main_window.customEvent(event)

        staged = main_window.staged_packages.get("werkzeug")
        assert staged is not None
        assert staged.is_dependency is True

    def test_duplicate_staging_ignored(self, main_window):
        pkg = PackageInfo(
            name="click", version="8.0.0", description="CLI toolkit",
        )
        event1 = PackageStagedEvent(pkg, is_dependency=False)
        event2 = PackageStagedEvent(pkg, is_dependency=False)
        main_window.customEvent(event1)
        main_window.customEvent(event2)

        # Should only appear once
        assert len([k for k in main_window.staged_packages if k == "click"]) == 1


# ── ConfigurePage Settings ──

class TestConfigurePageSettings:
    def test_save_settings_persists_to_config(self, main_window):
        page = main_window.configure_page
        page.python_combo.setCurrentText("3.12")
        page.platform_combo.setCurrentText("win_amd64")
        page.include_deps.setChecked(False)
        page.mirror_edit.setText("https://custom.mirror/simple/")
        page.output_edit.setText("/tmp/test-output")

        page.save_settings()

        cm = main_window.config_manager
        assert cm.get("download.python_version") == "3.12"
        assert cm.get("download.platform") == "win_amd64"
        assert cm.get("download.include_dependencies") is False
        assert cm.get("network.pypi_mirror") == "https://custom.mirror/simple/"
        assert cm.get("download.default_path") == "/tmp/test-output"

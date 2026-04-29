"""GUI workflow tests. Skipped if PyQt5 (or pytest-qt) is not installed."""

from __future__ import annotations

import pytest

PyQt5 = pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")  # provided by pytest-qt

from localpip.core import PackageInfo  # noqa: E402
from localpip.gui import (  # noqa: E402
    THEMES,
    MainWindow,
    PackageStagedEvent,
    generate_stylesheet,
    get_theme,
    set_theme,
)


@pytest.fixture
def main_window(qapp, tmp_path):
    config_path = str(tmp_path / "config.json")
    win = MainWindow(config_path=config_path)
    yield win
    win.close()


class TestThemes:
    def test_all_themes_generate_valid_stylesheet(self):
        for _name, theme_dict in THEMES.items():
            ss = generate_stylesheet(theme_dict)
            assert isinstance(ss, str)
            assert len(ss) > 100
            assert theme_dict["bg_primary"] in ss
            assert theme_dict["accent"] in ss

    def test_set_theme_updates_current(self):
        set_theme("Dark")
        assert get_theme() == THEMES["Dark"]
        set_theme("Nord")
        assert get_theme() == THEMES["Nord"]
        set_theme("Light")  # restore

    def test_invalid_theme_falls_back_to_light(self):
        set_theme("NonExistent")
        assert get_theme() == THEMES["Light"]


class TestNavigation:
    def test_sidebar_click_changes_page(self, main_window):
        main_window._go_to_page(2)
        assert main_window.stack.currentIndex() == 2

    def test_go_to_page_marks_previous_complete(self, main_window):
        main_window._go_to_page(2)
        assert main_window.sidebar.steps[0].completed is True
        assert main_window.sidebar.steps[1].completed is True
        assert main_window.sidebar.steps[2].completed is False


class TestStaging:
    def test_staged_event_adds_row(self, main_window):
        pkg = PackageInfo(name="flask", version="3.0.0", summary="Web framework")
        main_window.customEvent(PackageStagedEvent(pkg, is_dependency=False))
        assert "flask" in main_window.staged_packages
        assert main_window.search_page.staged_list_layout.count() >= 1

    def test_dependency_marked(self, main_window):
        pkg = PackageInfo(name="werkzeug", version="3.0.0", summary="WSGI")
        main_window.customEvent(PackageStagedEvent(pkg, is_dependency=True))
        staged = main_window.staged_packages.get("werkzeug")
        assert staged is not None
        assert staged.is_dependency is True

    def test_duplicate_staging_ignored(self, main_window):
        pkg = PackageInfo(name="click", version="8.0.0", summary="CLI toolkit")
        main_window.customEvent(PackageStagedEvent(pkg, is_dependency=False))
        main_window.customEvent(PackageStagedEvent(pkg, is_dependency=False))
        assert len([k for k in main_window.staged_packages if k == "click"]) == 1


class TestConfigurePageSettings:
    def test_save_settings_persists_to_config(self, main_window):
        page = main_window.configure_page
        page.python_combo.setCurrentText("3.12")
        page.platform_combo.setCurrentText("win_amd64")
        page.include_deps.setChecked(False)
        page.output_edit.setText("/tmp/test-output")

        # Clear existing mirrors and add a custom one
        while page.mirrors_layout.count():
            item = page.mirrors_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        page._add_mirror_row("https://custom.mirror/simple/")

        page.save_settings()

        cm = main_window.config_manager
        assert cm.get("download.python_version") == "3.12"
        assert cm.get("download.platform") == "win_amd64"
        assert cm.get("download.include_dependencies") is False
        assert cm.get("network.pypi_mirrors") == ["https://custom.mirror/simple/"]
        assert cm.get("download.default_path") == "/tmp/test-output"

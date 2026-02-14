"""Tests for ConfigManager: dot-notation access, load/save, defaults."""

import json
import os
import pytest

from core import ConfigManager


class TestConfigManagerGet:
    def test_get_top_level_section(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert isinstance(cm.get("network"), dict)

    def test_get_nested_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("network.timeout") == 30

    def test_get_deeply_nested_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("ui.window_size.width") == 1100

    def test_get_missing_key_returns_default(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("network.nonexistent") is None
        assert cm.get("network.nonexistent", "fallback") == "fallback"

    def test_get_missing_section_returns_default(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("totally.made.up", 42) == 42


class TestConfigManagerSet:
    def test_set_existing_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("network.timeout", 60)
        assert cm.get("network.timeout") == 60

    def test_set_creates_intermediate_keys(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("custom.nested.value", True)
        assert cm.get("custom.nested.value") is True


class TestConfigManagerLoadSave:
    def test_save_and_reload_round_trip(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("network.timeout", 99)
        cm.save()

        cm2 = ConfigManager(tmp_config)
        assert cm2.get("network.timeout") == 99

    def test_load_merges_missing_defaults(self, tmp_config):
        """If a saved config is missing a key that exists in DEFAULT, it's filled in."""
        partial = {"network": {"pypi_mirror": "https://custom.org/simple/"}}
        with open(tmp_config, "w") as f:
            json.dump(partial, f)

        cm = ConfigManager(tmp_config)
        # Preserved from file
        assert cm.get("network.pypi_mirror") == "https://custom.org/simple/"
        # Filled from defaults
        assert cm.get("network.timeout") == 30
        assert cm.get("ui.theme") == "Light"

    def test_corrupt_json_falls_back_to_defaults(self, tmp_config):
        with open(tmp_config, "w") as f:
            f.write("{bad json!!")

        cm = ConfigManager(tmp_config)
        assert cm.get("network.timeout") == 30
        assert cm.get("ui.theme") == "Light"

    def test_no_config_file_uses_defaults(self, tmp_config):
        assert not os.path.exists(tmp_config)
        cm = ConfigManager(tmp_config)
        assert cm.get("download.include_dependencies") is True

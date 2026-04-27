"""ConfigManager: dot-notation, defaults, save/load round-trip, migration."""

from __future__ import annotations

import json
import os

import pytest

from localpip.core import ConfigManager


class TestGet:
    def test_top_level_section(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert isinstance(cm.get("network"), dict)

    def test_nested_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("network.timeout") == 30

    def test_deeply_nested_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("ui.window_size.width") == 1100

    def test_missing_key_returns_default(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("network.missing") is None
        assert cm.get("network.missing", "fallback") == "fallback"

    def test_missing_section_returns_default(self, tmp_config):
        cm = ConfigManager(tmp_config)
        assert cm.get("totally.made.up", 42) == 42


class TestSet:
    def test_set_existing_key(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("network.timeout", 60)
        assert cm.get("network.timeout") == 60

    def test_set_creates_intermediate_keys(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("custom.nested.value", True)
        assert cm.get("custom.nested.value") is True


class TestLoadSave:
    def test_round_trip(self, tmp_config):
        cm = ConfigManager(tmp_config)
        cm.set("network.timeout", 99)
        cm.save()

        cm2 = ConfigManager(tmp_config)
        assert cm2.get("network.timeout") == 99

    def test_load_merges_missing_defaults(self, tmp_config):
        partial = {"network": {"pypi_mirrors": ["https://custom.org/simple/"]}}
        with open(tmp_config, "w") as f:
            json.dump(partial, f)

        cm = ConfigManager(tmp_config)
        assert cm.get("network.pypi_mirrors") == ["https://custom.org/simple/"]
        assert cm.get("network.timeout") == 30
        assert cm.get("ui.theme") == "Light"

    def test_migrates_legacy_pypi_mirror_string(self, tmp_config):
        partial = {"network": {"pypi_mirror": "https://legacy.org/simple/"}}
        with open(tmp_config, "w") as f:
            json.dump(partial, f)

        cm = ConfigManager(tmp_config)
        assert cm.get("network.pypi_mirrors") == ["https://legacy.org/simple/"]
        assert cm.get("network.pypi_mirror") is None

    def test_drops_legacy_when_both_present(self, tmp_config):
        partial = {
            "network": {
                "pypi_mirror": "https://legacy.org/simple/",
                "pypi_mirrors": ["https://new.org/simple/"],
            }
        }
        with open(tmp_config, "w") as f:
            json.dump(partial, f)

        cm = ConfigManager(tmp_config)
        assert cm.get("network.pypi_mirrors") == ["https://new.org/simple/"]
        assert cm.get("network.pypi_mirror") is None

    def test_corrupt_json_falls_back_to_defaults(self, tmp_config):
        with open(tmp_config, "w") as f:
            f.write("{bad json!!")

        cm = ConfigManager(tmp_config)
        assert cm.get("network.timeout") == 30
        assert cm.get("ui.theme") == "Light"

    def test_no_file_uses_defaults(self, tmp_config):
        assert not os.path.exists(tmp_config)
        cm = ConfigManager(tmp_config)
        assert cm.get("download.include_dependencies") is True
        assert cm.get("download.verify_checksums") is True

    def test_save_creates_parent_directory(self, tmp_path):
        nested = str(tmp_path / "nested" / "dirs" / "config.json")
        cm = ConfigManager(nested)
        cm.set("download.python_version", "3.12")
        cm.save()
        assert os.path.exists(nested)
        with open(nested) as f:
            assert json.load(f)["download"]["python_version"] == "3.12"

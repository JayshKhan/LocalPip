"""Pip/venv environment pack, unpack, and verify."""

from __future__ import annotations

import json

from localpip.cli import main
from localpip.pack import (
    PackError,
    pack_environment,
    read_manifest,
    unpack_archive,
    verify_archive,
)


def _make_fake_venv(tmp_path):
    env = tmp_path / "venv"
    bin_dir = env / "bin"
    dist = env / "lib" / "python3.11" / "site-packages" / "demo-1.0.dist-info"
    bin_dir.mkdir(parents=True)
    dist.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.11.8\n", encoding="utf-8")
    (bin_dir / "python").write_text("# fake python\n", encoding="utf-8")
    script = bin_dir / "demo"
    script.write_text(f"#!{env}/bin/python\nprint('demo')\n", encoding="utf-8")
    script.chmod(0o755)
    (dist / "METADATA").write_text("Name: demo\nVersion: 1.0\n", encoding="utf-8")
    return env


class TestPackEnvironment:
    def test_pack_writes_manifest_and_payload(self, tmp_path):
        env = _make_fake_venv(tmp_path)
        archive = tmp_path / "env.tar.gz"

        result = pack_environment(str(env), str(archive))
        manifest = read_manifest(str(archive))

        assert result.file_count >= 4
        assert archive.exists()
        assert manifest["source_prefix"] == str(env)
        assert manifest["python"]["version"] == "3.11.8"
        assert manifest["packages"] == [{"name": "demo", "version": "1.0"}]
        assert "bin/demo" in manifest["relocatable_scripts"]

    def test_pack_rejects_non_venv_directory(self, tmp_path):
        archive = tmp_path / "env.tar.gz"

        try:
            pack_environment(str(tmp_path), str(archive))
        except PackError as e:
            assert "missing pyvenv.cfg" in str(e)
        else:
            raise AssertionError("expected PackError")

    def test_exclude_pattern_omits_files(self, tmp_path):
        env = _make_fake_venv(tmp_path)
        (env / "cache.pyc").write_bytes(b"compiled")
        archive = tmp_path / "env.tar.gz"

        pack_environment(str(env), str(archive), exclude=["bin/python"])
        manifest = read_manifest(str(archive))
        paths = {entry["path"] for entry in manifest["files"]}

        assert "cache.pyc" not in paths
        assert "bin/python" not in paths


class TestUnpackAndVerify:
    def test_unpack_repairs_shebang_and_verify_destination(self, tmp_path):
        env = _make_fake_venv(tmp_path)
        archive = tmp_path / "env.tar.gz"
        dest = tmp_path / "unpacked"

        pack_environment(str(env), str(archive))
        result = unpack_archive(str(archive), str(dest))

        assert result.repaired == ["bin/demo"]
        assert (dest / "pyvenv.cfg").exists()
        assert (dest / "bin" / "demo").read_text(encoding="utf-8").startswith(
            f"#!{dest}/bin/python"
        )
        assert verify_archive(str(archive)).ok
        assert verify_archive(str(archive), destination=str(dest)).ok

    def test_verify_destination_detects_modified_file(self, tmp_path):
        env = _make_fake_venv(tmp_path)
        archive = tmp_path / "env.tar.gz"
        dest = tmp_path / "unpacked"

        pack_environment(str(env), str(archive))
        unpack_archive(str(archive), str(dest))
        (dest / "pyvenv.cfg").write_text("changed\n", encoding="utf-8")

        result = verify_archive(str(archive), destination=str(dest))

        assert not result.ok
        assert any("hash mismatch" in error for error in result.errors or [])


class TestPackCli:
    def test_pack_unpack_verify_commands_json(self, tmp_path, capsys):
        env = _make_fake_venv(tmp_path)
        archive = tmp_path / "env.tar.gz"
        dest = tmp_path / "unpacked"

        rc = main(["pack", str(env), "-o", str(archive), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["file_count"] >= 4

        rc = main(["verify", str(archive), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True

        rc = main(["unpack", str(archive), "-d", str(dest), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["repaired"] == ["bin/demo"]

        rc = main(["verify", str(archive), "-d", str(dest), "--json"])
        assert rc == 0

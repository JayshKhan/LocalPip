"""CLI smoke tests: parser, info command, error paths."""

from __future__ import annotations

import json
import os

import pytest

from localpip.cli import build_parser, fmt_bytes, main


class TestParser:
    def test_download_requires_subcommand(self):
        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args([])

    def test_download_args(self):
        p = build_parser()
        args = p.parse_args(["download", "flask", "--python", "3.12"])
        assert args.command == "download"
        assert args.packages == ["flask"]
        assert args.python == "3.12"

    def test_download_with_requirements_file(self):
        p = build_parser()
        args = p.parse_args(["download", "-r", "req.txt", "-r", "extra.txt"])
        assert args.requirement == ["req.txt", "extra.txt"]

    def test_info_requires_package(self):
        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["info"])

    def test_resolve_no_packages_fails_at_runtime(self, tmp_path):
        # Parser allows it; cmd_resolve calls sys.exit(2)
        cfg = str(tmp_path / "c.json")
        with pytest.raises(SystemExit) as exc:
            main(["resolve", "--config", cfg])
        assert exc.value.code == 2

    def test_mirror_repeatable(self):
        p = build_parser()
        args = p.parse_args([
            "download", "flask",
            "--mirror", "https://a.org/simple/",
            "--mirror", "https://b.org/simple/",
        ])
        assert args.mirror == ["https://a.org/simple/", "https://b.org/simple/"]


class TestFormatting:
    def test_fmt_bytes(self):
        assert fmt_bytes(0) == "0 B"
        assert fmt_bytes(512) == "512 B"
        assert fmt_bytes(2048) == "2.0 KiB"
        assert fmt_bytes(2 * 1024 ** 2) == "2.0 MiB"
        assert fmt_bytes(int(1.5 * 1024 ** 3)) == "1.50 GiB"


class TestInfoCommand:
    def test_info_404_returns_error(self, tmp_path, capsys, url_router):
        url_router({})  # everything 404s
        cfg = str(tmp_path / "c.json")
        rc = main(["info", "nonexistent-pkg-xyz", "--config", cfg])
        assert rc == 1

    def test_info_prints_package(self, tmp_path, capsys, url_router, fake_response,
                                  pypi_json_response):
        url_router({
            "https://pypi.org/pypi/flask/json":
            fake_response(pypi_json_response(name="flask", version="3.0.0",
                                              deps=["werkzeug"]))
        })
        cfg = str(tmp_path / "c.json")
        rc = main(["info", "flask", "--config", cfg])
        assert rc == 0
        captured = capsys.readouterr()
        assert "flask" in captured.out
        assert "3.0.0" in captured.out


class TestVersion:
    def test_version_flag_prints_and_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0


class TestNewSubcommands:
    def test_lock_subcommand_in_parser(self):
        p = build_parser()
        args = p.parse_args(["lock", "flask", "-o", "/tmp/lock.json"])
        assert args.command == "lock"
        assert args.packages == ["flask"]
        assert args.output == "/tmp/lock.json"

    def test_list_subcommand_in_parser(self):
        p = build_parser()
        args = p.parse_args(["list", "/some/dir"])
        assert args.command == "list"
        assert args.directory == "/some/dir"

    def test_clean_dry_run(self):
        p = build_parser()
        args = p.parse_args(["clean", "/some/dir", "--dry-run"])
        assert args.dry_run is True

    def test_download_lock_flag(self):
        p = build_parser()
        args = p.parse_args(["download", "--lock", "lock.json", "-o", "wheels"])
        assert args.lock == "lock.json"


class TestListAndClean:
    def test_list_empty_directory(self, tmp_path, capsys):
        rc = main(["list", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 wheel(s)" in out

    def test_list_with_wheels(self, tmp_path, capsys):
        (tmp_path / "flask-3.0.0-py3-none-any.whl").write_bytes(b"x" * 100)
        (tmp_path / "click-8.0.0-py3-none-any.whl").write_bytes(b"y" * 200)
        rc = main(["list", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "flask" in out
        assert "click" in out
        assert "2 wheel(s)" in out

    def test_list_json_output(self, tmp_path, capsys):
        (tmp_path / "flask-3.0.0-py3-none-any.whl").write_bytes(b"x" * 100)
        rc = main(["list", str(tmp_path), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["directory"] == str(tmp_path)
        assert len(payload["entries"]) == 1
        assert payload["entries"][0]["name"] == "flask"

    def test_clean_removes_part_files(self, tmp_path, capsys):
        (tmp_path / "good.whl").write_bytes(b"x")
        (tmp_path / "broken.whl.part").write_bytes(b"x")
        rc = main(["clean", str(tmp_path)])
        assert rc == 0
        assert (tmp_path / "good.whl").exists()
        assert not (tmp_path / "broken.whl.part").exists()

    def test_clean_dry_run_keeps_files(self, tmp_path):
        (tmp_path / "broken.whl.part").write_bytes(b"x")
        rc = main(["clean", str(tmp_path), "--dry-run"])
        assert rc == 0
        assert (tmp_path / "broken.whl.part").exists()


class TestLockCommand:
    def test_lock_writes_file(self, tmp_path, url_router, fake_response,
                                pypi_json_response, capsys):
        url_router({
            "https://pypi.org/pypi/flask/json":
                fake_response(pypi_json_response(name="flask", version="3.0.0")),
        })
        out = str(tmp_path / "lock.json")
        rc = main([
            "lock", "flask",
            "--no-deps", "--config", str(tmp_path / "cfg.json"),
            "-o", out, "--no-cache",
        ])
        assert rc == 0
        assert os.path.exists(out)
        with open(out) as f:
            data = json.load(f)
        assert data["lockfile_version"] == "1"
        assert len(data["packages"]) == 1
        assert data["packages"][0]["name"] == "flask"


class TestJsonOutput:
    def test_info_json(self, tmp_path, url_router, fake_response,
                        pypi_json_response, capsys):
        url_router({
            "https://pypi.org/pypi/flask/json":
                fake_response(pypi_json_response(name="flask", version="3.0.0")),
        })
        rc = main([
            "info", "flask", "--json",
            "--config", str(tmp_path / "cfg.json"), "--no-cache",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["name"] == "flask"
        assert payload["version"] == "3.0.0"
        assert payload["best_wheel"].endswith(".whl")

"""CLI smoke tests: parser, info command, error paths."""

from __future__ import annotations

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

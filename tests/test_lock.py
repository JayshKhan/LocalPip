"""LockFile: serialize, round-trip, deterministic install."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from localpip.core import LockEntry, LockFile, PackageInfo, Target


def _pkg(name="flask", version="3.0.0", deps=None):
    return PackageInfo(
        name=name,
        version=version,
        requires_dist=deps or [],
        files=[
            {
                "filename": f"{name}-{version}-py3-none-any.whl",
                "url": f"https://files.pythonhosted.org/{name}-{version}-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "digests": {"sha256": "a" * 64},
            }
        ],
    )


class TestLockFileSerialization:
    def test_round_trip(self, tmp_path):
        lock = LockFile(
            target=Target("3.11", "any"),
            packages=[
                LockEntry(
                    name="flask",
                    version="3.0.0",
                    filename="flask-3.0.0-py3-none-any.whl",
                    url="https://x/flask.whl",
                    sha256="b" * 64,
                ),
            ],
            generated_at="2026-04-29T12:00:00Z",
        )
        path = str(tmp_path / "lock.json")
        lock.write(path)

        loaded = LockFile.read(path)
        assert loaded.target.python_version == "3.11"
        assert loaded.target.platform == "any"
        assert len(loaded.packages) == 1
        assert loaded.packages[0].name == "flask"
        assert loaded.packages[0].sha256 == "b" * 64

    def test_unsupported_version_raises(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "lockfile_version": "999",
                    "target": {"python_version": "3.11", "platform": "any"},
                    "packages": [],
                },
                f,
            )
        with pytest.raises(ValueError, match="unsupported lockfile"):
            LockFile.read(path)

    def test_from_resolution_picks_wheels(self):
        resolved = [
            (_pkg("flask", "3.0.0"), False),
            (_pkg("werkzeug", "3.0.1"), True),
        ]
        lock = LockFile.from_resolution(resolved, Target("3.11", "any"))
        assert len(lock.packages) == 2
        assert lock.packages[0].name == "flask"
        assert lock.packages[0].is_dependency is False
        assert lock.packages[1].is_dependency is True
        assert lock.packages[0].kind == "wheel"
        assert lock.packages[0].sha256 == "a" * 64

    def test_from_resolution_skips_unmatched(self):
        # Wheel only published for an incompatible platform → skipped (no sdist)
        pkg = PackageInfo(
            name="badpkg", version="1.0",
            files=[
                {
                    "filename": "badpkg-1.0-cp310-cp310-win_amd64.whl",
                    "url": "https://x/p.whl",
                    "packagetype": "bdist_wheel",
                }
            ],
        )
        resolved = [(pkg, False)]
        lock = LockFile.from_resolution(
            resolved, Target("3.11", "manylinux2014_x86_64"), allow_sdist=False,
        )
        assert lock.packages == []


class TestEngineDownloadLocked:
    def test_lock_install_deterministic(self, tmp_path, url_router, fake_response):
        body = b"flask wheel content"
        sha = hashlib.sha256(body).hexdigest()
        url_router({"https://files/flask.whl": fake_response(body)})

        from localpip.core import ConfigManager, Engine
        cfg = ConfigManager(str(tmp_path / "cfg.json"))
        engine = Engine(config=cfg, target=Target("3.11", "any"), use_cache=False)

        lock = LockFile(
            target=Target("3.11", "any"),
            packages=[
                LockEntry(
                    name="flask", version="3.0.0",
                    filename="flask-3.0.0-py3-none-any.whl",
                    url="https://files/flask.whl",
                    sha256=sha,
                ),
            ],
        )

        results = engine.download_locked(lock, output_dir=str(tmp_path / "out"))
        assert len(results) == 1
        assert results[0].ok
        assert results[0].sha256 == sha

    def test_lock_install_sha_mismatch_fails(self, tmp_path, url_router, fake_response):
        body = b"flask wheel content"
        url_router({"https://files/flask.whl": fake_response(body)})

        from localpip.core import ConfigManager, Engine
        cfg = ConfigManager(str(tmp_path / "cfg.json"))
        engine = Engine(config=cfg, target=Target("3.11", "any"), use_cache=False)

        lock = LockFile(
            target=Target("3.11", "any"),
            packages=[
                LockEntry(
                    name="flask", version="3.0.0",
                    filename="flask-3.0.0-py3-none-any.whl",
                    url="https://files/flask.whl",
                    sha256="0" * 64,  # wrong
                ),
            ],
        )

        results = engine.download_locked(lock, output_dir=str(tmp_path / "out"))
        assert not results[0].ok
        assert "SHA-256 mismatch" in results[0].error

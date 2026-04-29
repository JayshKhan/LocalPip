"""Downloader: atomic streaming, sha256 verification, skip-existing, errors."""

from __future__ import annotations

import hashlib
import os

import pytest

from localpip.core import (
    Downloader,
    HTTPClient,
    PackageInfo,
    Target,
)


def _http():
    return HTTPClient(timeout=2, max_retries=1)


def _pkg_with_wheel(content: bytes, filename="pkg-1.0-py3-none-any.whl",
                    url="https://example.com/pkg.whl", sha256=None):
    digests = {}
    if sha256:
        digests["sha256"] = sha256
    return PackageInfo(
        name="pkg",
        version="1.0",
        files=[
            {
                "filename": filename,
                "url": url,
                "packagetype": "bdist_wheel",
                "digests": digests,
            }
        ],
    )


class TestDownload:
    def test_writes_file_atomically(self, tmp_path, url_router, fake_response):
        body = b"\x00wheelcontents" * 100
        url_router({"https://example.com/pkg.whl": fake_response(body)})

        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [_pkg_with_wheel(body)], Target("3.11", "any"), str(tmp_path),
            verify_sha256=False,
        )
        assert len(results) == 1
        r = results[0]
        assert r.ok
        assert os.path.exists(r.path)
        with open(r.path, "rb") as f:
            assert f.read() == body
        # No leftover .part files
        assert not any(p.suffix == ".part" for p in tmp_path.iterdir())

    def test_sha256_verification_passes(self, tmp_path, url_router, fake_response):
        body = b"hello world"
        sha = hashlib.sha256(body).hexdigest()
        url_router({"https://example.com/pkg.whl": fake_response(body)})

        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [_pkg_with_wheel(body, sha256=sha)],
            Target("3.11", "any"),
            str(tmp_path),
            verify_sha256=True,
        )
        assert results[0].ok
        assert results[0].sha256 == sha

    def test_sha256_mismatch_fails_and_cleans_up(self, tmp_path, url_router,
                                                   fake_response):
        body = b"hello world"
        url_router({"https://example.com/pkg.whl": fake_response(body)})

        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [_pkg_with_wheel(body, sha256="0" * 64)],
            Target("3.11", "any"),
            str(tmp_path),
            verify_sha256=True,
        )
        assert not results[0].ok
        assert "SHA-256 mismatch" in results[0].error
        # File should be cleaned up
        assert not os.path.exists(results[0].path)

    def test_skip_existing_file(self, tmp_path):
        existing = tmp_path / "pkg-1.0-py3-none-any.whl"
        existing.write_bytes(b"already here")

        # No url_router → urlopen would fail. But we shouldn't reach it.
        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [_pkg_with_wheel(b"")], Target("3.11", "any"), str(tmp_path),
            verify_sha256=False,
        )
        assert results[0].skipped
        assert results[0].size == len(b"already here")

    def test_no_compatible_wheel_records_error(self, tmp_path):
        pkg = PackageInfo(
            name="pkg", version="1.0",
            files=[{
                "filename": "pkg-1.0-cp310-cp310-win_amd64.whl",
                "url": "https://x.com/p.whl",
                "packagetype": "bdist_wheel",
            }],
        )
        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [pkg], Target("3.11", "manylinux2014_x86_64"),
            str(tmp_path), verify_sha256=False, allow_sdist=False,
        )
        assert not results[0].ok
        # New diagnostic mentions tags actually published
        assert "no wheel matches" in results[0].error
        assert "cp310-cp310-win_amd64" in results[0].error

    def test_event_callbacks_fire(self, tmp_path, url_router, fake_response):
        body = b"chunk" * 50
        url_router({"https://example.com/pkg.whl": fake_response(body)})

        events = []

        def on_event(event, **kw):
            events.append(event)

        dl = Downloader(_http(), max_workers=1)
        dl.download([_pkg_with_wheel(body)], Target("3.11", "any"),
                    str(tmp_path), on_event=on_event, verify_sha256=False)
        assert "start" in events
        assert "complete" in events

    def test_cancel_aborts_download(self, tmp_path, url_router, fake_response):
        body = b"x" * 100_000
        url_router({"https://example.com/pkg.whl": fake_response(body)})

        dl = Downloader(_http(), max_workers=1)
        # Cancel before starting; chunk_cb will return False on first event
        dl.cancel("pkg-1.0-py3-none-any.whl")
        results = dl.download([_pkg_with_wheel(body)], Target("3.11", "any"),
                              str(tmp_path), verify_sha256=False)
        assert not results[0].ok
        # Partial file should not be left behind
        assert not os.path.exists(results[0].path)

    def test_sdist_fallback_when_no_wheel_matches(self, tmp_path, url_router,
                                                    fake_response):
        body = b"sdist contents"
        url_router({"https://example.com/pkg.tar.gz": fake_response(body)})

        pkg = PackageInfo(
            name="pkg", version="1.0",
            files=[
                {  # incompatible wheel
                    "filename": "pkg-1.0-cp310-cp310-win_amd64.whl",
                    "url": "https://example.com/pkg.whl",
                    "packagetype": "bdist_wheel",
                },
                {  # sdist fallback
                    "filename": "pkg-1.0.tar.gz",
                    "url": "https://example.com/pkg.tar.gz",
                    "packagetype": "sdist",
                },
            ],
        )
        events = []

        def on_event(event, **kw):
            events.append(event)

        dl = Downloader(_http(), max_workers=1)
        results = dl.download(
            [pkg],
            Target("3.11", "manylinux2014_x86_64"),
            str(tmp_path),
            on_event=on_event,
            verify_sha256=False,
            allow_sdist=True,
        )
        assert results[0].ok
        assert results[0].filename == "pkg-1.0.tar.gz"
        assert "sdist_fallback" in events

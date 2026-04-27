"""Shared fixtures for LocalPip tests."""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional

import pytest

from localpip.core import PackageInfo, Target


@pytest.fixture
def tmp_config(tmp_path) -> str:
    return str(tmp_path / "config.json")


@pytest.fixture
def tmp_db(tmp_path) -> str:
    """Kept for back-compat with older tests (no longer used by core)."""
    return str(tmp_path / "packages.db")


@pytest.fixture
def target_any() -> Target:
    return Target(python_version="3.11", platform="any")


@pytest.fixture
def sample_package_info():
    """Factory for PackageInfo with realistic wheel files."""

    def _make(
        name: str = "requests",
        version: str = "2.31.0",
        deps: Optional[List[str]] = None,
        files: Optional[List[Dict[str, Any]]] = None,
    ) -> PackageInfo:
        if files is None:
            files = [
                {
                    "filename": f"{name}-{version}-py3-none-any.whl",
                    "url": f"https://files.pythonhosted.org/{name}-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "digests": {"sha256": "0" * 64},
                },
            ]
        return PackageInfo(
            name=name,
            version=version,
            summary=f"A test package: {name}",
            author="Test Author",
            license="MIT",
            requires_dist=deps or [],
            files=files,
        )

    return _make


@pytest.fixture
def pypi_json_response():
    """Factory for a PyPI JSON API response."""

    def _make(name="requests", version="2.31.0", deps=None):
        return {
            "info": {
                "name": name,
                "version": version,
                "summary": f"A test package: {name}",
                "author": "Test Author",
                "license": "MIT",
                "requires_dist": deps,
            },
            "releases": {
                version: [
                    {
                        "filename": f"{name}-{version}-py3-none-any.whl",
                        "url": f"https://files.pythonhosted.org/{name}-{version}-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                        "digests": {"sha256": "0" * 64},
                    }
                ]
            },
        }

    return _make


class FakeResponse:
    """Minimal urllib.response stand-in for unittest.mock to return."""

    def __init__(self, payload: Any, status: int = 200, headers: Optional[Dict] = None):
        if isinstance(payload, (dict, list)):
            self._body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, bytes):
            self._body = payload
        elif isinstance(payload, str):
            self._body = payload.encode("utf-8")
        else:
            raise TypeError(f"unsupported payload type: {type(payload)}")
        self.status = status
        self.headers = headers or {}
        self._buf = io.BytesIO(self._body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n) if n != -1 else self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._buf.close()
        return False


@pytest.fixture
def fake_response():
    return FakeResponse


@pytest.fixture
def url_router(monkeypatch):
    """Install a urllib.request.urlopen stub that dispatches by URL.

    Usage:
        url_router({"https://x.com/api": fake_response({"ok": True})})
    """

    def _install(routes: Dict[str, FakeResponse]):
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url not in routes:
                from urllib.error import HTTPError

                raise HTTPError(url, 404, "Not Found", {}, None)
            value = routes[url]
            if callable(value):
                return value()
            return value

        monkeypatch.setattr(
            "localpip.core.urllib.request.urlopen", fake_urlopen
        )

    return _install

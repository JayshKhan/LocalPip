"""Shared fixtures for LocalPip tests."""

import os
import json
import pytest
from PyQt5.QtWidgets import QApplication

from core import PackageInfo


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication (required by PyQt5 tests)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def tmp_config(tmp_path):
    """Return path to a temp config.json."""
    return str(tmp_path / "config.json")


@pytest.fixture
def tmp_db(tmp_path):
    """Return path to a temp SQLite database."""
    return str(tmp_path / "packages.db")


@pytest.fixture
def sample_package_info():
    """Factory for PackageInfo with realistic wheel URLs."""
    def _make(name="requests", version="2.31.0", deps=None, urls=None):
        if urls is None:
            urls = [
                {
                    "filename": f"{name}-{version}-py3-none-any.whl",
                    "url": f"https://files.pythonhosted.org/{name}-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                },
            ]
        return PackageInfo(
            name=name,
            version=version,
            description=f"A test package: {name}",
            author="Test Author",
            license="MIT",
            dependencies=deps or [],
            urls=urls,
        )
    return _make


@pytest.fixture
def pypi_json_response():
    """Factory for a realistic PyPI JSON API response."""
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
                    }
                ]
            },
        }
    return _make

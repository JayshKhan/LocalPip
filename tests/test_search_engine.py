"""Tests for SearchEngine: PyPI queries (mocked HTTP), version specifiers."""

import responses
import pytest

from core import SearchEngine


PYPI_MIRROR = "https://pypi.org/simple/"


class TestGetPackageDetails:
    @responses.activate
    def test_successful_fetch(self, qapp, tmp_db, pypi_json_response):
        body = pypi_json_response(name="requests", version="2.31.0", deps=["urllib3"])
        responses.add(
            responses.GET,
            "https://pypi.org/pypi/requests/json",
            json=body,
            status=200,
        )

        se = SearchEngine(tmp_db)
        pkg = se.get_package_details("requests", PYPI_MIRROR)

        assert pkg is not None
        assert pkg.name == "requests"
        assert pkg.version == "2.31.0"
        assert pkg.description == "A test package: requests"
        assert pkg.dependencies == ["urllib3"]

    @responses.activate
    def test_version_specifier_filtering(self, qapp, tmp_db):
        body = {
            "info": {
                "name": "requests",
                "version": "2.31.0",
                "summary": "HTTP lib",
                "author": "A",
                "license": "MIT",
                "requires_dist": None,
            },
            "releases": {
                "2.28.0": [{"filename": "requests-2.28.0-py3-none-any.whl",
                             "url": "https://x.com/2.28.whl", "packagetype": "bdist_wheel"}],
                "2.31.0": [{"filename": "requests-2.31.0-py3-none-any.whl",
                             "url": "https://x.com/2.31.whl", "packagetype": "bdist_wheel"}],
                "3.0.0": [{"filename": "requests-3.0.0-py3-none-any.whl",
                            "url": "https://x.com/3.0.whl", "packagetype": "bdist_wheel"}],
            },
        }
        responses.add(responses.GET, "https://pypi.org/pypi/requests/json", json=body, status=200)

        # Version-specific response for 2.31.0
        v_body = {
            "info": {
                "name": "requests", "version": "2.31.0",
                "summary": "HTTP lib", "author": "A", "license": "MIT",
                "requires_dist": None,
            },
            "releases": {
                "2.31.0": [{"filename": "requests-2.31.0-py3-none-any.whl",
                             "url": "https://x.com/2.31.whl", "packagetype": "bdist_wheel"}],
            },
        }
        responses.add(responses.GET, "https://pypi.org/pypi/requests/2.31.0/json", json=v_body, status=200)

        se = SearchEngine(tmp_db)
        pkg = se.get_package_details("requests>=2.0,<3.0", PYPI_MIRROR)

        assert pkg is not None
        assert pkg.version == "2.31.0"

    @responses.activate
    def test_package_not_found_returns_none(self, qapp, tmp_db):
        responses.add(
            responses.GET,
            "https://pypi.org/pypi/nonexistent-pkg-xyz/json",
            json={"message": "Not Found"},
            status=404,
        )

        se = SearchEngine(tmp_db)
        pkg = se.get_package_details("nonexistent-pkg-xyz", PYPI_MIRROR)
        assert pkg is None

    @responses.activate
    def test_network_timeout_returns_none(self, qapp, tmp_db):
        import requests as req_lib
        responses.add(
            responses.GET,
            "https://pypi.org/pypi/requests/json",
            body=req_lib.exceptions.ConnectionError("timeout"),
        )

        se = SearchEngine(tmp_db)
        pkg = se.get_package_details("requests", PYPI_MIRROR)
        assert pkg is None

    @responses.activate
    def test_no_matching_versions_returns_none(self, qapp, tmp_db):
        body = {
            "info": {
                "name": "requests", "version": "2.31.0",
                "summary": "HTTP lib", "author": "A", "license": "MIT",
                "requires_dist": None,
            },
            "releases": {
                "2.31.0": [],
            },
        }
        responses.add(responses.GET, "https://pypi.org/pypi/requests/json", json=body, status=200)

        se = SearchEngine(tmp_db)
        pkg = se.get_package_details("requests>=99.0", PYPI_MIRROR)
        assert pkg is None


class TestSearchPackages:
    def test_search_returns_matches(self, qapp, tmp_db):
        se = SearchEngine(tmp_db)
        # Insert some data
        se.conn.execute(
            "INSERT INTO packages (name, normalized_name) VALUES (?, ?)",
            ("requests", "requests"),
        )
        se.conn.commit()

        results = se.search_packages("req")
        assert "requests" in results

    def test_search_empty_query_returns_empty(self, qapp, tmp_db):
        se = SearchEngine(tmp_db)
        results = se.search_packages("")
        # LIKE '%%' matches everything, but table is empty
        assert results == []

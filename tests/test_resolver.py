"""Resolver: PyPI fetch (mocked urllib), version specifiers, dep resolution."""

from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

from localpip.core import (
    HTTPClient,
    HTTPError as LpHTTPError,
    PackageInfo,
    Resolver,
    Target,
)


PYPI_MIRRORS = ["https://pypi.org/simple/"]


def _http(timeout=2):
    # short timeout so a real network mishit doesn't hang the test
    return HTTPClient(timeout=timeout, max_retries=1)


class TestGetPackageInfo:
    def test_successful_fetch(self, url_router, fake_response, pypi_json_response):
        body = pypi_json_response(name="requests", version="2.31.0", deps=["urllib3"])
        url_router({"https://pypi.org/pypi/requests/json": fake_response(body)})

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        pkg = r.get_package_info("requests")
        assert pkg is not None
        assert pkg.name == "requests"
        assert pkg.version == "2.31.0"
        assert pkg.summary == "A test package: requests"
        assert pkg.requires_dist == ["urllib3"]

    def test_version_specifier_picks_filtered_release(self, url_router, fake_response):
        body = {
            "info": {
                "name": "requests",
                "version": "2.31.0",
                "summary": "HTTP",
                "author": "A",
                "license": "MIT",
                "requires_dist": None,
            },
            "releases": {
                "2.28.0": [{"filename": "requests-2.28.0-py3-none-any.whl",
                            "url": "https://x/2.28.whl",
                            "packagetype": "bdist_wheel"}],
                "2.31.0": [{"filename": "requests-2.31.0-py3-none-any.whl",
                            "url": "https://x/2.31.whl",
                            "packagetype": "bdist_wheel"}],
                "3.0.0": [{"filename": "requests-3.0.0-py3-none-any.whl",
                           "url": "https://x/3.0.whl",
                           "packagetype": "bdist_wheel"}],
            },
        }
        url_router({"https://pypi.org/pypi/requests/json": fake_response(body)})

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        # 2.31.0 matches the spec and equals data['info']['version'] → no extra fetch
        pkg = r.get_package_info("requests>=2.0,<3.0")
        assert pkg is not None
        assert pkg.version == "2.31.0"

    def test_404_returns_none(self, url_router, fake_response):
        url_router({})  # no routes, anything 404s
        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        assert r.get_package_info("nonexistent-pkg-xyz") is None

    def test_falls_through_to_next_mirror(self, monkeypatch, url_router, fake_response,
                                           pypi_json_response):
        # First mirror 404s, second succeeds
        body = pypi_json_response(name="flask", version="3.0.0")
        url_router({
            "https://second.org/pypi/flask/json": fake_response(body),
        })
        r = Resolver(
            _http(),
            ["https://first.org/simple/", "https://second.org/simple/"],
            Target("3.11"),
        )
        pkg = r.get_package_info("flask")
        assert pkg is not None
        assert pkg.version == "3.0.0"

    def test_no_matching_versions_returns_none(self, url_router, fake_response):
        body = {
            "info": {
                "name": "requests",
                "version": "2.31.0",
                "summary": "",
                "author": "",
                "license": "",
                "requires_dist": None,
            },
            "releases": {"2.31.0": []},
        }
        url_router({"https://pypi.org/pypi/requests/json": fake_response(body)})

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        assert r.get_package_info("requests>=99.0") is None

    def test_invalid_requirement_returns_none(self):
        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        assert r.get_package_info("not a valid requirement!") is None


class TestResolveDeps:
    def test_resolves_root_and_marks_deps(self, url_router, fake_response,
                                            pypi_json_response):
        # `flask` depends on `werkzeug`
        flask_body = pypi_json_response(
            name="flask", version="3.0.0", deps=["werkzeug>=3.0"]
        )
        wz_body = pypi_json_response(name="werkzeug", version="3.0.1")
        url_router({
            "https://pypi.org/pypi/flask/json": fake_response(flask_body),
            "https://pypi.org/pypi/werkzeug/json": fake_response(wz_body),
        })

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        out = r.resolve(["flask"], include_deps=True)
        names = [(p.name.lower(), is_dep) for p, is_dep in out]
        assert ("flask", False) in names
        assert ("werkzeug", True) in names

    def test_no_deps_skips_dependency_traversal(self, url_router, fake_response,
                                                  pypi_json_response):
        flask_body = pypi_json_response(
            name="flask", version="3.0.0", deps=["werkzeug>=3.0"]
        )
        url_router({"https://pypi.org/pypi/flask/json": fake_response(flask_body)})

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        out = r.resolve(["flask"], include_deps=False)
        assert len(out) == 1
        assert out[0][0].name.lower() == "flask"

    def test_marker_eval_drops_inapplicable_deps(self, url_router, fake_response,
                                                   pypi_json_response):
        # win32-only dep should be dropped on linux target
        body = pypi_json_response(
            name="multitarget",
            version="1.0",
            deps=["pywin32; sys_platform == 'win32'"],
        )
        url_router({"https://pypi.org/pypi/multitarget/json": fake_response(body)})
        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11", "manylinux2014_x86_64"))
        out = r.resolve(["multitarget"], include_deps=True)
        assert len(out) == 1
        assert out[0][0].name.lower() == "multitarget"

    def test_event_callback_fires(self, url_router, fake_response, pypi_json_response):
        url_router({"https://pypi.org/pypi/flask/json":
                    fake_response(pypi_json_response(name="flask", version="3.0.0"))})

        events = []

        def on_event(event, **kw):
            events.append((event, kw))

        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        r.resolve(["flask"], include_deps=False, on_event=on_event)
        kinds = [e[0] for e in events]
        assert "resolving" in kinds
        assert "resolved" in kinds
        assert kinds[-1] == "done"

    def test_concurrent_resolve_fetches_level_in_parallel(
        self, url_router, fake_response, pypi_json_response
    ):
        # Both root-level packages and their two distinct deps should be fetched
        # in two BFS passes. Make sure we get all 4 in the result set.
        url_router({
            "https://pypi.org/pypi/a/json":
                fake_response(pypi_json_response(name="a", version="1.0",
                                                  deps=["c"])),
            "https://pypi.org/pypi/b/json":
                fake_response(pypi_json_response(name="b", version="1.0",
                                                  deps=["d"])),
            "https://pypi.org/pypi/c/json":
                fake_response(pypi_json_response(name="c", version="1.0")),
            "https://pypi.org/pypi/d/json":
                fake_response(pypi_json_response(name="d", version="1.0")),
        })
        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        out = r.resolve(["a", "b"], include_deps=True)
        names = {p.name.lower() for p, _ in out}
        assert names == {"a", "b", "c", "d"}
        roots = {p.name.lower() for p, is_dep in out if not is_dep}
        deps = {p.name.lower() for p, is_dep in out if is_dep}
        assert roots == {"a", "b"}
        assert deps == {"c", "d"}

    def test_dedupes_within_level(self, url_router, fake_response, pypi_json_response):
        # Both roots depend on same `shared` package — should fetch once, not twice
        url_router({
            "https://pypi.org/pypi/a/json":
                fake_response(pypi_json_response(name="a", version="1.0",
                                                  deps=["shared"])),
            "https://pypi.org/pypi/b/json":
                fake_response(pypi_json_response(name="b", version="1.0",
                                                  deps=["shared"])),
            "https://pypi.org/pypi/shared/json":
                fake_response(pypi_json_response(name="shared", version="1.0")),
        })
        r = Resolver(_http(), PYPI_MIRRORS, Target("3.11"))
        out = r.resolve(["a", "b"], include_deps=True)
        # 3 unique packages; shared should appear exactly once
        names = [p.name.lower() for p, _ in out]
        assert names.count("shared") == 1

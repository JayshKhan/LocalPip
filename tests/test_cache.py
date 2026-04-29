"""JsonCache + HTTPClient cache integration."""

from __future__ import annotations

import pytest

from localpip.core import HTTPClient, HTTPError, JsonCache


def test_cache_round_trip(tmp_path):
    cache = JsonCache(str(tmp_path))
    cache.put("https://x.com/api", {"hello": "world"}, etag='"abc"')
    got = cache.get("https://x.com/api")
    assert got is not None
    assert got["data"] == {"hello": "world"}
    assert got["etag"] == '"abc"'
    assert "fetched_at" in got


def test_cache_miss_returns_none(tmp_path):
    cache = JsonCache(str(tmp_path))
    assert cache.get("https://x.com/missing") is None


def test_cache_corrupted_file_returns_none(tmp_path):
    cache = JsonCache(str(tmp_path))
    path = cache._path("https://x.com/api")
    with open(path, "w") as f:
        f.write("{not valid json")
    assert cache.get("https://x.com/api") is None


class TestHTTPClientCacheIntegration:
    def test_first_fetch_populates_cache(self, tmp_path, url_router, fake_response):
        cache = JsonCache(str(tmp_path))
        url_router(
            {
                "https://x.com/api": fake_response(
                    {"v": 1}, headers={"ETag": '"v1"', "Last-Modified": "Mon"}
                ),
            }
        )
        client = HTTPClient(timeout=2, max_retries=1, cache=cache)
        data = client.get_json("https://x.com/api")
        assert data == {"v": 1}
        cached = cache.get("https://x.com/api")
        assert cached["data"] == {"v": 1}
        assert cached["etag"] == '"v1"'

    def test_304_returns_cached_data(self, tmp_path, monkeypatch):
        from urllib.error import HTTPError as UrlHTTPError

        cache = JsonCache(str(tmp_path))
        cache.put("https://x.com/api", {"v": 1}, etag='"v1"')

        def fake_urlopen(req, timeout=None):
            # Simulate the server replying 304 because we sent If-None-Match
            assert req.headers.get("If-none-match") == '"v1"'
            raise UrlHTTPError("https://x.com/api", 304, "Not Modified", {}, None)

        monkeypatch.setattr("localpip.core.urllib.request.urlopen", fake_urlopen)
        client = HTTPClient(timeout=2, max_retries=1, cache=cache)
        data = client.get_json("https://x.com/api")
        assert data == {"v": 1}

    def test_network_error_falls_back_to_stale_cache(self, tmp_path, monkeypatch):
        from urllib.error import URLError

        cache = JsonCache(str(tmp_path))
        cache.put("https://x.com/api", {"v": 1, "stale": True}, etag='"v1"')

        def fake_urlopen(req, timeout=None):
            raise URLError("connection refused")

        monkeypatch.setattr("localpip.core.urllib.request.urlopen", fake_urlopen)
        client = HTTPClient(timeout=1, max_retries=1, cache=cache)
        data = client.get_json("https://x.com/api")
        assert data == {"v": 1, "stale": True}


class TestPerHostRetryBudget:
    def test_dead_host_short_circuits_after_threshold(self, monkeypatch):
        from urllib.error import URLError

        client = HTTPClient(timeout=1, max_retries=1, host_failure_threshold=2)

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise URLError("down")

        monkeypatch.setattr("localpip.core.urllib.request.urlopen", fake_urlopen)
        # First two calls hit the network and fail
        for _ in range(2):
            with pytest.raises(HTTPError):
                client.get_json("https://dead.example.com/a")
        # Third call short-circuits — does NOT hit urlopen
        before = len(calls)
        with pytest.raises(HTTPError, match="marked dead"):
            client.get_json("https://dead.example.com/b")
        assert len(calls) == before  # no network call made

    def test_success_resets_failure_count(self, monkeypatch, fake_response):
        from urllib.error import URLError

        client = HTTPClient(timeout=1, max_retries=1, host_failure_threshold=2)

        state = {"fail": True}

        def fake_urlopen(req, timeout=None):
            if state["fail"]:
                raise URLError("down")
            return fake_response({"ok": True})

        monkeypatch.setattr("localpip.core.urllib.request.urlopen", fake_urlopen)
        with pytest.raises(HTTPError):
            client.get_json("https://example.com/a")
        # Success — should clear failure count
        state["fail"] = False
        client.get_json("https://example.com/a")
        # Now we can fail again without short-circuit
        state["fail"] = True
        with pytest.raises(HTTPError):
            client.get_json("https://example.com/a")

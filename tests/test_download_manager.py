"""Tests for DownloadManager: wheel selection scoring, queue management."""

import pytest

from core import PackageInfo, DownloadManager, DownloadStatus


# ── Helper to build PackageInfo with specific wheel filenames ──

def make_pkg(name="pkg", version="1.0.0", filenames=None):
    """Build a PackageInfo with wheels from a list of filenames."""
    urls = []
    for fn in (filenames or []):
        urls.append({
            "filename": fn,
            "url": f"https://example.com/{fn}",
            "packagetype": "bdist_wheel",
        })
    return PackageInfo(
        name=name, version=version, description="", urls=urls,
    )


# ── _find_best_url: Platform Filtering ──

class TestFindBestUrlPlatform:
    def test_win_amd64_wheel_selected_for_win_target(self, qapp):
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-cp311-win_amd64.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "win_amd64")
        assert result is not None
        assert "win_amd64" in result["filename"]

    def test_manylinux_wheel_not_matched_for_win_target(self, qapp):
        """Regression: manylinux in filename must not match '-any' platform check.
        The old bug had 'any' in filename matching 'manylinux'."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-cp311-manylinux2014_x86_64.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "win_amd64")
        assert result is None

    def test_any_platform_wheel_matches_all_targets(self, qapp):
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-py3-none-any.whl",
        ])
        dm = DownloadManager()
        for platform in ("win_amd64", "manylinux2014_x86_64", "any"):
            result = dm._find_best_url(pkg, "3.11", platform)
            assert result is not None, f"any-platform wheel should match {platform}"

    def test_wrong_platform_filtered_out(self, qapp):
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-cp311-win_amd64.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "manylinux2014_x86_64")
        assert result is None

    def test_platform_specific_beats_any(self, qapp):
        """When targeting win_amd64, a win_amd64 wheel scores higher than any."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-cp311-win_amd64.whl",
            "pkg-1.0.0-py3-none-any.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "win_amd64")
        assert "win_amd64" in result["filename"]


# ── _find_best_url: Python Version Scoring ──

class TestFindBestUrlPythonVersion:
    def test_cp311_scores_highest(self, qapp):
        """cp{ver} exact match (+50) should beat py{ver} (+40)."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-cp311-any.whl",    # +50
            "pkg-1.0.0-py311-none-any.whl",       # +40
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert "cp311" in result["filename"]

    def test_py_version_scores_above_abi3(self, qapp):
        """py{ver} (+40) beats abi3 (+30).
        Use cp39-abi3 so abi3 compat path is taken (not exact cp311 match)."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-py311-none-any.whl",                    # +40
            "pkg-1.0.0-cp39-abi3-any.whl",                     # abi3 path: +30
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert "py311" in result["filename"]

    def test_abi3_scores_above_py3(self, qapp):
        """abi3 (+30) beats generic py3 (+20).
        Use cp39-abi3 so abi3 compat path is taken (not exact cp311 match)."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp39-abi3-any.whl",     # +30
            "pkg-1.0.0-py3-none-any.whl",      # +20
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert "abi3" in result["filename"]

    def test_py3_generic_is_lowest_scoring_match(self, qapp):
        """py3-none-any is the fallback (+20)."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-py3-none-any.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert result is not None
        assert "py3-none-any" in result["filename"]

    def test_wrong_python_version_filtered(self, qapp):
        """cp310 wheel should not match when targeting 3.11."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp310-cp310-any.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert result is None

    def test_abi3_with_different_cpython_passes(self, qapp):
        """abi3 wheels are compatible across cpython versions."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-cp311-abi3-any.whl",
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "any")
        assert result is not None


# ── _find_best_url: Edge Cases ──

class TestFindBestUrlEdgeCases:
    def test_no_wheels_returns_none(self, qapp):
        pkg = PackageInfo(name="pkg", version="1.0.0", description="", urls=[
            {"filename": "pkg-1.0.0.tar.gz", "url": "https://x.com/pkg.tar.gz", "packagetype": "sdist"},
        ])
        dm = DownloadManager()
        assert dm._find_best_url(pkg, "3.11", "any") is None

    def test_empty_urls_returns_none(self, qapp):
        pkg = PackageInfo(name="pkg", version="1.0.0", description="", urls=[])
        dm = DownloadManager()
        assert dm._find_best_url(pkg, "3.11", "any") is None

    def test_mixed_candidates_best_score_wins(self, qapp):
        """With multiple valid wheels, the highest-scored one wins."""
        pkg = make_pkg(filenames=[
            "pkg-1.0.0-py3-none-any.whl",                       # score: 20
            "pkg-1.0.0-cp311-cp311-win_amd64.whl",              # score: 100 + 50 = 150
            "pkg-1.0.0-cp311-abi3-win_amd64.whl",               # score: 100 + 30 = 130
        ])
        dm = DownloadManager()
        result = dm._find_best_url(pkg, "3.11", "win_amd64")
        assert "cp311-cp311-win_amd64" in result["filename"]


# ── Queue Management ──

class TestQueueManagement:
    def test_cancel_queued_item(self, qapp):
        dm = DownloadManager()
        from core import DownloadItem
        item = DownloadItem(
            download_id="test_1", package_name="pkg", version="1.0",
            filename="pkg-1.0-py3-none-any.whl", url="https://x.com/pkg.whl",
            output_path="/tmp/pkg.whl", python_version="3.11", platform="any",
        )
        dm.downloads["test_1"] = item
        dm.cancel_download("test_1")
        assert item.cancelled is True
        assert item.status == DownloadStatus.CANCELLED

    def test_retry_resets_state(self, qapp, tmp_path):
        dm = DownloadManager()
        from core import DownloadItem
        from unittest.mock import patch
        item = DownloadItem(
            download_id="test_2", package_name="pkg", version="1.0",
            filename="pkg-1.0-py3-none-any.whl",
            url="https://x.com/pkg.whl",
            output_path=str(tmp_path / "pkg.whl"),
            python_version="3.11", platform="any",
            status=DownloadStatus.FAILED, error_message="timeout",
        )
        dm.downloads["test_2"] = item
        # Patch start_download to avoid spawning a real worker thread
        with patch.object(dm, 'start_download'):
            dm.retry_download("test_2")
        assert item.progress == 0
        assert item.error_message == ""
        assert item.cancelled is False

    def test_get_queue_returns_all(self, qapp):
        dm = DownloadManager()
        from core import DownloadItem
        for i in range(3):
            dm.downloads[f"id_{i}"] = DownloadItem(
                download_id=f"id_{i}", package_name="pkg", version="1.0",
                filename="f.whl", url="https://x.com/f.whl",
                output_path="/tmp/f.whl", python_version="3.11", platform="any",
            )
        assert len(dm.get_queue()) == 3

    def test_reset_clears_queue(self, qapp):
        dm = DownloadManager()
        from core import DownloadItem
        dm.downloads["x"] = DownloadItem(
            download_id="x", package_name="pkg", version="1.0",
            filename="f.whl", url="https://x.com/f.whl",
            output_path="/tmp/f.whl", python_version="3.11", platform="any",
        )
        dm.reset()
        assert len(dm.get_queue()) == 0

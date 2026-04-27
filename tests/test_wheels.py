"""select_wheel: PEP 425 / packaging.tags-based wheel selection."""

from __future__ import annotations

import pytest

from localpip.core import Target, compatible_tags, select_wheel


def make_files(*filenames):
    return [
        {
            "filename": fn,
            "url": f"https://example.com/{fn}",
            "packagetype": "bdist_wheel",
        }
        for fn in filenames
    ]


class TestPlatformMatching:
    def test_win_amd64_matches_win_target(self):
        files = make_files("pkg-1.0-cp311-cp311-win_amd64.whl")
        out = select_wheel(files, Target("3.11", "win_amd64"))
        assert out is not None
        assert "win_amd64" in out["filename"]

    def test_manylinux_does_not_match_win_target(self):
        """Regression: 'any' must not be a substring-match for 'manylinux'."""
        files = make_files("pkg-1.0-cp311-cp311-manylinux2014_x86_64.whl")
        assert select_wheel(files, Target("3.11", "win_amd64")) is None

    def test_any_wheel_matches_all_targets(self):
        files = make_files("pkg-1.0-py3-none-any.whl")
        for plat in ("win_amd64", "manylinux2014_x86_64", "any"):
            out = select_wheel(files, Target("3.11", plat))
            assert out is not None, f"any-platform wheel should match {plat}"

    def test_wrong_platform_filtered_out(self):
        files = make_files("pkg-1.0-cp311-cp311-win_amd64.whl")
        assert select_wheel(files, Target("3.11", "manylinux2014_x86_64")) is None

    def test_platform_specific_beats_any(self):
        files = make_files(
            "pkg-1.0-py3-none-any.whl",
            "pkg-1.0-cp311-cp311-win_amd64.whl",
        )
        out = select_wheel(files, Target("3.11", "win_amd64"))
        assert "win_amd64" in out["filename"]


class TestPythonVersionScoring:
    def test_cp_exact_beats_py_xy(self):
        files = make_files(
            "pkg-1.0-cp311-cp311-any.whl",
            "pkg-1.0-py311-none-any.whl",
        )
        out = select_wheel(files, Target("3.11", "any"))
        assert "cp311-cp311" in out["filename"]

    def test_abi3_compatible_with_newer_python(self):
        """An abi3 wheel built against cp39 is usable on cp311."""
        files = make_files("pkg-1.0-cp39-abi3-any.whl")
        out = select_wheel(files, Target("3.11", "any"))
        assert out is not None
        assert "abi3" in out["filename"]

    def test_cp_exact_beats_abi3_when_both_present(self):
        files = make_files(
            "pkg-1.0-cp39-abi3-any.whl",
            "pkg-1.0-cp311-cp311-any.whl",
        )
        out = select_wheel(files, Target("3.11", "any"))
        assert "cp311-cp311" in out["filename"]

    def test_wrong_cpython_excluded(self):
        files = make_files("pkg-1.0-cp310-cp310-any.whl")
        assert select_wheel(files, Target("3.11", "any")) is None

    def test_pure_py3_fallback(self):
        files = make_files("pkg-1.0-py3-none-any.whl")
        out = select_wheel(files, Target("3.11", "any"))
        assert out is not None


class TestEdgeCases:
    def test_no_wheels_returns_none(self):
        files = [
            {
                "filename": "pkg-1.0.tar.gz",
                "url": "https://x.com/pkg.tar.gz",
                "packagetype": "sdist",
            }
        ]
        assert select_wheel(files, Target("3.11", "any")) is None

    def test_empty_returns_none(self):
        assert select_wheel([], Target("3.11", "any")) is None

    def test_malformed_filename_skipped(self):
        files = [
            {
                "filename": "garbage.whl",
                "url": "https://x.com/g.whl",
                "packagetype": "bdist_wheel",
            },
            {
                "filename": "pkg-1.0-py3-none-any.whl",
                "url": "https://x.com/p.whl",
                "packagetype": "bdist_wheel",
            },
        ]
        out = select_wheel(files, Target("3.11", "any"))
        assert out is not None
        assert out["filename"] == "pkg-1.0-py3-none-any.whl"

    def test_manylinux_variant_compat(self):
        """A manylinux_2_17 wheel should match a manylinux2014 target (same ABI)."""
        files = make_files("pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl")
        out = select_wheel(files, Target("3.11", "manylinux2014_x86_64"))
        assert out is not None

    def test_higher_score_wins(self):
        files = make_files(
            "pkg-1.0-py3-none-any.whl",
            "pkg-1.0-cp311-cp311-win_amd64.whl",
            "pkg-1.0-cp311-abi3-win_amd64.whl",
        )
        out = select_wheel(files, Target("3.11", "win_amd64"))
        assert "cp311-cp311-win_amd64" in out["filename"]


class TestCompatibleTagsHelper:
    def test_returns_a_ranked_list(self):
        tags = compatible_tags(Target("3.11", "manylinux2014_x86_64"))
        assert len(tags) > 0
        # Most-specific tag should be cp311/cp311/manylinux2014_x86_64
        first = tags[0]
        assert first.interpreter == "cp311"
        assert first.abi == "cp311"
        assert "manylinux2014" in first.platform

    def test_any_platform_has_short_tag_list(self):
        tags = compatible_tags(Target("3.11", "any"))
        assert all(t.platform == "any" for t in tags)

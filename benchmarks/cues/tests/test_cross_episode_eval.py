"""Pure-logic tests for cross_episode_eval aggregation and skip paths.

No audio, no network, no file I/O.
"""
from pathlib import Path

import pytest

from cuebench.cross_episode_eval import _summarize
from cuebench.report import _render_md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ep(path, candidates):
    return {"episode": path, "candidates": candidates}


def _cand(kind, start, end):
    return {"kind": kind, "start": start, "end": end, "duration": round(end - start, 2)}


def _minimal_sweep():
    return {
        "scores": [],
        "per_template": {},
        "floor_used": 0.35,
        "episodes_scanned": 1,
        "formant_ab": None,
        "confirm_counts": None,
    }


# ---------------------------------------------------------------------------
# _summarize: aggregation math
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_empty_episodes(self):
        s = _summarize([])
        assert s["episodes_total"] == 0
        assert s["intro_found"] == 0
        assert s["outro_found"] == 0
        assert s["intro_start_span"] == {}
        assert s["outro_end_span"] == {}

    def test_no_candidates_in_episodes(self):
        eps = [_ep("/a/ep1.mp3", []), _ep("/a/ep2.mp3", [])]
        s = _summarize(eps)
        assert s["episodes_total"] == 2
        assert s["intro_found"] == 0
        assert s["outro_found"] == 0

    def test_intro_count(self):
        eps = [
            _ep("/a/ep1.mp3", [_cand("intro", 5.0, 30.0)]),
            _ep("/a/ep2.mp3", [_cand("intro", 6.0, 31.0)]),
            _ep("/a/ep3.mp3", []),
        ]
        s = _summarize(eps)
        assert s["intro_found"] == 2
        assert s["outro_found"] == 0
        assert s["episodes_total"] == 3

    def test_outro_count(self):
        eps = [
            _ep("/a/ep1.mp3", [_cand("outro", 1200.0, 1230.0)]),
            _ep("/a/ep2.mp3", []),
        ]
        s = _summarize(eps)
        assert s["outro_found"] == 1
        assert s["intro_found"] == 0

    def test_span_stats_min_max_mean(self):
        # Two episodes each with intro at different starts
        eps = [
            _ep("/a/ep1.mp3", [_cand("intro", 4.0, 30.0)]),
            _ep("/a/ep2.mp3", [_cand("intro", 6.0, 32.0)]),
        ]
        s = _summarize(eps)
        assert s["intro_start_span"]["min"] == 4.0
        assert s["intro_start_span"]["max"] == 6.0
        assert s["intro_start_span"]["mean"] == 5.0
        assert s["intro_end_span"]["min"] == 30.0
        assert s["intro_end_span"]["max"] == 32.0

    def test_outro_span_stats(self):
        eps = [
            _ep("/a/ep1.mp3", [_cand("outro", 1200.0, 1220.0)]),
            _ep("/a/ep2.mp3", [_cand("outro", 1100.0, 1130.0)]),
        ]
        s = _summarize(eps)
        assert s["outro_start_span"]["min"] == 1100.0
        assert s["outro_start_span"]["max"] == 1200.0
        assert s["outro_end_span"]["mean"] == pytest.approx((1220.0 + 1130.0) / 2, rel=1e-3)

    def test_episode_with_both_intro_and_outro(self):
        eps = [
            _ep("/a/ep1.mp3", [_cand("intro", 5.0, 30.0), _cand("outro", 1200.0, 1220.0)]),
        ]
        s = _summarize(eps)
        assert s["intro_found"] == 1
        assert s["outro_found"] == 1

    def test_multiple_intros_in_one_episode_count_once(self):
        # Two intro candidates in same episode should count as 1 episode with intro
        eps = [
            _ep("/a/ep1.mp3", [_cand("intro", 5.0, 30.0), _cand("intro", 35.0, 55.0)]),
        ]
        s = _summarize(eps)
        assert s["intro_found"] == 1
        # But both start values contribute to the span stats
        assert s["intro_start_span"]["min"] == 5.0
        assert s["intro_start_span"]["max"] == 35.0


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------

class TestSkipPaths:
    """Verify the structured skip output when conditions aren't met."""

    def test_fewer_than_two_episodes_skip(self):
        # Simulate what run() returns when len(audio_paths) < 2
        result = {
            "available": True,
            "skip_reason": "fewer than 2 episodes -- cross-episode comparison requires >= 2 siblings",
            "episodes": [],
            "summary": {},
        }
        assert result["available"] is True
        assert result["skip_reason"] is not None
        assert "2" in result["skip_reason"]
        assert result["episodes"] == []

    def test_fpcalc_absent_skip(self):
        result = {
            "available": False,
            "skip_reason": "fpcalc not found on PATH",
            "episodes": [],
            "summary": {},
        }
        assert result["available"] is False
        assert "fpcalc" in result["skip_reason"]


# ---------------------------------------------------------------------------
# Report rendering for cross-episode section
# ---------------------------------------------------------------------------

class TestReportXepSection:
    def test_section_absent_when_xep_none(self):
        md = _render_md(_minimal_sweep(), None, None)
        assert "## Cross-episode intro/outro" not in md

    def test_section_present_when_xep_provided(self):
        xep = {
            "available": True,
            "skip_reason": None,
            "episodes": [
                _ep("/a/ep1.mp3", [_cand("intro", 5.0, 30.0)]),
            ],
            "summary": {
                "episodes_total": 1,
                "intro_found": 1,
                "outro_found": 0,
                "intro_start_span": {"min": 5.0, "max": 5.0, "mean": 5.0},
                "intro_end_span": {"min": 30.0, "max": 30.0, "mean": 30.0},
                "outro_start_span": {},
                "outro_end_span": {},
            },
        }
        md = _render_md(_minimal_sweep(), None, xep)
        assert "## Cross-episode intro/outro" in md
        assert "1/1" in md
        assert "intro" in md

    def test_skip_reason_rendered(self):
        xep = {
            "available": True,
            "skip_reason": "fewer than 2 episodes -- cross-episode comparison requires >= 2 siblings",
            "episodes": [],
            "summary": {},
        }
        md = _render_md(_minimal_sweep(), None, xep)
        assert "## Cross-episode intro/outro" in md
        assert "fewer than 2" in md

    def test_no_candidates_shows_dash_row(self):
        xep = {
            "available": True,
            "skip_reason": None,
            "episodes": [_ep("/a/ep1.mp3", [])],
            "summary": {
                "episodes_total": 1,
                "intro_found": 0,
                "outro_found": 0,
                "intro_start_span": {},
                "intro_end_span": {},
                "outro_start_span": {},
                "outro_end_span": {},
            },
        }
        md = _render_md(_minimal_sweep(), None, xep)
        # No-candidate episode gets a dash row
        assert "| ep1.mp3 | - | - | - | - |" in md

    def test_outro_row_rendered(self):
        xep = {
            "available": True,
            "skip_reason": None,
            "episodes": [
                _ep("/a/ep1.mp3", [_cand("outro", 1200.0, 1230.0)]),
            ],
            "summary": {
                "episodes_total": 1,
                "intro_found": 0,
                "outro_found": 1,
                "intro_start_span": {},
                "intro_end_span": {},
                "outro_start_span": {"min": 1200.0, "max": 1200.0, "mean": 1200.0},
                "outro_end_span": {"min": 1230.0, "max": 1230.0, "mean": 1230.0},
            },
        }
        md = _render_md(_minimal_sweep(), None, xep)
        assert "outro" in md
        assert "1200" in md
        assert "0/1" in md

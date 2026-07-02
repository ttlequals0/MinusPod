"""Pure-logic tests for scan_eval position-based matching.

No audio, no network, no file I/O.
"""
from pathlib import Path

import pytest

from cuebench.scan_eval import _eval_episode, _threshold_for, _ground_truth_for_episode
from cuebench.report import _render_md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_info(
    scores=None,
    episodes=None,
    suggestion=None,
    duration_s=3.0,
    label="test-cue",
):
    """Build a minimal per_template info dict."""
    return {
        "label": label,
        "duration_s": duration_s,
        "scores": scores or [],
        "episodes": episodes or {},
        "suggestion": suggestion or {},
    }


def _make_occurrence(start, end, score=0.9):
    return {"start": start, "end": end, "score": score}


def _make_candidate(start, end):
    return {"start": start, "end": end}


# ---------------------------------------------------------------------------
# Threshold selection
# ---------------------------------------------------------------------------

class TestThresholdFor:
    def test_uses_suggested_when_high_confidence(self):
        info = _make_info(suggestion={"confidence": "high", "suggested": 0.82})
        assert _threshold_for(info) == 0.82

    def test_falls_back_when_low_confidence(self):
        from config import AUDIO_CUE_TEMPLATE_SCORE
        info = _make_info(suggestion={"confidence": "low", "suggested": 0.82})
        assert _threshold_for(info) == AUDIO_CUE_TEMPLATE_SCORE

    def test_falls_back_when_no_suggestion(self):
        from config import AUDIO_CUE_TEMPLATE_SCORE
        info = _make_info(suggestion={})
        assert _threshold_for(info) == AUDIO_CUE_TEMPLATE_SCORE

    def test_falls_back_when_suggested_is_none(self):
        from config import AUDIO_CUE_TEMPLATE_SCORE
        info = _make_info(suggestion={"confidence": "high", "suggested": None})
        assert _threshold_for(info) == AUDIO_CUE_TEMPLATE_SCORE


# ---------------------------------------------------------------------------
# Ground truth per episode
# ---------------------------------------------------------------------------

class TestGroundTruthForEpisode:
    def test_returns_only_matches_for_that_episode(self):
        ep1 = "/audio/ep1.mp3"
        ep2 = "/audio/ep2.mp3"
        info = _make_info(episodes={
            ep1: [_make_occurrence(5.0, 8.0, score=0.9)],
            ep2: [_make_occurrence(2.0, 5.0, score=0.9)],
        })
        result = _ground_truth_for_episode(Path(ep1), info, 0.75)
        assert len(result) == 1
        assert result[0]["start"] == 5.0

    def test_filters_below_threshold(self):
        ep = "/audio/ep.mp3"
        info = _make_info(episodes={
            ep: [
                _make_occurrence(5.0, 8.0, score=0.9),
                _make_occurrence(15.0, 18.0, score=0.5),
            ]
        })
        result = _ground_truth_for_episode(Path(ep), info, 0.75)
        assert len(result) == 1
        assert result[0]["start"] == 5.0

    def test_returns_empty_for_episode_with_no_matches(self):
        ep = "/audio/ep.mp3"
        info = _make_info(episodes={})
        result = _ground_truth_for_episode(Path(ep), info, 0.75)
        assert result == []

    def test_per_episode_count_is_independent(self):
        ep1 = "/audio/ep1.mp3"
        ep2 = "/audio/ep2.mp3"
        info = _make_info(episodes={
            ep1: [_make_occurrence(5.0, 8.0, score=0.9)],
            ep2: [
                _make_occurrence(2.0, 5.0, score=0.9),
                _make_occurrence(10.0, 13.0, score=0.85),
            ],
        })
        assert len(_ground_truth_for_episode(Path(ep1), info, 0.75)) == 1
        assert len(_ground_truth_for_episode(Path(ep2), info, 0.75)) == 2


# ---------------------------------------------------------------------------
# Midpoint-in-span matching
# ---------------------------------------------------------------------------

class TestEvalEpisodeMidpointMatching:
    """The key regression: duration-match alone is not enough."""

    def _run(self, candidates, occurrences, duration_s=3.0):
        ep = Path("/audio/ep.mp3")
        info = _make_info(
            duration_s=duration_s,
            episodes={str(ep): occurrences},
            suggestion={"confidence": "high", "suggested": 0.8},
        )
        result = _eval_episode(ep, candidates, {"t1": info}, {"t1": duration_s})
        return result["per_template"]["t1"]

    def test_hit_midpoint_inside_candidate(self):
        # occurrence at [5, 8] -> midpoint 6.5; candidate [4, 10] contains 6.5
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        cand = _make_candidate(4.0, 10.0)
        r = self._run([cand], [occ], duration_s=3.0)
        assert r["found"] is True
        assert r["rank"] == 1

    def test_miss_by_position_despite_correct_duration(self):
        # Old code bug: candidate is right duration (3s) but in wrong place.
        # occurrence midpoint 6.5 is NOT in candidate [20, 23].
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        cand = _make_candidate(20.0, 23.0)   # correct duration, wrong position
        r = self._run([cand], [occ], duration_s=3.0)
        assert r["found"] is False
        assert r["rank"] is None

    def test_rank_is_first_matching_candidate(self):
        # First candidate is wrong position, second is correct.
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        wrong_cand = _make_candidate(20.0, 23.0)
        right_cand = _make_candidate(4.0, 10.0)
        r = self._run([wrong_cand, right_cand], [occ], duration_s=3.0)
        assert r["found"] is True
        assert r["rank"] == 2

    def test_span_accuracy_uses_candidate_span_over_template_dur(self):
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        # candidate span=6s, template_dur=3s -> span_accuracy = 6/3 = 2.0
        cand = _make_candidate(4.0, 10.0)
        r = self._run([cand], [occ], duration_s=3.0)
        assert r["span_accuracy"] == pytest.approx(2.0, rel=1e-3)

    def test_span_accuracy_can_exceed_1(self):
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        cand = _make_candidate(3.0, 12.0)  # span=9, tpl=3 -> 3.0
        r = self._run([cand], [occ], duration_s=3.0)
        assert r["span_accuracy"] > 1.0

    def test_matched_occurrences_count(self):
        # Two occurrences both in the same candidate
        occ1 = _make_occurrence(5.0, 8.0, score=0.9)
        occ2 = _make_occurrence(10.0, 13.0, score=0.85)
        # candidate [0, 20] covers both midpoints (6.5 and 11.5)
        cand = _make_candidate(0.0, 20.0)
        r = self._run([cand], [occ1, occ2], duration_s=3.0)
        assert r["matched_occurrences"] == 2
        assert r["ground_truth_count"] == 2

    def test_no_candidates(self):
        occ = _make_occurrence(5.0, 8.0, score=0.9)
        r = self._run([], [occ], duration_s=3.0)
        assert r["found"] is False
        assert r["rank"] is None
        assert r["matched_occurrences"] == 0

    def test_no_occurrences(self):
        cand = _make_candidate(4.0, 10.0)
        r = self._run([cand], [], duration_s=3.0)
        assert r["found"] is False
        assert r["ground_truth_count"] == 0


# ---------------------------------------------------------------------------
# Report.md scan_eval section rendering
# ---------------------------------------------------------------------------

class TestReportScanEvalSection:
    def _minimal_sweep(self):
        return {
            "scores": [],
            "per_template": {},
            "floor_used": 0.35,
            "episodes_scanned": 1,
            "formant_ab": None,
            "confirm_counts": None,
        }

    def test_section_absent_when_no_scan(self):
        md = _render_md(self._minimal_sweep(), None)
        assert "## Discovery scan eval" not in md

    def test_section_present_when_scan_available(self):
        scan = {
            "available": True,
            "skip_reason": None,
            "results": [
                {
                    "episode": "/audio/ep1.mp3",
                    "per_template": {
                        "t1": {
                            "label": "intro",
                            "found": True,
                            "rank": 1,
                            "span_accuracy": 1.05,
                            "matched_occurrences": 1,
                            "ground_truth_count": 2,
                            "candidates_total": 5,
                        }
                    },
                }
            ],
        }
        md = _render_md(self._minimal_sweep(), scan)
        assert "## Discovery scan eval" in md
        assert "True" in md
        assert "1/2" in md

    def test_skip_reason_rendered_when_unavailable(self):
        scan = {
            "available": False,
            "skip_reason": "fpcalc not found on PATH",
            "results": [],
        }
        md = _render_md(self._minimal_sweep(), scan)
        assert "## Discovery scan eval" in md
        assert "fpcalc not found on PATH" in md

    def test_empty_results_list_renders_header_only(self):
        scan = {"available": True, "skip_reason": None, "results": []}
        md = _render_md(self._minimal_sweep(), scan)
        assert "## Discovery scan eval" in md

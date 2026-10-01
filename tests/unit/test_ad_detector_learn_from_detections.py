"""Tests for Gate B in AdDetector.learn_from_detections."""
from unittest.mock import MagicMock

import pytest

from ad_detector import AdDetector
from ad_validator import AdValidator
from utils.markers import mark_distinct_merge


@pytest.fixture
def detector():
    """AdDetector with mocked DB, text_pattern_matcher, sponsor_service, fingerprinter."""
    det = AdDetector(api_key="test-key")
    det.db = MagicMock()
    det.db.get_active_pattern_sponsors = MagicMock(return_value=set())
    det.db.get_setting_float = MagicMock(side_effect=lambda key, default: default)
    det.text_pattern_matcher = MagicMock()
    det.text_pattern_matcher.create_patterns_from_ad = MagicMock(return_value=[])
    det.sponsor_service = MagicMock()
    det.sponsor_service.get_sponsors = MagicMock(return_value=[])
    det.sponsor_service.find_sponsor_in_text = MagicMock(return_value=False)
    det.audio_fingerprinter = None
    return det


def _segments():
    return [
        {"start": 0, "end": 60, "text": "Xero is the accounting platform for small business."},
    ]


def _ad(sponsor, start=0.0, end=60.0):
    return {
        "sponsor": sponsor,
        "start": start,
        "end": end,
        "was_cut": True,
        "detection_stage": "claude",
        "confidence": 0.95,
    }


class TestGateBShortSponsor:

    def test_rejects_unknown_short_single_word(self, detector):
        detector.learn_from_detections(
            [_ad("Foobr")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_not_called()

    def test_passes_when_sponsor_in_registry(self, detector):
        # Real find_sponsor_in_text returns the canonical sponsor name or None.
        # The test ad uses a name not in KNOWN_SHORT_BRANDS so only Gate B
        # via the registry should let it through.
        detector.sponsor_service.find_sponsor_in_text.return_value = "Foobr"
        detector.learn_from_detections(
            [_ad("Foobr")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_passes_when_pattern_exists_for_sponsor(self, detector):
        detector.db.get_active_pattern_sponsors.return_value = {"pura"}
        detector.learn_from_detections(
            [_ad("Pura")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_passes_for_known_short_brand_seed(self, detector):
        detector.learn_from_detections(
            [_ad("Xero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_long_name_bypasses_gate_b_entirely(self, detector):
        detector.learn_from_detections(
            [_ad("LongerName")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_multi_word_short_name_bypasses_gate_b(self, detector):
        detector.learn_from_detections(
            [_ad("Ad Co")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        detector.text_pattern_matcher.create_patterns_from_ad.assert_called_once()

    def test_zero_alias_canonicalized_to_xero(self, detector):
        detector.learn_from_detections(
            [_ad("Zero")], _segments(), podcast_id="podA", episode_id="ep1"
        )
        call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
        assert call is not None
        assert call.kwargs["sponsor"] == "Xero"


def test_learning_skips_silent_remainder_cut_with_the_ad(detector):
    read = {"start": 100.0, "end": 160.0, "text": "brought to you by LongerName use promo code show"}
    silence = {"silence_spans": [{"start": 160.0, "end": 200.0, "duration": 40.0}]}
    ad = {"start": 100.0, "end": 160.0, "confidence": 0.95, "sponsor": "LongerName",
          "reason": "LongerName sponsor read", "detection_stage": "claude"}
    mark_distinct_merge(ad, {"start": 150.0, "end": 200.0, "confidence": 0.95,
                             "detection_stage": "text_pattern", "span_estimated": True,
                             "text_start": 150.0, "text_end": 160.0,
                             "has_estimated_pattern_member": True})
    ad["end"] = 200.0

    cut = AdValidator(3600.0, [read], splice_veto_enabled=False).validate(
        [ad], audio_analysis=silence).ads
    assert [(a["start"], a["end"]) for a in cut] == [(100.0, 200.0)]
    cut[0]["was_cut"] = True

    detector.learn_from_detections(cut, [read], podcast_id="podA", episode_id="ep1")

    call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
    assert (call.kwargs["start"], call.kwargs["end"]) == (100.0, 160.0)


def _dai_marker(members):
    return {"start": 731.1, "end": 939.9, "was_cut": True, "confidence": 0.9,
            "detection_stage": "dai_differential", "category": "sponsor",
            "reason": "Dynamically inserted: audio differs across fetches",
            "merged_distinct_ads": True, "merged_member_spans": members}


def _claude_member(confidence=0.97, sponsor="LongerName", start=731.1, end=937.8):
    return {"start": start, "end": end, "stage": "claude", "confidence": confidence,
            "sponsor": sponsor}


def test_claude_member_of_a_dai_marker_is_learned_on_its_own_span(detector, caplog):
    member = _claude_member()
    with caplog.at_level("INFO", logger="podcast.claude"):
        detector.learn_from_detections(
            [_dai_marker([member, {"start": 731.1, "end": 939.9, "stage": "dai_differential"}])],
            _segments(), podcast_id="podA", episode_id="ep1")
    call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
    assert call is not None
    assert (call.kwargs["start"], call.kwargs["end"]) == (731.1, 937.8)
    assert call.kwargs["sponsor"] == "LongerName"
    # The splitter sees the member candidate, not the merged marker.
    assert call.kwargs["ad"]["detection_stage"] == "claude"
    assert "Learning from claude member 731.1s-937.8s" in caplog.text


def test_member_span_is_clipped_to_the_marker_bounds(detector):
    marker = _dai_marker([_claude_member(start=700.0, end=950.0)])
    marker["end"] = 930.0  # a reviewer trim
    detector.learn_from_detections([marker], _segments(), podcast_id="podA", episode_id="ep1")
    call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
    assert (call.kwargs["start"], call.kwargs["end"]) == (731.1, 930.0)


@pytest.mark.parametrize("members", [
    [{"start": 731.1, "end": 939.9, "stage": "dai_differential"},
     {"start": 800.0, "end": 860.0, "stage": "fingerprint", "pattern_id": 3},
     {"start": 870.0, "end": 900.0, "stage": "text_pattern", "pattern_id": 4}],
    # A pattern already explains this audio: relearning would overwrite its fingerprint.
    [_claude_member(), {"start": 800.0, "end": 860.0, "stage": "fingerprint", "pattern_id": 3}],
    [_claude_member(), {"start": 900.0, "end": 930.0, "stage": "text_pattern"}],
])
def test_no_learning_without_an_unexplained_claude_member(detector, caplog, members):
    with caplog.at_level("DEBUG", logger="podcast.claude"):
        detector.learn_from_detections(
            [_dai_marker(members)], _segments(), podcast_id="podA", episode_id="ep1")
    detector.text_pattern_matcher.create_patterns_from_ad.assert_not_called()
    assert "Skipping pattern learning for dai_differential marker" in caplog.text


def test_a_claude_member_below_the_floor_is_not_learned(detector):
    detector.learn_from_detections(
        [_dai_marker([_claude_member(confidence=0.5)])], _segments(),
        podcast_id="podA", episode_id="ep1")
    detector.text_pattern_matcher.create_patterns_from_ad.assert_not_called()


def test_a_marker_without_members_logs_nothing(detector, caplog):
    marker = _dai_marker([])
    del marker["merged_member_spans"]
    with caplog.at_level("DEBUG", logger="podcast.claude"):
        detector.learn_from_detections([marker], _segments(), podcast_id="podA", episode_id="ep1")
    assert "Skipping pattern learning" not in caplog.text


def test_member_sponsor_is_not_taken_from_the_parent_reason(detector):
    detector.sponsor_service.find_sponsor_in_text.side_effect = (
        lambda text: "Acme Tools" if "Acme" in text else None)
    marker = _dai_marker([_claude_member(sponsor="NewBrand")])
    marker["reason"] = "Acme Tools sponsor read followed by another read"
    detector.learn_from_detections([marker], _segments(), podcast_id="podA", episode_id="ep1")
    call = detector.text_pattern_matcher.create_patterns_from_ad.call_args
    assert call.kwargs["sponsor"] == "NewBrand"


def test_member_candidate_carries_the_parent_dai_cores_clipped(detector):
    marker = _dai_marker([_claude_member()])
    marker["dai_core_spans"] = [{"start": 731.1, "end": 833.0}, {"start": 833.3, "end": 939.9}]
    marker["dai_probe_spans"] = [{"start": 731.6, "end": 735.6}]
    detector.learn_from_detections([marker], _segments(), podcast_id="podA", episode_id="ep1")
    candidate = detector.text_pattern_matcher.create_patterns_from_ad.call_args.kwargs["ad"]
    assert candidate["dai_core_spans"] == [{"start": 731.1, "end": 833.0},
                                           {"start": 833.3, "end": 937.8}]
    assert "merged_member_spans" not in candidate
    assert "reason" not in candidate


def test_member_candidates_still_pass_the_sponsor_gates(detector):
    detector.learn_from_detections(
        [_dai_marker([_claude_member(sponsor="Foobr")])], _segments(),
        podcast_id="podA", episode_id="ep1")
    detector.text_pattern_matcher.create_patterns_from_ad.assert_not_called()


@pytest.mark.parametrize("stage, learned", [("cue_pair", True), ("manual", True),
                                           ("fingerprint", False)])
def test_only_pattern_matches_explain_a_claude_member(detector, stage, learned):
    marker = _dai_marker([_claude_member(), {"start": 731.1, "end": 939.9, "stage": stage}])
    detector.learn_from_detections([marker], _segments(), podcast_id="podA", episode_id="ep1")
    assert detector.text_pattern_matcher.create_patterns_from_ad.called is learned

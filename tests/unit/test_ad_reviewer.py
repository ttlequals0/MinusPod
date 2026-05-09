"""Tests for the ad reviewer."""
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from ad_reviewer import (
    AdReviewer,
    RESURRECT_BAND_WIDTH,
    ReviewResult,
    ReviewVerdict,
    split_resurrection_pool,
)


def _mock_segments():
    return [
        {'start': 0.0, 'end': 60.0, 'text': 'show content'},
        {'start': 60.0, 'end': 120.0, 'text': 'before ad'},
        {'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'},
        {'start': 180.0, 'end': 240.0, 'text': 'after ad'},
        {'start': 240.0, 'end': 300.0, 'text': 'more show content'},
    ]


def _mock_episode_meta():
    return {
        'podcast_name': 'Test Podcast',
        'episode_title': 'Test Episode',
        'episode_description': 'desc',
        'podcast_description': 'pod desc',
        'slug': 'test-pod',
        'episode_id': 'ep1',
        'podcast_id': 'p1',
    }


def _build_reviewer(db_settings=None, conn=None):
    db_settings = db_settings or {}
    db = MagicMock()
    db.get_setting.side_effect = lambda key: db_settings.get(key)
    db.get_connection.return_value = conn or MagicMock()
    llm_client = MagicMock()
    return AdReviewer(db=db, llm_client=llm_client, sponsor_service=None)


@dataclass
class _LLMResp:
    """Matches the LLMResponse dataclass shape (content is a string)."""
    content: str
    model: str = "test-model"


def _resp(body: str) -> _LLMResp:
    return _LLMResp(content=body)


def test_clamp_to_cap_limits_shifts():
    """Adjust verdicts cannot move boundaries past the configured cap."""
    assert AdReviewer._clamp_to_cap(150.0, 100.0, 60) == 150.0  # within cap
    assert AdReviewer._clamp_to_cap(200.0, 100.0, 60) == 160.0  # capped up
    assert AdReviewer._clamp_to_cap(30.0, 100.0, 60) == 40.0  # capped down
    assert AdReviewer._clamp_to_cap(100.0, 100.0, 60) == 100.0  # no shift


# ---------- Pass 1 (accepted pool) ----------

def test_array_with_unchanged_boundaries_yields_confirmed():
    """One element back, start/end within tolerance of original -> confirmed."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'confirmed'
    assert len(result.accepted_after_review) == 1
    assert result.accepted_after_review[0]['start'] == 120.0
    assert result.accepted_after_review[0]['end'] == 180.0


def test_array_with_shifted_boundaries_yields_adjust():
    """One element back, start/end shifted within cap -> adjust, boundaries updated."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 115.0, "end": 185.0, "confidence": 0.88, '
        '"reason": "Adjusted to capture transition phrase"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    out = result.accepted_after_review[0]
    assert result.verdicts[0].verdict == 'adjust'
    assert out['start'] == 115.0
    assert out['end'] == 185.0
    assert out['reviewer_original_start'] == 120.0
    assert out['reviewer_original_end'] == 180.0


def test_array_with_shift_outside_cap_clamps():
    """Shifts beyond review_max_boundary_shift are clamped to the cap."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '30',
    })
    # Model proposes a 200s shift; cap is 30s.
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 320.0, "end": 380.0, "confidence": 0.85}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.85}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    out = result.accepted_after_review[0]
    assert result.verdicts[0].verdict == 'adjust'
    assert out['start'] == 150.0  # clamped: original_start + cap
    assert out['end'] == 210.0    # clamped: original_end + cap
    assert out['reviewer_original_start'] == 120.0
    assert out['reviewer_original_end'] == 180.0


def test_empty_array_yields_reject():
    """Empty array from accepted pool -> reject, ad removed from cut list."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp('[]')
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.85}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'reject'
    assert result.accepted_after_review == []
    assert len(result.rejected_by_reviewer) == 1
    assert result.rejected_by_reviewer[0]['source'] == 'reviewer'
    assert result.rejected_by_reviewer[0]['was_cut'] is False


# ---------- Resurrection pool ----------

def test_array_with_element_yields_resurrect():
    """One element back from resurrection pool -> resurrect, ad added to cut list."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.85, '
        '"reason": "Acast post-roll, validator was wrong"}]'
    )
    eligible = {'start': 120.0, 'end': 180.0, 'confidence': 0.7}
    result = reviewer.review(
        accepted_ads=[], resurrection_eligible=[eligible],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'resurrect'
    assert len(result.resurrected) == 1
    assert result.accepted_after_review[0]['was_cut'] is True
    assert result.accepted_after_review[0]['source'] == 'reviewer'


def test_empty_array_in_resurrection_pool_yields_reject():
    """Empty array from resurrection pool -> reject, ad stays out of cut list."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp('[]')
    eligible = {'start': 120.0, 'end': 180.0, 'confidence': 0.7}
    result = reviewer.review(
        accepted_ads=[], resurrection_eligible=[eligible],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'reject'
    assert result.resurrected == []
    assert result.accepted_after_review == []


# ---------- Failure / fall-through ----------

def test_unparseable_response_falls_through():
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        'this is not json at all'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.accepted_after_review == [ad]
    assert result.verdicts[0].verdict == 'failure'


def test_llm_call_failure_falls_through():
    """Per-ad LLM failure: ad stays unchanged, verdict logged as failure."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    with patch('ad_reviewer.call_llm_for_window', return_value=(None, RuntimeError('boom'))):
        ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
        result = reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=_mock_segments(), episode_meta=_mock_episode_meta(),
            pass_num=1, pass_model='claude-test',
        )

    assert result.accepted_after_review == [ad]  # unchanged
    assert result.verdicts[0].verdict == 'failure'
    assert result.verdicts[0].success is False


def test_per_ad_failure_does_not_block_other_ads():
    """One failing ad does not prevent the rest from being reviewed."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.side_effect = [
        _resp('not json'),  # ad 1 unparseable -> failure
        _resp('[{"start": 200.0, "end": 220.0, "confidence": 0.9}]'),  # ad 2 confirmed
    ]
    ads = [
        {'start': 100.0, 'end': 120.0, 'confidence': 0.9},
        {'start': 200.0, 'end': 220.0, 'confidence': 0.9},
    ]
    result = reviewer.review(
        accepted_ads=ads, resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert len(result.verdicts) == 2
    assert result.verdicts[0].verdict == 'failure'
    assert result.verdicts[1].verdict == 'confirmed'
    assert len(result.accepted_after_review) == 2


def test_inverted_boundaries_keep_original():
    """If the LLM returns end < start, fall back to original boundaries."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 200.0, "end": 100.0, "confidence": 0.5}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    # Treated as confirmed (within tolerance of original since we restored them)
    out = result.accepted_after_review[0]
    assert out['start'] == 120.0
    assert out['end'] == 180.0
    assert result.verdicts[0].verdict == 'confirmed'


def test_multi_element_array_takes_first():
    """Defensive: if the LLM returns multiple elements, take the first."""
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.95}, '
        '{"start": 999.0, "end": 9999.0, "confidence": 0.1}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    out = result.accepted_after_review[0]
    assert out['start'] == 120.0
    assert out['end'] == 180.0
    assert result.verdicts[0].verdict == 'confirmed'


def test_catastrophic_failure_returns_inputs_unchanged():
    reviewer = _build_reviewer({
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
    })
    ad = {'start': 100.0, 'end': 120.0, 'confidence': 0.9}
    with patch.object(reviewer, '_review_inner', side_effect=RuntimeError('catastrophic')):
        result = reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=_mock_segments(), episode_meta=_mock_episode_meta(),
            pass_num=1, pass_model='claude-test',
        )
    assert result.accepted_after_review == [ad]


# ---------- Resurrection pool selector ----------

def test_resurrection_pool_filters_by_band():
    min_cut = 0.80  # band [0.60, 0.80)
    all_ads = [
        # In cut list: skipped
        {'start': 10.0, 'end': 20.0, 'confidence': 0.95, 'validation': {}},
        # Below band: skipped
        {'start': 30.0, 'end': 40.0, 'confidence': 0.50, 'validation': {}},
        # In band, no disqualifying reasons: eligible
        {'start': 50.0, 'end': 60.0, 'confidence': 0.70, 'validation': {}},
        # In band but at threshold: NOT eligible (band is half-open at top)
        {'start': 70.0, 'end': 80.0, 'confidence': 0.80, 'validation': {}},
    ]
    cut_list = [all_ads[0]]
    eligible = split_resurrection_pool(all_ads, cut_list, min_cut)
    assert len(eligible) == 1
    assert eligible[0]['start'] == 50.0


def test_resurrection_pool_disqualifies_stacked_reasons():
    min_cut = 0.80
    all_ads = [
        # In band but with structural ERROR flag: disqualified
        {
            'start': 10.0, 'end': 20.0, 'confidence': 0.70,
            'validation': {'flags': ['ERROR: Very short (3.2s)']},
        },
        # In band, only confidence-related WARN: eligible
        {
            'start': 30.0, 'end': 40.0, 'confidence': 0.65,
            'validation': {'flags': ['WARN: Low confidence (0.65)']},
        },
        # In band but user marked as false positive: disqualified
        {
            'start': 50.0, 'end': 60.0, 'confidence': 0.70,
            'validation': {'flags': ['INFO: User marked as false positive']},
        },
    ]
    eligible = split_resurrection_pool(all_ads, [], min_cut)
    assert len(eligible) == 1
    assert eligible[0]['start'] == 30.0


def test_resurrection_pool_confidence_error_flag_does_not_disqualify():
    """ERROR: Very low confidence is the validator's confidence rejection -
    that's exactly what the reviewer wants to second-guess, so it must not
    disqualify."""
    min_cut = 0.80
    all_ads = [
        {
            'start': 10.0, 'end': 20.0, 'confidence': 0.65,
            'validation': {'flags': ['ERROR: Very low confidence (0.65)']},
        },
    ]
    eligible = split_resurrection_pool(all_ads, [], min_cut)
    assert len(eligible) == 1


def test_resurrection_band_width_is_20_points():
    """The band width matches the documented 20pp window."""
    assert RESURRECT_BAND_WIDTH == 0.20


def test_resurrection_pool_uses_validation_adjusted_confidence_when_present():
    """Validator may adjust confidence; reviewer band reads the adjusted value."""
    min_cut = 0.80
    all_ads = [
        {
            'start': 10.0, 'end': 20.0, 'confidence': 0.95,
            'validation': {'adjusted_confidence': 0.65},
        },
    ]
    eligible = split_resurrection_pool(all_ads, [], min_cut)
    assert len(eligible) == 1


def test_resurrection_band_dynamic_with_min_cut_confidence():
    """Resurrection band shifts with the user's min_cut_confidence slider."""
    all_ads = [{'start': 10.0, 'end': 20.0, 'confidence': 0.45, 'validation': {}}]
    # With min_cut=0.50, band is [0.30, 0.50): 0.45 is eligible
    assert len(split_resurrection_pool(all_ads, [], 0.50)) == 1
    # With min_cut=0.80, band is [0.60, 0.80): 0.45 is NOT eligible
    assert len(split_resurrection_pool(all_ads, [], 0.80)) == 0

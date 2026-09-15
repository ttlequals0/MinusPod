"""Reviewer trims on merged ads (issue #750).

A merged ad's recorded members decide what a proposal may drop: coarse
LLM/heuristic members carry padding the reviewer exists to trim, measured
members must stay fully covered.
"""
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from ad_reviewer import AdReviewer
from config import HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
from utils.markers import mark_distinct_merge


def _mock_segments():
    return [
        {'start': 0.0, 'end': 60.0, 'text': 'show content'},
        {'start': 60.0, 'end': 120.0, 'text': 'before ad'},
        {'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'},
        {'start': 180.0, 'end': 240.0, 'text': 'after ad'},
    ]


def _mock_episode_meta():
    return {
        'podcast_name': 'Test Podcast', 'episode_title': 'Test Episode',
        'episode_description': 'desc', 'podcast_description': 'pod desc',
        'slug': 'test-pod', 'episode_id': 'ep1', 'podcast_id': 'p1',
    }


@dataclass
class _LLMResp:
    content: str
    model: str = 'test-model'


def _build_reviewer(max_shift='60'):
    settings = {
        'review_prompt': 'review',
        'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': max_shift,
    }
    db = MagicMock()
    db.get_setting.side_effect = lambda key: settings.get(key)
    db.get_connection.return_value = MagicMock()
    return AdReviewer(db=db, llm_client=MagicMock(), sponsor_service=None)


def _review(reviewer, ad, proposal):
    reviewer._llm_client.messages_create.return_value = _LLMResp(
        f'[{{"start": {proposal[0]}, "end": {proposal[1]}, '
        f'"confidence": 0.9, "reason": "sponsor read"}}]'
    )
    return reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )


def _merged(start, end, members, **extra):
    ad = {
        'start': start, 'end': end, 'confidence': 0.9,
        'merged_distinct_ads': True,
        'merged_protected_start': min(m['start'] for m in members),
        'merged_protected_end': max(m['end'] for m in members),
        'merged_member_spans': members,
    }
    ad.update(extra)
    return ad


def test_trim_of_coarse_member_padding_is_applied():
    # First-pass LLM span padded on both edges around a text-pattern member
    # and a measured cross-fetch core. The proposal keeps both, so it cuts.
    ad = _merged(
        819.9, 1190.0,
        [{'start': 819.9, 'end': 1190.0, 'stage': 'first_pass'},
         {'start': 922.3, 'end': 977.7, 'stage': 'text_pattern'}],
        dai_core_spans=[{'start': 1010.9, 'end': 1120.0}],
    )
    # Above the 60s production default, which would clamp the 79.1s start move.
    reviewer = _build_reviewer(max_shift='120')

    result = _review(reviewer, ad, (899.0, 1129.6))

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert result.verdicts[0].verdict == 'adjust'
    assert accepted['start'] == pytest.approx(899.0)
    assert accepted['end'] == pytest.approx(1129.6)


def test_clamp_floor_still_restores_a_severed_measured_member():
    reviewer = _build_reviewer()
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 200.0, 'stage': 'claude'},
         {'start': 150.0, 'end': 190.0, 'stage': 'fingerprint'}],
    )

    bounds = reviewer._clamp_proposed_bounds(
        ad, 120.0, 170.0, 100.0, 200.0, 60.0, 'slug', 'ep')

    assert bounds == (120.0, 190.0)


def test_proposal_dropping_a_coarse_member_is_held():
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 130.0, 'stage': 'claude'},
         {'start': 160.0, 'end': 200.0, 'stage': 'verification'}],
    )
    reviewer = _build_reviewer()

    result = _review(reviewer, ad, (100.0, 135.0))

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
    assert (held['start'], held['end']) == (100.0, 200.0)
    assert (held['reviewer_proposed_start'],
            held['reviewer_proposed_end']) == (100.0, 135.0)


def test_trim_into_a_measured_member_is_held():
    ad = _merged(
        10152.0, 10379.8,
        [{'start': 10152.0, 'end': 10160.0, 'stage': 'fingerprint'},
         {'start': 10152.0, 'end': 10379.8, 'stage': 'first_pass'}],
    )
    reviewer = _build_reviewer()

    result = _review(reviewer, ad, (10159.6, 10379.8))

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
    assert (held['start'], held['end']) == (10152.0, 10379.8)


def test_proposal_outside_the_original_span_is_held():
    ad = _merged(
        7335.0, 7424.0,
        [{'start': 7335.0, 'end': 7400.0, 'stage': 'first_pass'},
         {'start': 7380.0, 'end': 7424.0, 'stage': 'verification'}],
    )
    reviewer = _build_reviewer()

    result = _review(reviewer, ad, (7315.0, 7335.0))

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT


def test_unknown_member_stage_stays_fully_protected():
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 200.0, 'stage': 'some_future_stage'}],
    )

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 110.0, 200.0, 100.0, 200.0) is True


def test_legacy_merged_marker_keeps_the_union_rule():
    # Persisted before member tracking: no merged_member_spans, so the
    # union of protected members is the only thing the reviewer knows.
    ad = {'start': 100.0, 'end': 200.0, 'merged_distinct_ads': True,
          'merged_protected_start': 100.0, 'merged_protected_end': 180.0}

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 110.0, 190.0, 100.0, 200.0) is True
    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 100.0, 190.0, 100.0, 200.0) is False


TRIM_REASON = (
    'The span from 100s to ~130.0s is the show content. The rest is a '
    'genuine ad break with a sponsor read; the opening portion is not an '
    'ad and the start should move to 130.0s.'
)


def test_recovered_trim_of_coarse_padding_is_applied():
    # The affirmed-confirm branch re-checks the same conflict rule on the
    # bounds recovered from prose.
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 200.0, 'stage': 'claude'}],
    )
    reviewer = _build_reviewer()
    reviewer._llm_client.messages_create.side_effect = [
        _LLMResp('[{"start": 100.0, "end": 200.0, "confidence": 0.9, '
                 f'"reason": "{TRIM_REASON}"}}]'),
        _LLMResp('{"ad_start": 130.0, "ad_end": 200.0}'),
    ]

    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (130.0, 200.0)
    assert accepted['reviewer_verdict'] == 'adjust'


def test_recovered_trim_severing_a_measured_member_is_held():
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 200.0, 'stage': 'claude'},
         {'start': 110.0, 'end': 150.0, 'stage': 'cue_pair'}],
    )
    reviewer = _build_reviewer()
    reviewer._llm_client.messages_create.side_effect = [
        _LLMResp('[{"start": 100.0, "end": 200.0, "confidence": 0.9, '
                 f'"reason": "{TRIM_REASON}"}}]'),
        _LLMResp('{"ad_start": 130.0, "ad_end": 200.0}'),
    ]

    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert (held['start'], held['end']) == (100.0, 200.0)
    assert (held['reviewer_proposed_start'],
            held['reviewer_proposed_end']) == (130.0, 200.0)


def test_near_total_drop_of_a_coarse_member_is_held():
    # A sliver of overlap is not evidence the member survived.
    ad = _merged(
        100.0, 200.0,
        [{'start': 100.0, 'end': 130.0, 'stage': 'claude'},
         {'start': 160.0, 'end': 200.0, 'stage': 'claude'}],
    )
    reviewer = _build_reviewer()

    result = _review(reviewer, ad, (100.0, 160.5))

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT


def test_short_coarse_member_stays_trimmable():
    # The retention floor cannot exceed half a member, or a short heuristic
    # span would become untrimmable.
    ad = _merged(
        0.0, 60.0,
        [{'start': 0.0, 'end': 8.0, 'stage': 'heuristic_preroll'},
         {'start': 8.0, 'end': 60.0, 'stage': 'claude'}],
    )
    reviewer = _build_reviewer()

    result = _review(reviewer, ad, (3.0, 60.0))

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (3.0, 60.0)


def _real_merge():
    """A merged ad whose member spans come from the merge primitive."""
    ad = {'start': 100.0, 'end': 130.0, 'confidence': 0.9,
          'detection_stage': 'claude'}
    mark_distinct_merge(ad, {'start': 160.0, 'end': 200.0, 'confidence': 0.9,
                             'detection_stage': 'text_pattern'})
    ad['end'] = 200.0
    return ad


def test_members_recorded_by_a_real_merge_are_honored():
    ad = _real_merge()
    assert ad['merged_member_spans'] == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 160.0, 'end': 200.0, 'stage': 'text_pattern'},
    ]

    trimmed = _review(_build_reviewer(), ad, (105.0, 200.0))
    accepted = trimmed.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (105.0, 200.0)

    severed = _review(_build_reviewer(), _real_merge(), (170.0, 200.0))
    assert severed.accepted_after_review == []
    assert severed.held_by_boundary_conflict[0]['start'] == 100.0

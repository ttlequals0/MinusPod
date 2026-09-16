"""Reviewer trims on merged ads (issue #750).

A merged ad's recorded members decide what a proposal may drop: coarse
LLM/heuristic members carry padding the reviewer exists to trim, measured
members must stay covered but for a few seconds of boundary disagreement.
"""
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_merged_member_trims_test_')

from ad_detector import AdDetector
from ad_reviewer import AdReviewer, _clamp_overrode
from config import HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
from ad_detector.boundaries import deduplicate_window_ads
from utils.markers import mark_distinct_merge, note_fold


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


def _run_review(reviewer, ad):
    return reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )


def _review(reviewer, ad, proposal, reason='sponsor read'):
    reviewer._llm_client.messages_create.return_value = _LLMResp(
        f'[{{"start": {proposal[0]}, "end": {proposal[1]}, '
        f'"confidence": 0.9, "reason": "{reason}"}}]'
    )
    return _run_review(reviewer, ad)


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


TAIL_TRIM_REASON = (
    'This is a genuine ad break with a sponsor read. The last couple of '
    'seconds are show content, so the end should move to 2404.0s.'
)


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

    result = _run_review(reviewer, ad)

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

    result = _run_review(reviewer, ad)

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


def _distinct_merged_ad():
    """A merged ad whose member spans come from the distinct-merge primitive."""
    ad = {'start': 100.0, 'end': 130.0, 'confidence': 0.9,
          'detection_stage': 'claude'}
    mark_distinct_merge(ad, {'start': 160.0, 'end': 200.0, 'confidence': 0.9,
                             'detection_stage': 'text_pattern'})
    ad['end'] = 200.0
    return ad


def test_members_recorded_by_a_real_merge_are_honored():
    ad = _distinct_merged_ad()
    assert ad['merged_member_spans'] == [
        {'start': 100.0, 'end': 130.0, 'stage': 'claude'},
        {'start': 160.0, 'end': 200.0, 'stage': 'text_pattern'},
    ]

    trimmed = _review(_build_reviewer(), ad, (105.0, 200.0))
    accepted = trimmed.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (105.0, 200.0)

    severed = _review(_build_reviewer(), _distinct_merged_ad(), (170.0, 200.0))
    assert severed.accepted_after_review == []
    assert severed.held_by_boundary_conflict[0]['start'] == 100.0


def _folded_estimate_ad():
    """The production shape: two claude members distinct-merged, then an
    estimated text_pattern folded in on a true overlap."""
    ad = {'start': 649.4, 'end': 700.0, 'confidence': 0.9,
          'detection_stage': 'claude'}
    mark_distinct_merge(ad, {'start': 710.0, 'end': 921.1, 'confidence': 0.9,
                             'detection_stage': 'claude'})
    ad['end'] = 921.1
    note_fold(ad, {'start': 831.75, 'end': 999.65, 'confidence': 0.85,
                   'detection_stage': 'text_pattern', 'span_estimated': True,
                   'text_start': 831.75, 'text_end': 860.0})
    ad['end'] = 999.65
    return ad


def test_estimated_text_pattern_tail_is_trimmed_not_held():
    # The text_pattern end is the pattern's average duration, not evidence.
    ad = _folded_estimate_ad()

    result = _review(_build_reviewer(), ad, (649.4, 943.8))

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (649.4, 943.8)


def _tail_trim_ad():
    return _merged(2211.3, 2406.5,
                   [{'start': 2211.3, 'end': 2406.5, 'stage': 'text_pattern'}])


def test_measured_member_trim_within_tolerance():
    ad = _tail_trim_ad()

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 2211.3, 2404.0, 2211.3, 2406.5) is False
    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 2211.3, 2400.0, 2211.3, 2406.5) is True


@pytest.mark.parametrize('members,original,proposal', [
    pytest.param([{'start': 100.0, 'end': 102.0, 'stage': 'fingerprint'},
                  {'start': 100.0, 'end': 300.0, 'stage': 'claude'}],
                 (100.0, 300.0), (102.5, 300.0), id='short_member'),
    pytest.param([{'start': 100.0, 'end': 110.0, 'stage': 'fingerprint'}],
                 (100.0, 110.0), (103.0, 107.0), id='both_edges'),
])
def test_measured_member_floor_holds(members, original, proposal):
    # The tolerance is half a member at most, or a 2s cue would be trimmable
    # out of existence, and it counts total intrusion rather than per edge:
    # 4s left of a 10s member is not that member any more.
    ad = _merged(original[0], original[1], members)

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, proposal[0], proposal[1], *original) is True


@pytest.mark.parametrize('stage,conflict', [
    pytest.param(None, True, id='legacy_union'),
    pytest.param('fingerprint', False, id='measured'),
])
def test_stage_none_member_keeps_the_strict_rule(stage, conflict):
    ad = _merged(100.0, 300.0, [{'start': 100.0, 'end': 300.0, 'stage': stage}])

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 102.0, 300.0, 100.0, 300.0) is conflict


def test_merged_distinct_flag_survives_a_duplicate_fold():
    # Without the flag the combined marker's members are never consulted.
    a = {'start': 100.0, 'end': 400.0, 'confidence': 0.9,
         'detection_stage': 'claude'}
    b = _merged(120.0, 380.0,
                [{'start': 200.0, 'end': 260.0, 'stage': 'fingerprint'}],
                confidence=0.8, detection_stage='claude')

    detector = AdDetector.__new__(AdDetector)
    combined = detector._merge_overlapping_accepted_duplicates([a, b])[0]

    assert combined['merged_distinct_ads'] is True
    assert AdReviewer._proposal_conflicts_with_protection(
        combined, 100.0, 150.0, 100.0, 400.0) is True


CONTRADICTION_TAIL_REASON = (
    'This span contains no advertisement content whatsoever; the read ends '
    'at 2404.0s and the tail must be trimmed off the end.'
)


def test_contradiction_hold_proposal_floors_to_a_measured_member():
    # A stamped proposal is one tap from a cut, so it obeys the same member
    # floor the boundary clamp applies.
    reviewer = _build_reviewer()
    reviewer._llm_client.messages_create.side_effect = [
        _LLMResp('[{"start": 2211.3, "end": 2406.5, "confidence": 0.9, '
                 f'"reason": "{CONTRADICTION_TAIL_REASON}"}}]'),
        _LLMResp('{"ad_start": 2211.3, "ad_end": 2404.0}'),
    ]

    result = _run_review(reviewer, _tail_trim_ad())

    held = result.held_by_contradiction[0]
    assert held['reviewer_proposed_start'] == 2211.3
    assert held['reviewer_proposed_end'] == 2406.5


def test_clamp_overrode_ignores_an_inverted_proposal():
    assert _clamp_overrode(2406.5, 2211.3, 2211.3, 2406.5,
                           2211.3, 2406.5) is False


def test_clamp_overrode_flags_a_moved_proposal_the_clamp_refused():
    assert _clamp_overrode(2211.3, 2404.0, 2211.3, 2406.5,
                           2211.3, 2406.5) is True


def test_clamped_away_proposal_confirms_without_a_recovery_call():
    reviewer = _build_reviewer()

    result = _review(reviewer, _tail_trim_ad(), (2211.3, 2404.0),
                     reason=TAIL_TRIM_REASON)

    assert reviewer._llm_client.messages_create.call_count == 1
    assert result.verdicts[0].verdict == 'confirmed'
    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (2211.3, 2406.5)
    assert not accepted.get('reviewer_moved')


def test_recovered_trim_clamped_away_accepts_the_ad_unchanged():
    reviewer = _build_reviewer()
    reviewer._llm_client.messages_create.side_effect = [
        _LLMResp('[{"start": 2211.3, "end": 2406.5, "confidence": 0.9, '
                 f'"reason": "{TAIL_TRIM_REASON}"}}]'),
        _LLMResp('{"ad_start": 2211.3, "ad_end": 2404.0}'),
    ]

    result = _run_review(reviewer, _tail_trim_ad())

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (2211.3, 2406.5)
    assert not accepted.get('reviewer_moved')


def test_inverted_proposal_still_reaches_prose_trim_recovery():
    # Garbage bounds are not a ruling on a trim: the clamp discarded them, so
    # the affirming prose still earns its recovery call.
    reviewer = _build_reviewer()
    reviewer._llm_client.messages_create.side_effect = [
        _LLMResp('[{"start": 2406.5, "end": 2211.3, "confidence": 0.9, '
                 f'"reason": "{TAIL_TRIM_REASON}"}}]'),
        _LLMResp('{"ad_start": 2211.3, "ad_end": 2404.0}'),
    ]

    result = _run_review(reviewer, _tail_trim_ad())

    assert reviewer._llm_client.messages_create.call_count == 2
    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    # The recovered 2404.0 end floors back at the measured member's edge.
    assert (accepted['start'], accepted['end']) == (2211.3, 2406.5)
    assert not accepted.get('reviewer_moved')


def _window_merged(second_stage):
    """Two window detections merged the way deduplicate_window_ads merges
    them: touching spans, so the merge records both as protected members."""
    ads = [
        {'start': 100.0, 'end': 200.0, 'confidence': 0.9,
         'detection_stage': 'claude', 'reason': 'Acme read'},
        {'start': 203.0, 'end': 260.0, 'confidence': 0.8,
         'detection_stage': second_stage, 'reason': 'Acme code'},
    ]
    return deduplicate_window_ads(ads)[0]


def test_llm_members_record_their_stage_through_window_dedup():
    merged = _window_merged('claude')

    assert [m['stage'] for m in merged['merged_member_spans']] == [
        'claude', 'claude']


def test_ten_second_trim_of_an_llm_member_merge_is_applied():
    merged = _window_merged('claude')
    reviewer = _build_reviewer()

    result = _review(reviewer, merged, (100.0, 250.0))

    assert result.held_by_boundary_conflict == []
    accepted = result.accepted_after_review[0]
    assert (accepted['start'], accepted['end']) == (100.0, 250.0)


def test_the_same_trim_of_a_measured_member_is_held():
    merged = _window_merged('fingerprint')
    reviewer = _build_reviewer()

    result = _review(reviewer, merged, (100.0, 250.0))

    assert result.accepted_after_review == []
    held = result.held_by_boundary_conflict[0]
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
    assert (held['start'], held['end']) == (100.0, 260.0)


def test_a_stageless_member_still_takes_the_legacy_union_rule():
    """Markers persisted before the stage stamp keep their full protection."""
    ad = _merged(
        100.0, 260.0,
        [{'start': 100.0, 'end': 200.0, 'stage': None},
         {'start': 203.0, 'end': 260.0, 'stage': None}],
    )

    assert AdReviewer._proposal_conflicts_with_protection(
        ad, 100.0, 250.0, 100.0, 260.0) is True

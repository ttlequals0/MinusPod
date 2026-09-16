"""Evidence floor on reviewer rejects (issue #750)."""
from dataclasses import dataclass
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_reject_floor_test_')

import main_app.processing as processing
from ad_reviewer import AdReviewer, ReviewVerdict, reject_hold_evidence
from config import HOLD_REASON_REVIEWER_REJECT_CONFLICT


@dataclass
class _LLMResp:
    content: str
    model: str = 'test-model'


def _segments():
    return [
        {'start': 0.0, 'end': 120.0, 'text': 'show content'},
        {'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'},
        {'start': 180.0, 'end': 240.0, 'text': 'after ad'},
    ]


def _episode_meta():
    return {
        'podcast_name': 'Test Podcast', 'episode_title': 'Test Episode',
        'episode_description': 'desc', 'podcast_description': 'pod desc',
        'slug': 'test-pod', 'episode_id': 'ep1', 'podcast_id': 'p1',
    }


def _reject(ad):
    """Run the reviewer over one ad with an empty-array (reject) response."""
    db = MagicMock()
    db.get_setting.side_effect = {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
    }.get
    db.get_connection.return_value = MagicMock()
    reviewer = AdReviewer(db=db, llm_client=MagicMock(), sponsor_service=None)
    reviewer._llm_client.messages_create.return_value = _LLMResp('[]')
    return reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_segments(), episode_meta=_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )


def _ad(**extra):
    return {'start': 120.0, 'end': 180.0, 'confidence': 0.9, **extra}


def test_llm_only_ad_is_still_rejected():
    result = _reject(_ad(detection_stage='claude'))

    assert result.accepted_after_review == []
    assert len(result.rejected_by_reviewer) == 1
    assert result.verdicts[0].reject_hold_reason is None


def test_reject_over_a_measured_stage_is_held():
    result = _reject(_ad(detection_stage='fingerprint'))

    assert result.rejected_by_reviewer == []
    assert (result.verdicts[0].reject_hold_reason
            == HOLD_REASON_REVIEWER_REJECT_CONFLICT)


def test_reject_over_a_measured_merge_member_is_held():
    ad = _ad(
        detection_stage='claude',
        merged_distinct_ads=True,
        merged_member_spans=[
            {'start': 120.0, 'end': 180.0, 'stage': 'claude'},
            {'start': 130.0, 'end': 150.0, 'stage': 'text_pattern'},
        ],
    )

    result = _reject(ad)

    assert reject_hold_evidence(ad) == 'text_pattern'
    assert (result.verdicts[0].reject_hold_reason
            == HOLD_REASON_REVIEWER_REJECT_CONFLICT)


def test_reject_over_a_confirmed_sponsor_is_held():
    ad = _ad(detection_stage='claude', validation={
        'decision': 'ACCEPT', 'sponsor_confirmed': True,
    })

    result = _reject(ad)

    assert reject_hold_evidence(ad) == 'sponsor_confirmed'
    assert (result.verdicts[0].reject_hold_reason
            == HOLD_REASON_REVIEWER_REJECT_CONFLICT)


def test_cue_snap_counts_as_evidence():
    assert reject_hold_evidence(
        _ad(detection_stage='claude', cue_snap={'start': True})) == 'cue_snap'


def test_the_apply_path_stamps_the_hold_on_the_master_marker():
    """The reviewer's own lists never reach storage; the verdict does."""
    marker = _ad(detection_stage='fingerprint', was_cut=True)
    verdict = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='reject',
        original_start=120.0, original_end=180.0,
        reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT,
    )

    processing._apply_reviewer_verdict_to_ad(marker, verdict)

    assert marker['was_cut'] is False
    assert marker['held_for_review'] is True
    assert marker['hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT


def test_a_plain_reject_leaves_no_hold_on_the_master_marker():
    marker = _ad(detection_stage='claude', was_cut=True)
    verdict = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='reject',
        original_start=120.0, original_end=180.0,
    )

    processing._apply_reviewer_verdict_to_ad(marker, verdict)

    assert marker['was_cut'] is False
    assert 'held_for_review' not in marker


def test_the_held_ad_lands_in_a_result_bucket():
    """It is in no cut, reject, or hold list otherwise: the run's own record of
    the ad disappears and only the verdict carries it."""
    result = _reject(_ad(detection_stage='fingerprint'))

    assert len(result.held_by_reject_evidence) == 1
    held = result.held_by_reject_evidence[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert result.accepted_after_review == []


def test_a_snap_between_review_and_merge_still_yields_a_held_marker():
    """The verdict carries the bounds the reviewer saw; a boundary snap moves
    the master marker a fraction of a second, and an exact-float key lookup
    then drops the hold on the floor."""
    marker = _ad(detection_stage='fingerprint', start=120.05, end=180.02,
                 was_cut=True)
    verdict = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='reject',
        original_start=120.0, original_end=180.0,
        reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT,
    )
    result = type('R', (), {'verdicts': [verdict], 'resurrected': []})()

    processing._merge_reviewer_result(result, [marker])

    assert marker['held_for_review'] is True
    assert marker['hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert marker['was_cut'] is False


def test_two_snapped_twins_take_one_verdict_each():
    """Both markers sit inside both verdicts' tolerance windows. Taking the
    first match stamped one marker twice and left the other unreviewed."""
    first = _ad(detection_stage='fingerprint', start=120.05, end=180.02,
                was_cut=True)
    second = _ad(detection_stage='fingerprint', start=120.40, end=180.30,
                 was_cut=True)
    verdicts = [
        ReviewVerdict(pool='accepted', pass_num=1, verdict='reject',
                      original_start=120.5, original_end=180.4,
                      reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT),
        ReviewVerdict(pool='accepted', pass_num=1, verdict='adjust',
                      original_start=120.0, original_end=180.0,
                      adjusted_start=130.0, adjusted_end=170.0),
    ]
    result = type('R', (), {'verdicts': verdicts, 'resurrected': []})()

    processing._merge_reviewer_result(result, [first, second])

    assert (second['held_for_review'], second['was_cut']) == (True, False)
    assert 'held_for_review' not in first
    assert (first['start'], first['end']) == (130.0, 170.0)


def test_a_tolerance_verdict_listed_first_leaves_the_exact_key_alone():
    """Verdict order must not decide ownership: the tolerance-only verdict is
    nearest the marker the exact-key verdict owns, and taking it first pushed
    the exact-key verdict onto the twin."""
    exact = _ad(detection_stage='fingerprint', start=120.0, end=180.0,
                was_cut=True)
    twin = _ad(detection_stage='fingerprint', start=120.45, end=180.45,
               was_cut=True)
    verdicts = [
        ReviewVerdict(pool='accepted', pass_num=1, verdict='reject',
                      original_start=120.1, original_end=180.1,
                      reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT),
        ReviewVerdict(pool='accepted', pass_num=1, verdict='adjust',
                      original_start=120.0, original_end=180.0,
                      adjusted_start=130.0, adjusted_end=170.0),
    ]
    result = type('R', (), {'verdicts': verdicts, 'resurrected': []})()

    processing._merge_reviewer_result(result, [exact, twin])

    assert (exact['start'], exact['end']) == (130.0, 170.0)
    assert 'held_for_review' not in exact
    assert (twin['held_for_review'], twin['was_cut']) == (True, False)


def test_a_marker_nowhere_near_the_verdict_is_not_touched():
    marker = _ad(detection_stage='fingerprint', start=600.0, end=660.0,
                 was_cut=True)
    verdict = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='reject',
        original_start=120.0, original_end=180.0,
        reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT,
    )
    result = type('R', (), {'verdicts': [verdict], 'resurrected': []})()

    processing._merge_reviewer_result(result, [marker])

    assert marker['was_cut'] is True
    assert 'held_for_review' not in marker


class TestCueSnapEvidenceNeedsASnappedEdge:
    """The differential cue-fusion marker lives under the same key but names
    no snapped edge, so a bare truthiness test made every fused span evidence
    and froze the reviewer on it."""

    def test_a_snapped_edge_is_evidence(self):
        assert reject_hold_evidence(
            _ad(detection_stage='claude', cue_snap={'end': True})) == 'cue_snap'

    def test_the_differential_fusion_marker_is_not(self):
        assert reject_hold_evidence(_ad(
            detection_stage='claude',
            cue_snap={'source': 'differential_cue_fusion'})) is None

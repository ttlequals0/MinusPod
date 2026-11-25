"""Pass 2 may release a reviewer-reject hold whose span the reviewer saw without words.

A reject over measured evidence is held (reviewer_reject_conflict). When the
transcript had no words in the span, the reject judged nothing; a confident
pass-2 re-detection on the re-transcribed cut audio is then the first read of
the speech and may approve the hold like the other evidence holds.
"""
from tests.app_bootstrap import bootstrap

bootstrap('pass2_textless_reject_release_')

from ad_reviewer import ReviewVerdict
from config import HOLD_REASON_REVIEWER_REJECT_CONFLICT
from main_app.pipeline import review, verify
from main_app.pipeline.verify import _gate_verification_ads_by_confidence
from tests.unit.pass2_test_utils import _approval_db, _user_corrections
from tests.unit.reviewer_test_utils import _mock_episode_meta, _resp, _reviewer


def _segments(with_ad_words):
    segments = [
        {'start': 0.0, 'end': 120.0, 'text': 'show content'},
        {'start': 180.0, 'end': 240.0, 'text': 'after the break'},
    ]
    if with_ad_words:
        segments.insert(1, {'start': 125.0, 'end': 175.0, 'text': 'a word from our sponsor'})
    return segments


def _measured_ad():
    return {'start': 120.0, 'end': 180.0, 'confidence': 0.95,
            'detection_stage': 'dai_differential',
            'cue_snap': {'start': {'cue_type': 'ad_break_boundary'}}}


def _reject(with_ad_words):
    reviewer = _reviewer()
    reviewer._llm_client.messages_create.return_value = _resp('[]')
    return reviewer.review(
        accepted_ads=[_measured_ad()], resurrection_eligible=[],
        segments=_segments(with_ad_words), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )


def test_reject_over_a_wordless_span_records_it():
    result = _reject(with_ad_words=False)

    [verdict] = result.verdicts
    assert verdict.reject_hold_reason == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert verdict.reject_saw_no_text is True
    [held] = result.held_by_reject_evidence
    assert held['reviewer_saw_no_text'] is True


def test_reject_over_spoken_words_records_nothing():
    result = _reject(with_ad_words=True)

    [verdict] = result.verdicts
    assert verdict.reject_hold_reason == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert verdict.reject_saw_no_text is False
    [held] = result.held_by_reject_evidence
    assert 'reviewer_saw_no_text' not in held


def test_the_apply_path_stamps_the_wordless_reject_on_the_marker():
    marker = dict(_measured_ad(), was_cut=True)
    verdict = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='reject',
        original_start=120.0, original_end=180.0,
        reject_hold_reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT,
        reject_saw_no_text=True,
    )

    review._apply_reviewer_verdict_to_ad(marker, verdict)

    assert marker['hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert marker['reviewer_saw_no_text'] is True


def _hold(saw_no_text):
    hold = {'start': 1155.7, 'end': 1180.4, 'held_for_review': True,
            'was_cut': False, 'hold_reason': HOLD_REASON_REVIEWER_REJECT_CONFLICT,
            'detection_stage': 'dai_differential'}
    if saw_no_text:
        hold['reviewer_saw_no_text'] = True
    return hold


def _pass2_ad(start, end, confidence=0.99):
    return {'start': start, 'end': end, 'confidence': confidence,
            'sponsor': 'Acme', 'reason': 'Acme sponsor read',
            'validation': {'decision': 'ACCEPT', 'adjusted_confidence': confidence}}


def _gate(hold):
    # Processed coordinates sit 20 s earlier (an earlier cut); the gate compares original ones.
    return _gate_verification_ads_by_confidence(
        [_pass2_ad(1135.8, 1158.3)], [_pass2_ad(1155.8, 1178.3)],
        min_cut_confidence=0.85, pass1_held_markers=[hold],
    )


def test_pass2_corroborates_a_wordless_reject_hold(monkeypatch):
    hold = _hold(saw_no_text=True)

    to_cut, for_ui, held, corroborated, _rel = _gate(hold)

    assert (to_cut, for_ui, held) == ([], [], [])
    assert corroborated == 1
    assert hold['pass2_corroborated'] is True
    assert hold['pass2_corroborated_span'] == {'start': 1155.8, 'end': 1178.3}

    db = _approval_db(monkeypatch)
    assert verify._file_corroborated_hold_approvals(
        's', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    assert db.create_pattern_correction.called


def test_pass2_leaves_a_reject_hold_the_reviewer_read_words_in(monkeypatch):
    hold = _hold(saw_no_text=False)

    _to_cut, _for_ui, _held, corroborated, _rel = _gate(hold)

    assert corroborated == 0
    assert not hold.get('pass2_corroborated')

    hold['pass2_corroborated'] = True
    _approval_db(monkeypatch)
    assert verify._file_corroborated_hold_approvals(
        's', 'e', [hold], corrections=_user_corrections('s', 'e')) == 0

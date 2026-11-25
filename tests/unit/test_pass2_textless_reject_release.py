"""Pass 2 may release a reviewer-reject hold whose span the reviewer saw without words.

A reject over measured evidence is held (reviewer_reject_conflict). When the
transcript had no words in the span, the reject judged nothing; a confident
pass-2 re-detection on the re-transcribed cut audio is then the first read of
the speech and may approve the hold like the other evidence holds.
"""
from tests.app_bootstrap import bootstrap

bootstrap('pass2_textless_reject_release_')

import json
from unittest.mock import MagicMock

from ad_reviewer import ReviewVerdict
from config import HOLD_REASON_REVIEWER_REJECT_CONFLICT, is_pending_review
from main_app import processing
from main_app.pipeline import recut, review, verify
from main_app.pipeline.verify import _gate_verification_ads_by_confidence
from tests.unit.pass2_test_utils import (
    _approval_db, _filed_confirms, _recut_validate, _release_confirm, _user_corrections)
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


def test_an_empty_transcript_does_not_count_as_wordless():
    reviewer = _reviewer()
    reviewer._llm_client.messages_create.return_value = _resp('[]')
    result = reviewer.review(
        accepted_ads=[_measured_ad()], resurrection_eligible=[],
        segments=[], episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )

    [verdict] = result.verdicts
    assert verdict.reject_hold_reason == HOLD_REASON_REVIEWER_REJECT_CONFLICT
    assert verdict.reject_saw_no_text is False
    [held] = result.held_by_reject_evidence
    assert 'reviewer_saw_no_text' not in held


def test_a_partial_finding_makes_a_wordless_reject_hold_a_release_candidate():
    hold = _hold(saw_no_text=True)

    *_rest, candidates = _gate_verification_ads_by_confidence(
        [_pass2_ad(1140.0, 1148.0)], [_pass2_ad(1160.0, 1168.0)],
        min_cut_confidence=0.85, pass1_held_markers=[hold],
    )

    assert [(sub['start'], sub['end'], h is hold) for sub, h in candidates] == [
        (1160.0, 1168.0, True)]


def _recut_db(monkeypatch, marker, confirms):
    db = MagicMock()
    db.get_episode.return_value = {'ad_markers_json': json.dumps([marker])}
    db.get_episode_corrections.return_value = []
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = confirms
    db.get_podcast_by_slug.return_value = {'id': 42}
    db.get_podcast_row.return_value = {'id': 42}
    db.get_podcast_cue_settings_overrides.return_value = {}
    db.get_episode_audio_analysis.return_value = None
    db.get_episode_dai_differential.return_value = None
    db.get_episode_upstream_transcript.return_value = None
    db.resolve_segment_actions.return_value = {}
    db.get_setting.return_value = None
    db.get_setting_bool.side_effect = lambda k, default=False, **kw: default
    db.get_setting_float.side_effect = lambda k, default=None: default
    monkeypatch.setattr(processing, 'db', db)


def _recut(monkeypatch, hold, filed):
    """Recut the stored hold with the confirm pass 2 filed, as the database returns it."""
    confirm = _release_confirm(
        (filed['original_bounds']['start'], filed['original_bounds']['end']),
        (filed['corrected_bounds']['start'], filed['corrected_bounds']['end']),
        reason=filed['source_hold_reason'])
    stored = {k: v for k, v in hold.items() if not k.startswith('_')}
    _recut_db(monkeypatch, stored, [confirm])
    ads_to_remove, all_ads, _keep, _rejects = recut._build_recut_ad_list(
        'slug', 'ep', [], 3000.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    return ads_to_remove, all_ads


def test_recut_cuts_a_wordless_reject_hold_pass2_approved(monkeypatch):
    hold = dict(_hold(saw_no_text=True), confidence=0.95, reason='sponsor spot')
    _gate(hold)
    [filed] = _filed_confirms(monkeypatch, [hold])
    assert filed['source_hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT

    ads_to_remove, all_ads = _recut(monkeypatch, hold, filed)

    assert [(a['start'], a['end']) for a in ads_to_remove] == [(1155.8, 1178.3)]
    assert not any(is_pending_review(a) and a['start'] >= 1155.8 and a['end'] <= 1178.3
                   for a in all_ads)


def test_recut_keeps_a_worded_reject_hold_despite_an_auto_confirm(monkeypatch):
    hold = dict(_hold(saw_no_text=True), confidence=0.95, reason='sponsor spot')
    _gate(hold)
    [filed] = _filed_confirms(monkeypatch, [hold])
    del hold['reviewer_saw_no_text']

    ads_to_remove, all_ads = _recut(monkeypatch, hold, filed)

    assert ads_to_remove == []
    [after] = all_ads
    assert is_pending_review(after)
    assert after['hold_reason'] == HOLD_REASON_REVIEWER_REJECT_CONFLICT


def test_held_remainders_do_not_inherit_the_wordless_flag():
    hold = dict(_hold(saw_no_text=True), start=1000.0, end=1100.0, confidence=0.95,
                reason='sponsor spot', pass2_corroborated=True,
                pass2_corroborated_span={'start': 1040.0, 'end': 1060.0})
    confirm = _release_confirm((1000.0, 1100.0), (1040.0, 1060.0),
                               reason=HOLD_REASON_REVIEWER_REJECT_CONFLICT)

    ads = _recut_validate([hold], [confirm])

    rests = [a for a in ads if a.get('pass2_hold_remainder')]
    assert rests
    assert all('reviewer_saw_no_text' not in a for a in rests)

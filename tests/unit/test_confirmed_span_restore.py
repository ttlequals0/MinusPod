"""Saved user-confirmed intervals are cut even when no detection survives."""
from dataclasses import dataclass
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('confirmed_span_restore_test_')

from ad_validator import restore_uncovered_confirmed_spans, user_trimmed_keep_ranges
from config import is_pending_review
from main_app import processing
from tests.unit.test_keep_bypass import _run_pipeline
from tests.unit.test_processing_boundary_safety import InconclusiveError, _reviewer
from tests.unit.test_segment_rerender import ALL_REMOVE, _run_recut

# 600 s keeps the validator's end-of-episode extension away from the fixtures.
DURATION = 600.0
SEGMENTS = [
    {'start': 0.0, 'end': 120.0, 'text': 'Opening discussion.'},
    {'start': 120.0, 'end': 160.0, 'text': 'A message from Acme.'},
    {'start': 160.0, 'end': 200.0, 'text': 'Discussion continues.'},
    {'start': 200.0, 'end': 300.0, 'text': 'More discussion follows.'},
]
CONFIRM = {'start': 120.0, 'end': 160.0, 'correction_type': 'confirm'}


@dataclass
class _LLMResp:
    content: str
    model: str = 'test-model'


def _candidate(start, end, confidence=0.98):
    return {'start': start, 'end': end, 'confidence': confidence,
            'detection_stage': 'claude', 'sponsor': 'Acme',
            'reason': 'Acme sponsor read', 'category': 'sponsor'}


def _run(monkeypatch, candidates, confirmed=(CONFIRM,), review_body='[]',
         review_error=None, false_positives=None):
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: _reviewer())
    monkeypatch.setattr(
        'ad_reviewer.call_llm_for_window',
        lambda **kwargs: ((None, review_error) if review_error
                          else (_LLMResp(review_body), None)))
    with patch.object(processing.ad_detector, 'learn_from_detections',
                      return_value=0) as learning:
        run = _run_pipeline(
            candidates, {'sponsor': 'remove'}, segments=SEGMENTS,
            real_refine_reviewer=True, duration=DURATION,
            confirmed_corrections=[dict(c) for c in confirmed],
            false_positive_corrections=false_positives)
    assert run['result'] is True
    run['learning'] = learning
    cuts = run['local_ap'].process_episode.call_args.args[1]
    return run, sorted((c['start'], c['end']) for c in cuts)


def _saved(run):
    return run['storage'].save_combined_ads.call_args_list[-1].args[2]


def _restored(markers):
    return [m for m in markers if m.get('detection_stage') == 'manual']


def test_no_candidate_restores_confirmed_interval(monkeypatch):
    run, cuts = _run(monkeypatch, [])
    assert cuts == [(120.0, 160.0)]
    restored = _restored(run['local_ap'].process_episode.call_args.args[1])
    assert restored[0]['validation']['user_confirmed'] is True
    run['learning'].assert_not_called()


def test_confirmed_candidate_rejected_by_reviewer_still_cuts(monkeypatch):
    _, cuts = _run(monkeypatch, [_candidate(120.0, 160.0)])
    assert cuts == [(120.0, 160.0)]


def test_trimmed_confirm_restores_only_approved_span(monkeypatch):
    trimmed = dict(CONFIRM, confirmed_span={'start': 125.0, 'end': 155.0})
    _, cuts = _run(monkeypatch, [], confirmed=[trimmed])
    assert cuts == [(125.0, 155.0)]


def test_wide_candidate_rejected_by_reviewer_restores_interval(monkeypatch):
    run, cuts = _run(monkeypatch, [_candidate(110.0, 195.0)])
    assert cuts == [(120.0, 160.0)]
    run['learning'].assert_not_called()


def test_reviewer_trim_restores_uncovered_remainder(monkeypatch):
    body = '[{"start": 150.0, "end": 195.0, "is_ad": true, "confidence": 0.95}]'
    run, cuts = _run(monkeypatch, [_candidate(110.0, 195.0)], review_body=body)
    assert cuts == [(120.0, 150.0), (150.0, 195.0)]
    assert [(m['start'], m['end']) for m in _restored(_saved(run))] == [(120.0, 150.0)]


def test_accepted_candidate_adds_no_manual_marker(monkeypatch):
    body = '[{"start": 120.0, "end": 160.0, "is_ad": true, "confidence": 0.95}]'
    run, cuts = _run(monkeypatch, [_candidate(120.0, 160.0)], review_body=body)
    assert cuts == [(120.0, 160.0)]
    assert _restored(_saved(run)) == []


def test_partial_candidate_is_not_widened(monkeypatch):
    # The validator splits the candidate at the confirm start; the reviewer sees only 110-120.
    body = '[{"start": 110.0, "end": 120.0, "is_ad": true, "confidence": 0.95}]'
    run, cuts = _run(monkeypatch, [_candidate(110.0, 140.0)], review_body=body)
    assert cuts == [(110.0, 120.0), (120.0, 140.0), (140.0, 160.0)]
    saved = _saved(run)
    assert [(m['start'], m['end']) for m in _restored(saved)] == [(140.0, 160.0)]
    assert all(m['end'] <= 140.0 for m in saved if m.get('detection_stage') == 'claude')
    assert not any(m['start'] < 120.0 and m['end'] > 140.0 for m in saved)


def test_validator_rejected_wide_candidate_restores_once(monkeypatch):
    run, cuts = _run(monkeypatch, [_candidate(105.0, 195.0, confidence=0.2)])
    assert cuts == [(120.0, 160.0)]
    assert len(_restored(_saved(run))) == 1


def test_false_positive_wins(monkeypatch):
    fp = [{'start': 120.0, 'end': 160.0}]
    _, cuts = _run(monkeypatch, [], false_positives=fp)
    assert cuts == []


def test_partial_false_positive_is_excluded(monkeypatch):
    fp = [{'start': 150.0, 'end': 160.0}]
    _, cuts = _run(monkeypatch, [], false_positives=fp)
    assert cuts == [(120.0, 150.0)]


def test_saved_trim_keep_range_wins(monkeypatch):
    # A newer trimmed confirm keeps 40-60 in the audio.
    trimmed = {'start': 140.0, 'end': 170.0, 'correction_type': 'confirm',
               'confirmed_span': {'start': 160.0, 'end': 170.0}}
    _, cuts = _run(monkeypatch, [], confirmed=[trimmed, CONFIRM])
    assert cuts == [(120.0, 140.0), (160.0, 170.0)]


def test_held_marker_is_carved_around_restored_piece(monkeypatch):
    run, cuts = _run(monkeypatch, [_candidate(110.0, 195.0)],
                     review_error=InconclusiveError())
    assert cuts == [(120.0, 160.0)]
    held = [m for m in _saved(run) if is_pending_review(m)]
    assert sorted((m['start'], m['end']) for m in held) == [(110.0, 120.0), (160.0, 195.0)]


def test_recut_restores_confirmed_interval():
    rejected = {'start': 10.0, 'end': 50.0, 'was_cut': False, 'category': 'sponsor',
                'detection_stage': 'claude', 'validation': {'decision': 'REJECT'}}
    confirm = {'start': 30.0, 'end': 50.0, 'correction_type': 'confirm'}
    cuts, saved = _run_recut([], [rejected], ALL_REMOVE, confirmed_corrections=[confirm])
    assert [(c['start'], c['end']) for c in cuts] == [(30.0, 50.0)]
    assert [(m['start'], m['end']) for m in _restored(saved)] == [(30.0, 50.0)]


def _ad(start, end, **extra):
    return dict({'start': start, 'end': end, 'was_cut': True}, **extra)


def _restore(cuts, markers, confirmed, fps=(), duration=DURATION):
    return restore_uncovered_confirmed_spans(
        cuts, markers, list(confirmed), list(fps),
        user_trimmed_keep_ranges(list(confirmed)), duration)


def test_helper_fills_only_uncovered_pieces():
    cut = _ad(130.0, 140.0)
    markers = [cut]
    result = _restore([cut], markers, [CONFIRM])
    assert [(a['start'], a['end']) for a in result] == [
        (120.0, 130.0), (130.0, 140.0), (140.0, 160.0)]
    assert cut['start'] == 130.0 and cut['end'] == 140.0
    assert len(markers) == 3
    restored = [a for a in result if a is not cut]
    assert all(a['_skip_pattern_learning'] and a['was_cut'] for a in restored)
    assert all(a['validation']['confirmed_span'] == {'start': a['start'], 'end': a['end']}
               for a in restored)


def test_helper_skips_slivers_and_clamps_to_duration():
    cuts = [_ad(120.5, 159.7)]
    assert _restore(cuts, list(cuts), [CONFIRM]) == cuts
    result = _restore([], [], [CONFIRM], duration=145.0)
    assert [(a['start'], a['end']) for a in result] == [(120.0, 145.0)]


def test_helper_ignores_adjustments_and_auto_filed_confirms():
    adjust = dict(CONFIRM, correction_type='boundary_adjustment',
                  confirmed_span={'start': 120.0, 'end': 160.0})
    auto = dict(CONFIRM, auto_filed=True)
    assert _restore([], [], [adjust, auto]) == []


def test_helper_promotes_rejected_marker_with_same_bounds():
    rejected = _ad(120.0, 160.0, was_cut=False, detection_stage='claude',
                   validation={'decision': 'REJECT'})
    markers = [rejected]
    result = _restore([], markers, [CONFIRM])
    assert result == [rejected] and markers == [rejected]
    assert rejected['was_cut'] is True
    assert rejected['validation']['user_confirmed'] is True
    assert rejected['_skip_pattern_learning'] is True


def test_helper_keeps_keep_action_markers():
    kept = _ad(130.0, 140.0, was_cut=False, action_applied='keep')
    result = _restore([], [kept], [CONFIRM])
    assert [(a['start'], a['end']) for a in result] == [(120.0, 130.0), (140.0, 160.0)]


def test_helper_false_positive_majority_skips_confirm():
    assert _restore([], [], [CONFIRM], fps=[{'start': 120.0, 'end': 145.0}]) == []

"""Pass-2 findings that overlap a pass-1 hold keep their outside parts as candidates."""
import copy
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('pass2_hold_split_test_')

from ad_reviewer import ReviewResult
from ad_validator import AdValidator, Decision
from audio_processor import AudioProcessor, get_replacement_duration
from config import is_pending_review
from main_app import processing
from main_app.verification_reconciliation import (
    Pass2Ledger, _gate_hold_split_fragments, _gate_verification_ads_by_confidence,
    _split_pass2_candidates_around_holds, _split_pass2_candidates_around_spans,
)
from tests.unit.pass2_test_utils import (
    NO_SPLICE, _ad, _approval_db, _ctx, _hold, _pair, _recut_validate, _release_confirm,
    _spans, _user_corrections, _verdict, drive_verification_pass,
)
from utils.time import adjust_timestamp, overlap_seconds

INCONCLUSIVE = 'reviewer_inconclusive_bounds'


def _gate(pairs, holds, **kwargs):
    return _gate_verification_ads_by_confidence(
        [p for p, _ in pairs], [o for _, o in pairs], min_cut_confidence=0.8,
        pass1_held_markers=holds, **kwargs)


# ---------- Gate: the hold decision is unchanged, the parent is collected ----------

def _expect(cut=(), n=0, corroborated=(), candidates=()):
    return {'cut': list(cut), 'n': n, 'corroborated': list(corroborated),
            'candidates': list(candidates)}


# Expected decisions as the gate made them before findings were split around holds.
GATE_SHAPES = [
    ([(2104.57, 2198.87, 0.98)], [(2124.95, 2198.87, NO_SPLICE)],
     _expect(n=1, corroborated=[(2124.95, 2198.87, {'start': 2124.95, 'end': 2198.87})])),
    ([(1825.7, 2001.4, 0.94)], [(1963.37, 2012.88, INCONCLUSIVE)],
     _expect(candidates=[(1963.37, 2001.4, 1963.37)])),
    ([(900.0, 1110.0, 0.95)], [(1000.0, 1100.0, NO_SPLICE)],
     _expect(candidates=[(1000.0, 1100.0, 1000.0)])),
    ([(1040.0, 1300.0, 0.95)], [(1000.0, 1100.0, NO_SPLICE)],
     _expect(candidates=[(1040.0, 1100.0, 1000.0)])),
    ([(990.0, 1210.0, 0.95)], [(1000.0, 1100.0, NO_SPLICE), (1110.0, 1200.0, NO_SPLICE)],
     _expect(candidates=[(1000.0, 1100.0, 1000.0), (1110.0, 1200.0, 1110.0)])),
    # Intentional change: every disjoint subspan is a candidate, not only the first.
    ([(1040.0, 1060.0, 0.95), (1080.0, 1140.0, 0.95)], [(1000.0, 1100.0, INCONCLUSIVE)],
     _expect(candidates=[(1040.0, 1060.0, 1000.0), (1080.0, 1100.0, 1000.0)])),
    ([(950.0, 1100.0, 0.7)], [(1000.0, 1100.0, NO_SPLICE)], _expect()),
    ([(1000.0, 1100.0, 0.95)], [(1000.0, 1100.0, 'estimated_pattern_bounds')],
     _expect(n=1, corroborated=[(1000.0, 1100.0, {'start': 1000.0, 'end': 1100.0})])),
    ([(500.0, 560.0, 0.95)], [(1000.0, 1100.0, NO_SPLICE)], _expect(cut=[(500.0, 560.0)])),
]


def _gate_snapshot(findings, holds, collect):
    held_markers = [_hold(*h) for h in holds]
    pairs = [_pair(*f) for f in findings]
    collected = [] if collect else None
    cut, ui, held, n, candidates = _gate(pairs, held_markers, hold_overlaps=collected)
    return {
        'cut': _spans(cut), 'ui': _spans(ui), 'held': _spans(held), 'n': n,
        'corroborated': [(h['start'], h['end'], dict(h['pass2_corroborated_span']))
                         for h in held_markers if h.get('pass2_corroborated')],
        'candidates': sorted((round(s['start'], 2), round(s['end'], 2), h['start'])
                             for s, h in candidates),
        'parents': [(round(o['start'], 2), round(o['end'], 2)) for _p, o in pairs],
    }, collected


@pytest.mark.parametrize('collect', [False, True])
@pytest.mark.parametrize('findings,holds,expected', GATE_SHAPES)
def test_gate_decisions_match_the_pre_split_snapshot(findings, holds, expected, collect):
    snapshot, _ = _gate_snapshot(findings, holds, collect=collect)
    assert {key: snapshot[key] for key in expected} == expected
    assert snapshot['ui'] == expected['cut']
    assert snapshot['held'] == []


def test_corroboration_and_release_spans_are_computed_on_the_full_finding():
    corroborated, _ = _gate_snapshot([(2104.57, 2198.87, 0.98)],
                                     [(2124.95, 2198.87, NO_SPLICE)], collect=True)
    assert corroborated['corroborated'] == [
        (2124.95, 2198.87, {'start': 2124.95, 'end': 2198.87})]
    assert corroborated['candidates'] == []
    released, _ = _gate_snapshot([(1825.7, 2001.4, 0.94)],
                                 [(1963.37, 2012.88, INCONCLUSIVE)], collect=True)
    assert released['corroborated'] == []
    assert released['candidates'] == [(1963.37, 2001.4, 1963.37)]


def test_a_finding_mostly_outside_the_hold_does_not_corroborate_it():
    # In-hold part alone (1000-1100) would corroborate; the full finding sits 1/3 inside.
    snapshot, collected = _gate_snapshot([(900.0, 1200.0, 0.95)],
                                         [(1000.0, 1100.0, NO_SPLICE)], collect=True)
    assert snapshot['corroborated'] == []
    [(_proc, orig)] = collected
    assert (orig['start'], orig['end']) == (900.0, 1200.0)


def test_collected_parent_is_the_pre_gate_finding():
    hold = _hold(2124.95, 2198.87)
    pair = _pair(2104.57, 2198.87, 0.98)
    collected = []
    _gate([pair], [hold], hold_overlaps=collected)
    [(proc, orig)] = collected
    assert 'held_for_review' not in orig and 'was_cut' not in orig
    assert 'was_cut' not in proc
    # The gate still marks the finding itself held so it is never resurrected.
    assert pair[1]['held_for_review'] is True


def test_validation_held_parent_is_not_collected():
    pair = _pair(900.0, 1110.0, held_for_review=True, hold_reason='max_duration')
    collected = []
    _cut, _ui, held, _n, _c = _gate([pair], [_hold(1000.0, 1100.0)], hold_overlaps=collected)
    assert collected == []
    assert _spans(held) == [(900.0, 1110.0)]


# ---------- Split: carve the parts outside the holds ----------

def _split(pairs, holds, cuts=()):
    return _split_pass2_candidates_around_holds(pairs, holds, list(cuts))


def test_part_before_the_hold_survives():
    proc, orig = _split([_pair(2104.57, 2198.87, 0.98)], [_hold(2124.95, 2198.87)])
    assert _spans(orig) == [(2104.57, 2124.95)]
    assert _spans(proc) == [(2104.57, 2124.95)]


def test_part_after_the_hold_survives():
    _proc, orig = _split([_pair(1040.0, 1300.0)], [_hold(1000.0, 1100.0)])
    assert _spans(orig) == [(1100.0, 1300.0)]


def test_parts_on_both_sides_survive():
    _proc, orig = _split([_pair(900.0, 1200.0)], [_hold(1000.0, 1100.0)])
    assert _spans(orig) == [(900.0, 1000.0), (1100.0, 1200.0)]


def test_gap_between_two_holds_becomes_a_candidate():
    _proc, orig = _split([_pair(990.0, 1210.0)],
                         [_hold(1000.0, 1100.0), _hold(1110.0, 1200.0)])
    assert _spans(orig) == [(990.0, 1000.0), (1100.0, 1110.0), (1200.0, 1210.0)]


def test_short_fragment_of_a_short_parent_is_dropped_with_a_reason(caplog):
    ledger = Pass2Ledger()
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        proc, orig = _split_pass2_candidates_around_holds(
            [_pair(95.0, 104.0)], [_hold(100.0, 130.0)], [], ledger=ledger)
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert proc == [] and orig == []
    assert any('Pass-2 span 95.0s-100.0s: dropped:short_fragment' in r.getMessage()
               for r in caplog.records)


def test_short_fragment_of_a_measured_parent_survives():
    proc, orig = _split([_pair(1095.0, 1300.0)], [_hold(1100.0, 1300.0)])
    assert _spans(orig) == [(1095.0, 1100.0)]
    assert proc[0]['_measured_split_fragment'] is True
    assert orig[0]['_measured_split_fragment'] is True


def test_protected_split_drops_a_sliver_at_the_barrier_edge():
    proc, orig = _pair(999.98, 1100.0)
    ledger = Pass2Ledger()
    out_proc, out_orig = _split_pass2_candidates_around_spans(
        [proc], [orig], [{'start': 1000.0, 'end': 1100.0}], [], 'protected audio', ledger=ledger)
    assert out_proc == [] and out_orig == []
    assert [e[3] for e in ledger._entries.values() if e[3]] == ['dropped:short_fragment']


def test_fragment_inside_a_replacement_beep_is_dropped_without_a_hold():
    cuts = [{'start': 200.0, 'end': 300.0}]
    beep = get_replacement_duration()
    hold = _hold(300.0, 400.0)
    # Processed parent starts inside the beep, so its pre-hold part has no original audio.
    proc = _ad(adjust_timestamp(200.0, cuts, beep) + 0.2, adjust_timestamp(400.0, cuts, beep))
    orig = _ad(300.0, 400.0)
    out_proc, out_orig = _split([(proc, orig)], [hold], cuts)
    assert out_proc == [] and out_orig == []


def test_fragment_sheds_the_parent_verdict_state():
    proc, orig = _pair(900.0, 1110.0, detection_stage='verification',
                       pass2_corroborated=True,
                       pass2_corroborated_span={'start': 1000.0, 'end': 1100.0},
                       reviewer_verdict='adjust',
                       reviewer_reasoning='x', user_confirmed=True)
    proc.update(was_cut=True)
    orig.update(held_for_review=True, hold_reason=NO_SPLICE, was_cut=False)
    out_proc, out_orig = _split([(proc, orig)], [_hold(1000.0, 1100.0)])
    assert _spans(out_orig) == [(900.0, 1000.0), (1100.0, 1110.0)]
    assert len(out_proc) == 2
    for frag in (*out_proc, *out_orig):
        for key in ('held_for_review', 'was_cut', 'hold_reason', 'validation',
                    'pass2_corroborated', 'pass2_corroborated_span',
                    'detection_stage', 'user_confirmed', 'reviewer_verdict',
                    'reviewer_reasoning'):
            assert key not in frag, key


# ---------- Verification pass: fragments go through the normal pass-2 pipeline ----------

def _run_pass2(holds, findings, *, review=None, reviewer_verdicts=None, **kwargs):
    """Drive the verification pass, recording what the pass-2 reviewer saw."""
    reviewer_seen = {}

    def pass2_reviewer(ctx, cut, ui, held, proc, orig, *args, **kw):
        reviewer_seen.update(cut=_spans(cut), ui=_spans(ui), pool=_spans(orig),
                             barriers=_spans(kw['protected_original_ranges']))
        if review:
            review(cut, ui, held, proc, orig)

    run = drive_verification_pass(findings, holds=holds, pass2_reviewer=pass2_reviewer,
                                  hold_verdicts=reviewer_verdicts, **kwargs)
    run.reviewer = reviewer_seen
    return run


def _assert_clear_of(ads, spans):
    for ad in ads:
        for s in spans:
            assert overlap_seconds(ad['start'], ad['end'], s['start'], s['end']) <= 1e-6, (
                _spans([ad]), _spans([s]))


def test_part_before_a_corroborated_hold_is_cut():
    # Production shape: 20.38 s of a pass-2 finding before a corroborated no-splice hold.
    hold = _hold(2124.95, 2198.87)
    run = _run_pass2([hold], [(2104.57, 2198.87, 0.98)])

    assert hold['pass2_corroborated'] is True
    assert hold['pass2_corroborated_span'] == {'start': 2124.95, 'end': 2198.87}
    assert _spans(run.output[1]) == [(2104.57, 2124.95)]
    assert run.output[1][0]['was_cut'] is True
    assert (2104.57, 2124.95) in [(round(s['start'], 2), round(s['end'], 2))
                                  for s in run.rendered['requested']]
    assert (2124.95, 2198.87) in _spans(run.rendered['cut_barriers'])
    # The fragment was validated on its own, after the parent.
    assert [v['orig'] for v in run.validated] == [[(2104.57, 2198.87)], [(2104.57, 2124.95)]]
    assert run.reviewer['ui'] == [(2104.57, 2124.95)]
    assert is_pending_review(hold)


def test_corroborated_hold_confirm_is_not_extended_by_the_fragment(monkeypatch):
    hold = _hold(2124.95, 2198.87)
    _run_pass2([hold], [(2104.57, 2198.87, 0.98)])
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', MagicMock())
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    kwargs = db.create_pattern_correction.call_args.kwargs
    assert kwargs['original_bounds'] == {'start': 2124.95, 'end': 2198.87}
    assert kwargs['corrected_bounds'] is None


def test_part_before_a_release_reviewed_hold_reaches_the_reviewer():
    # Production shape: 137.67 s of a pass-2 finding before a release-reviewed hold.
    hold = _hold(1963.37, 2012.88, INCONCLUSIVE)
    run = _run_pass2([hold], [(1825.7, 2001.4, 0.94)], reviewer_verdicts=[])

    assert run.hold_reviews == [[(1963.37, 2001.4)]]
    assert run.reviewer['cut'] == [(1825.7, 1963.37)]
    assert run.reviewer['ui'] == [(1825.7, 1963.37)]
    assert (1825.7, 1963.37) in run.reviewer['pool']
    assert _spans(run.output[1]) == [(1825.7, 1963.37)]
    assert 'pass2_corroborated' not in hold


def test_reviewer_rejecting_the_fragment_leaves_it_uncut():
    def reject(cut, ui, _held, _proc, _orig):
        for ad in (*cut, *ui):
            ad['was_cut'] = False
        cut.clear()
        ui.clear()

    run = _run_pass2([_hold(1000.0, 1100.0)], [(900.0, 1110.0, 0.95)], review=reject)
    assert run.output[1] == []
    assert run.rendered == {}


def test_low_confidence_fragment_is_never_cut():
    run = _run_pass2([_hold(1000.0, 1100.0)], [(900.0, 1110.0, 0.7)])
    assert run.output[1] == []
    assert _spans(run.output[3]) == [(900.0, 1000.0), (1100.0, 1110.0)]
    assert {a['hold_reason'] for a in run.output[3]} == {'verification_miss'}


def test_fragment_never_crosses_a_keep_or_the_hold():
    keep = {'start': 850.0, 'end': 870.0, 'action_applied': 'keep'}
    hold = _hold(1000.0, 1100.0)
    run = _run_pass2([hold], [(800.0, 1150.0, 0.95)], kept=[keep])
    assert sorted(_spans(run.output[1])) == [(800.0, 850.0), (870.0, 1000.0), (1100.0, 1150.0)]
    _assert_clear_of(run.output[1], [keep, hold])


def test_fragment_never_crosses_a_user_rejection():
    rejected = {'start': 900.0, 'end': 930.0}
    hold = _hold(1000.0, 1100.0)
    run = _run_pass2([hold], [(880.0, 1200.0, 0.95)], fp=[rejected])
    assert sorted(_spans(run.output[1])) == [(880.0, 900.0), (930.0, 1000.0), (1100.0, 1200.0)]
    _assert_clear_of(run.output[1], [rejected, hold])


def test_fragment_never_crosses_a_user_trim():
    trim = {'start': 1120.0, 'end': 1140.0}
    hold = _hold(1000.0, 1100.0)
    run = _run_pass2([hold], [(1050.0, 1200.0, 0.95)], trims=[trim])
    assert sorted(_spans(run.output[1])) == [(1100.0, 1120.0), (1140.0, 1200.0)]
    _assert_clear_of(run.output[1], [trim, hold])


def test_fragment_across_a_pass1_cut_maps_back_to_original_time():
    cuts = [{'start': 200.0, 'end': 300.0}]
    hold = _hold(1000.0, 1100.0)
    run = _run_pass2([hold], [(900.0, 1100.0, 0.95)], cuts=cuts)
    assert _spans(run.output[1]) == [(900.0, 1000.0)]


def test_fragment_validation_sees_the_hold_as_a_barrier():
    hold = _hold(1000.0, 1100.0)
    run = _run_pass2([hold], [(900.0, 1110.0, 0.95)], cue_gate_enabled=True)
    fragment_call = run.validated[-1]
    assert fragment_call['kwargs']['cue_gate_enabled'] is True
    assert (1000.0, 1100.0) in _spans(fragment_call['kwargs']['keep_barriers_processed'])


def test_every_pass2_validation_gets_the_run_fp_snapshot():
    fp = [{'start': 2000.0, 'end': 2010.0}]
    run = _run_pass2([_hold(1000.0, 1100.0)], [(900.0, 1110.0, 0.95)], fp=fp)
    assert len(run.validated) == 2
    assert all(call['kwargs']['false_positive_corrections'] == fp for call in run.validated)


def _protection(*holds):
    return processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[], holds=list(holds),
        pass1_cuts=[])


def _passthrough(proc, orig, _barriers):
    return proc, orig


def _gate_all(proc, orig, _holds, _overlaps):
    return list(proc), list(orig), [], 0, []


def test_fragment_matching_a_user_rejection_is_dropped(caplog):
    parents = [_pair(990.0, 1200.0)]
    ledger = Pass2Ledger()
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        result = _gate_hold_split_fragments(
            'example-podcast', 'a1b2c3d4e5f6', parents, _protection(_hold(1000.0, 1100.0)),
            [{'start': 1090.0, 'end': 1160.0}], _passthrough, _gate_all, ledger=ledger)
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert _spans(result.original) == [(990.0, 1000.0)]
    assert any('Pass-2 span 1100.0s-1200.0s: rejected:fp_correction' in r.getMessage()
               for r in caplog.records)


def test_fragment_moved_into_a_hold_by_validation_is_dropped():
    hold = _hold(1000.0, 1100.0)

    def widen(proc, orig, _barriers):
        return [dict(p, end=p['end'] + 5.0) for p in proc], [
            dict(o, end=o['end'] + 5.0) for o in orig]

    result = _gate_hold_split_fragments(
        'example-podcast', 'a1b2c3d4e5f6', [_pair(900.0, 1100.0)], _protection(hold), [],
        widen, _gate_all)
    assert result.to_cut == [] and result.original == []
    assert 'pass2_corroborated' not in hold


def test_failure_after_the_carve_records_the_outside_parts(caplog):
    hold = _hold(1000.0, 1100.0)
    ledger = Pass2Ledger()
    collected = []
    _gate([_pair(900.0, 1200.0)], [hold], hold_overlaps=collected, ledger=ledger)

    def boom(proc, orig, _barriers):
        raise RuntimeError('validator failed')

    with pytest.raises(RuntimeError):
        _gate_hold_split_fragments(
            'example-podcast', 'a1b2c3d4e5f6', collected, _protection(hold), [], boom,
            None, ledger=ledger)
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    lines = [r.getMessage().split('] ', 1)[1] for r in caplog.records
             if 'Pass-2 span' in r.getMessage()]
    assert lines == ['Pass-2 span 900.0s-1000.0s: dropped:pass_failed',
                     'Pass-2 span 1000.0s-1100.0s: covered:pass1_hold',
                     'Pass-2 span 1100.0s-1200.0s: dropped:pass_failed']


def test_hold_is_a_cut_barrier_for_the_trailing_extension():
    # A fragment ending near the file end must not extend over a hold after it.
    hold = _hold(2980.0, 2995.0)
    run = _run_pass2([hold], [(2900.0, 2990.0, 0.95)])
    assert _spans(run.output[1]) == [(2900.0, 2980.0)]
    applied = AudioProcessor().compute_applied_cuts(
        run.rendered['requested'], 3000.0, run.rendered['cut_barriers'],
        hard_barriers=run.rendered['hard_barriers'])
    assert [(c['start'], c['end']) for c in applied] == [(2900.0, 2980.0)]


def test_parent_copy_is_unaffected_by_later_fragment_mutation():
    hold = _hold(1000.0, 1100.0)
    pair = _pair(900.0, 1110.0)
    collected = []
    _gate([pair], [hold], hold_overlaps=collected)
    before = copy.deepcopy(collected)
    _split(collected, [hold])
    assert collected == before


# ---------- Several supported subspans of one hold ----------

def _release(monkeypatch, hold, subs, verdicts):
    pairs = [_pair(*sub) for sub in subs]
    *_rest, candidates = _gate(pairs, [hold])
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, det: SimpleNamespace(
        review=lambda **kw: ReviewResult(verdicts=list(verdicts))))
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model')
    monkeypatch.setattr(processing.ad_detector, 'get_verification_provider',
                        lambda: None)
    protection = processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[], holds=[hold],
        pass1_cuts=[])
    released, _pairs = processing._review_hold_release_candidates(
        _ctx(), candidates, [], protection)
    return candidates, released


def test_every_approved_subspan_of_a_hold_is_released(monkeypatch):
    # Production shape: two reads inside one no-splice hold, both confirmed by review.
    hold = _hold(1992.9, 2198.9)
    candidates, released = _release(
        monkeypatch, hold, [(1992.9, 2103.5, 0.98), (2124.9, 2195.4, 0.98)],
        [_verdict('confirmed', 1992.9, 2103.5), _verdict('confirmed', 2124.9, 2195.4)])

    assert [(s['start'], s['end']) for s, _h in candidates] == [
        (1992.9, 2103.5), (2124.9, 2195.4)]
    assert released == 2
    assert hold['pass2_released_spans'] == [
        {'start': 1992.9, 'end': 2103.5}, {'start': 2124.9, 'end': 2195.4}]
    assert is_pending_review(hold)

    db = _approval_db(monkeypatch)
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    filed = [c.kwargs for c in db.create_pattern_correction.call_args_list]
    assert [f['corrected_bounds'] for f in filed] == [
        {'start': 1992.9, 'end': 2103.5}, {'start': 2124.9, 'end': 2195.4}]
    assert {tuple(f['original_bounds'].values()) for f in filed} == {(1992.9, 2198.9)}


def test_a_rejected_subspan_leaves_only_the_approved_one_released(monkeypatch):
    hold = _hold(1000.0, 1200.0)
    _candidates, released = _release(
        monkeypatch, hold, [(1010.0, 1050.0), (1100.0, 1150.0)],
        [_verdict('reject', 1010.0, 1050.0), _verdict('confirmed', 1100.0, 1150.0)])
    assert released == 1
    assert hold['pass2_released_spans'] == [{'start': 1100.0, 'end': 1150.0}]
    assert hold['pass2_reviewed_release'] == {'start': 1100.0, 'end': 1150.0}


def test_a_rejected_later_subspan_is_logged_as_held_not_the_whole_hold(monkeypatch, caplog):
    hold = _hold(1000.0, 1200.0)
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        _candidates, released = _release(
            monkeypatch, hold, [(1010.0, 1050.0), (1100.0, 1150.0)],
            [_verdict('confirmed', 1010.0, 1050.0), _verdict('reject', 1100.0, 1150.0)])
    assert released == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any('subspan 1100.0s-1150.0s of hold 1000.0s-1200.0s stays held' in m
               for m in messages)
    assert not any('the hold stays whole' in m for m in messages)


def test_an_adjust_into_an_already_released_subspan_is_not_released(monkeypatch):
    hold = _hold(1000.0, 1200.0)
    _candidates, released = _release(
        monkeypatch, hold, [(1010.0, 1050.0), (1100.0, 1150.0)],
        [_verdict('confirmed', 1010.0, 1050.0),
         _verdict('adjust', 1100.0, 1150.0, adjusted=(1040.0, 1150.0))])
    assert released == 1
    assert hold['pass2_released_spans'] == [{'start': 1010.0, 'end': 1050.0}]


def test_a_fast_path_corroboration_still_owns_the_hold(monkeypatch):
    hold = _hold(1000.0, 1100.0)
    _candidates, released = _release(
        monkeypatch, hold, [(1000.0, 1100.0)], [_verdict('confirmed', 1000.0, 1100.0)])
    assert released == 0
    assert 'pass2_released_spans' not in hold


def test_recut_cuts_each_released_subspan_and_holds_the_rest():
    hold = _hold(1000.0, 1200.0)
    hold.update(confidence=0.95, reason='Acme sponsor read', detection_stage='claude')
    # Newest first, as the loader returns them.
    confirms = [_release_confirm((1000.0, 1200.0), (1100.0, 1150.0)),
                _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))]

    ads = _recut_validate([hold], confirms)

    got = sorted((a['start'], a['end'], a['validation']['decision'],
                  a.get('hold_reason')) for a in ads)
    assert got == [
        (1000.0, 1010.0, 'REVIEW', NO_SPLICE),
        (1010.0, 1050.0, 'ACCEPT', None),
        (1050.0, 1100.0, 'REVIEW', NO_SPLICE),
        (1100.0, 1150.0, 'ACCEPT', None),
        (1150.0, 1200.0, 'REVIEW', NO_SPLICE)]

    saved = []
    for a in ads:
        a = {k: v for k, v in a.items() if k != 'validation'}
        a['was_cut'] = not a.get('held_for_review')
        saved.append(a)
    again = _recut_validate(saved, confirms)
    assert sorted((a['start'], a['end'], a['validation']['decision']) for a in again) == [
        (s, e, d) for s, e, d, _r in got]


def test_newer_user_confirm_outranks_the_split():
    hold = _hold(1000.0, 1200.0)
    hold.update(confidence=0.95, reason='Acme sponsor read', detection_stage='claude')
    user = {'start': 1000.0, 'end': 1200.0, 'correction_type': 'confirm'}
    confirms = [user, _release_confirm((1000.0, 1200.0), (1100.0, 1150.0)),
                _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))]
    ads = _recut_validate([hold], confirms)
    assert [(a['start'], a['end'], a['validation']['decision']) for a in ads] == [
        (1000.0, 1200.0, Decision.ACCEPT.value)]


def test_verification_pass_reviews_every_disjoint_subspan_of_a_hold():
    hold = _hold(1992.9, 2198.9, INCONCLUSIVE)
    run = _run_pass2([hold], [(1992.9, 2103.5, 0.98), (2124.9, 2195.4, 0.98)],
                     reviewer_verdicts=[_verdict('confirmed', 1992.9, 2103.5),
                                        _verdict('confirmed', 2124.9, 2195.4)])
    assert run.hold_reviews == [[(1992.9, 2103.5), (2124.9, 2195.4)]]
    assert run.output[7] == 2
    assert _spans(hold['pass2_released_spans']) == [(1992.9, 2103.5), (2124.9, 2195.4)]
    assert hold['pass2_reviewed_release'] == {'start': 1992.9, 'end': 2103.5}
    assert run.output[1] == []
    assert is_pending_review(hold)
    assert run.rendered == {}


# ---------- Confirmed-correction clamp ----------

def _detection(start, end, confidence=0.97):
    return {'start': start, 'end': end, 'confidence': confidence, 'category': 'sponsor',
            'sponsor': 'Acme', 'reason': 'Acme sponsor read', 'detection_stage': 'claude'}


def _clamp_validate(detection, confirm):
    segments = [{'start': 0.0, 'end': 3000.0, 'text': 'Acme sponsor read, visit acme.com'}]
    validator = AdValidator(episode_duration=3000.0, segments=segments,
                            confirmed_corrections=[confirm], min_cut_confidence=0.8)
    return sorted(validator.validate([detection]).ads, key=lambda a: a['start'])


def test_plain_confirm_clamp_leaves_its_remainder_as_a_candidate():
    # Production shape: a plain confirm of 1963.37-2012.88 and a re-detection to 2020.4.
    confirm = {'start': 1963.37, 'end': 2012.88, 'correction_type': 'confirm'}
    ads = _clamp_validate(_detection(1963.4, 2020.4), confirm)

    assert _spans(ads) == [(1963.4, 2012.88), (2012.88, 2020.4)]
    approved, remainder = ads
    assert approved['validation']['user_confirmed'] is True
    assert 'user_confirmed' not in remainder['validation']
    assert 'INFO: User confirmed as ad' not in remainder['validation']['flags']
    assert remainder['_skip_pattern_learning'] is True


@pytest.mark.parametrize('kind', ['confirm', 'boundary_adjustment'])
def test_trim_or_adjustment_clamp_keeps_the_trimmed_part_out(kind):
    confirm = {'start': 1000.0, 'end': 1100.0, 'correction_type': kind,
               'confirmed_span': {'start': 1000.0, 'end': 1060.0}}
    ads = _clamp_validate(_detection(1000.0, 1100.0), confirm)
    assert _spans(ads) == [(1000.0, 1060.0)]


def test_short_clamp_remainder_is_dropped_with_a_reason(caplog):
    confirm = {'start': 1000.0, 'end': 1100.0, 'correction_type': 'confirm'}
    with caplog.at_level(logging.INFO):
        ads = _clamp_validate(_detection(1000.0, 1105.0), confirm)
    assert _spans(ads) == [(1000.0, 1100.0)]
    assert any('1100.0s-1105.0s' in r.getMessage() and 'too short' in r.getMessage()
               for r in caplog.records)


def test_unflagged_marker_with_the_same_releases_is_split():
    validator = AdValidator(
        episode_duration=3000.0, segments=[], min_cut_confidence=0.8,
        confirmed_corrections=[_release_confirm((1000.0, 1200.0), (1100.0, 1150.0)),
                               _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))])
    assert len(validator._split_multi_release_holds([_hold(1000.0, 1200.0)])) > 1


@pytest.mark.parametrize('flag', ['_reviewer_rejected', '_user_kept_by_trim'])
def test_rejected_or_kept_marker_is_not_split_by_releases(flag):
    validator = AdValidator(
        episode_duration=3000.0, segments=[], min_cut_confidence=0.8,
        confirmed_corrections=[_release_confirm((1000.0, 1200.0), (1100.0, 1150.0)),
                               _release_confirm((1000.0, 1200.0), (1010.0, 1050.0))])
    marker = dict(_hold(1000.0, 1200.0), **{flag: True})
    assert validator._split_multi_release_holds([marker]) == [marker]
    assert '_pinned_release_confirm' not in marker

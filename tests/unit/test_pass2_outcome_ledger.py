"""Every final pass-2 span ends with exactly one outcome line and a run_stats count."""
import logging
import random
import re
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('pass2_outcome_ledger_test_')

from main_app import processing
from main_app.verification_reconciliation import Pass2Ledger
from tests.unit.pass2_test_utils import NO_SPLICE, _hold, drive_verification_pass
from utils.markers import subtract_spans

LINE = re.compile(r'Pass-2 span (-?\d+\.\d)s-(-?\d+\.\d)s: (\S+)$')


def _ledger_lines(records):
    return [(float(m[1]), float(m[2]), m[3])
            for m in (LINE.search(r.getMessage()) for r in records) if m]


def _run(findings, *, validator_rejects=(), reviewer_rejects=(), reviewer_error=None,
         reviewer_widen=None, caplog=None, **kwargs):
    """Drive the verification pass with ledger-aware validator and reviewer stubs."""
    def validate(*args, ledger=None, **_kwargs):
        proc, orig = [], []
        for p, o in zip(args[2], args[3], strict=True):
            if (o['start'], o['end']) in validator_rejects:
                ledger.record(o, 'rejected:validator')
                continue
            proc.append(p)
            orig.append(o)
        return proc, orig

    def pass2_reviewer(ctx, cut, ui, held, proc, orig, *args, ledger=None, **_kwargs):
        if reviewer_error:
            raise reviewer_error
        for p, o in zip(cut, ui, strict=True):
            if reviewer_widen and (o['start'], o['end']) == reviewer_widen[0]:
                p['start'], p['end'] = o['start'], o['end'] = reviewer_widen[1]
        for p, o in list(zip(cut, ui, strict=True)):
            if (o['start'], o['end']) in reviewer_rejects:
                cut.remove(p)
                ui.remove(o)
                ledger.record(o, 'rejected:reviewer')

    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        run = drive_verification_pass(findings, duration=6000.0, validate=validate,
                                      pass2_reviewer=pass2_reviewer, **kwargs)
    run.lines = _ledger_lines(caplog.records)
    return run


def test_one_line_and_one_count_per_outcome(caplog):
    run = _run(
        [(200.0, 260.0), (400.0, 460.0, 0.7), (600.0, 660.0, 0.95, 'self_promo'),
         (800.0, 860.0), (1000.0, 1060.0), (1200.0, 1260.0), (1400.0, 1460.0, 0.3),
         (1610.0, 1660.0), (1800.0, 1860.0)],
        holds=[_hold(1190.0, 1270.0)],
        kept=[{'start': 1600.0, 'end': 1700.0, 'action_applied': 'keep'}],
        fp=[{'start': 800.0, 'end': 860.0}],
        validator_rejects={(1000.0, 1060.0)},
        reviewer_rejects={(1800.0, 1860.0)},
        caplog=caplog)

    assert sorted(run.lines) == [
        (200.0, 260.0, 'cut'),
        (400.0, 460.0, 'held:verification_miss'),
        (600.0, 660.0, 'kept'),
        (800.0, 860.0, 'rejected:fp_correction'),
        (1000.0, 1060.0, 'rejected:validator'),
        (1200.0, 1260.0, 'covered:pass1_hold'),
        (1400.0, 1460.0, 'dropped:below_miss_floor'),
        (1610.0, 1660.0, 'dropped:inside_kept'),
        (1800.0, 1860.0, 'rejected:reviewer'),
    ]
    assert run.stats['pass2_outcomes'] == {
        'cut': 1, 'held:verification_miss': 1, 'kept': 1, 'rejected:fp_correction': 1,
        'rejected:validator': 1, 'covered:pass1_hold': 1, 'dropped:below_miss_floor': 1,
        'dropped:inside_kept': 1, 'rejected:reviewer': 1}


def test_finding_split_around_a_hold_gets_one_line_per_fragment(caplog):
    run = _run([(900.0, 1200.0)], holds=[_hold(1000.0, 1100.0)], caplog=caplog)
    assert sorted(run.lines) == [
        (900.0, 1000.0, 'cut'), (1000.0, 1100.0, 'covered:pass1_hold'),
        (1100.0, 1200.0, 'cut')]
    assert run.stats['pass2_outcomes'] == {'cut': 2, 'covered:pass1_hold': 1}


def test_short_fragment_beside_a_hold_is_dropped_with_its_own_line(caplog):
    run = _run([(95.0, 104.0)], holds=[_hold(100.0, 130.0)], caplog=caplog)
    assert sorted(run.lines) == [
        (95.0, 100.0, 'dropped:short_fragment'), (100.0, 104.0, 'covered:pass1_hold')]


def test_bounds_are_in_original_time_across_a_pass1_cut(caplog):
    cuts = [{'start': 100.0, 'end': 200.0}]
    run = _run([(900.0, 1100.0)], holds=[_hold(1000.0, 1100.0)], cuts=cuts, caplog=caplog)
    assert sorted(run.lines) == [(900.0, 1000.0, 'cut'), (1000.0, 1100.0, 'covered:pass1_hold')]


def test_carved_off_parts_are_labelled_by_what_protects_them(caplog):
    run = _run([(100.0, 400.0)],
               kept=[{'start': 120.0, 'end': 140.0, 'action_applied': 'keep'}],
               fp=[{'start': 200.0, 'end': 220.0}], trims=[{'start': 300.0, 'end': 320.0}],
               caplog=caplog)
    assert sorted(run.lines) == [
        (100.0, 120.0, 'cut'), (120.0, 140.0, 'kept:pass1_keep'), (140.0, 200.0, 'cut'),
        (200.0, 220.0, 'rejected:fp_correction'), (220.0, 300.0, 'cut'),
        (300.0, 320.0, 'kept:user_trim'), (320.0, 400.0, 'cut')]


def test_failed_status_flushes_every_candidate(caplog):
    run = _run([(200.0, 260.0), (500.0, 560.0)], status='detection_failed', caplog=caplog)
    assert sorted(run.lines) == [(200.0, 260.0, 'dropped:pass_failed'),
                                 (500.0, 560.0, 'dropped:pass_failed')]
    assert run.stats['pass2_outcomes'] == {'dropped:pass_failed': 2}


def test_exception_flushes_every_unsettled_candidate(caplog):
    run = _run([(200.0, 260.0), (400.0, 460.0, 0.3), (900.0, 1200.0)],
               holds=[_hold(1000.0, 1100.0)], reviewer_error=RuntimeError('boom'),
               caplog=caplog)
    assert run.output[6] is False
    assert sorted(run.lines) == [
        (200.0, 260.0, 'dropped:pass_failed'), (400.0, 460.0, 'dropped:below_miss_floor'),
        (900.0, 1000.0, 'dropped:pass_failed'), (1000.0, 1100.0, 'covered:pass1_hold'),
        (1100.0, 1200.0, 'dropped:pass_failed')]


def test_recut_failure_after_a_protected_carve_reports_each_part_once(caplog):
    run = _run([(150.0, 400.0)], trims=[{'start': 120.0, 'end': 140.0}],
               reviewer_widen=((150.0, 400.0), (100.0, 400.0)),
               recut_error=RuntimeError('boom'), caplog=caplog)
    assert run.output[6] is False
    assert sorted(run.lines) == [
        (100.0, 120.0, 'dropped:pass_failed'), (120.0, 140.0, 'kept:user_trim'),
        (140.0, 400.0, 'dropped:pass_failed')]


def test_helper_records_into_the_ledger_it_is_given(caplog):
    ledger = Pass2Ledger()
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        processing._gate_verification_ads_by_confidence(
            [{'start': 10.0, 'end': 40.0, 'confidence': 0.2}],
            [{'start': 10.0, 'end': 40.0, 'confidence': 0.2}], min_cut_confidence=0.8,
            ledger=ledger)
        assert _ledger_lines(caplog.records) == []
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert _ledger_lines(caplog.records) == [(10.0, 40.0, 'dropped:below_miss_floor')]


def test_later_record_replaces_an_earlier_outcome(caplog):
    ledger = Pass2Ledger()
    ad = {'start': 10.0, 'end': 20.0}
    ledger.record(ad, 'dropped:below_miss_floor')
    ledger.settle(cut=[ad], held=[], kept=[])
    stats = {}
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6', stats)
    assert _ledger_lines(caplog.records) == [(10.0, 20.0, 'cut')]
    assert stats == {'pass2_outcomes': {'cut': 1}}


def test_carving_a_recorded_span_removes_its_earlier_line(caplog):
    # A below-floor miss the reviewer resurrects, then carved by a beep sibling.
    ledger = Pass2Ledger()
    orig = {'start': 100.0, 'end': 160.0}
    ledger.record(orig, 'dropped:below_miss_floor')
    beep = {'start': 140.0, 'end': 200.0}
    _proc, fragments = processing._split_pass2_candidates_around_spans(
        [dict(orig)], [orig], [beep], [], 'beep-replacement audio', ledger=ledger,
        carved_labels=[dict(beep, label='covered:beep')])
    ledger.settle(cut=fragments, held=[], kept=[])
    stats = {}
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6', stats)
    assert _ledger_lines(caplog.records) == [(100.0, 140.0, 'cut'), (140.0, 160.0, 'covered:beep')]
    assert stats == {'pass2_outcomes': {'cut': 1, 'covered:beep': 1}}


# ---------- Property: every fragment the pass produces has exactly one line ----------

SLOT = 400.0


def _scenario(seed):
    rng = random.Random(seed)
    findings, holds, kept, fp, trims = [], [], [], [], []
    for i in range(6):
        base = 100.0 + SLOT * i
        if rng.random() < 0.6:
            holds.append(_hold(base + 100.0, base + 200.0,
                               rng.choice([NO_SPLICE, 'reviewer_inconclusive_bounds'])))
        if rng.random() < 0.4:
            kept.append({'start': base + 40.0, 'end': base + 60.0,
                         'action_applied': 'keep'})
        if rng.random() < 0.3:
            fp.append({'start': base + 70.0, 'end': base + 85.0})
        if rng.random() < 0.3:
            trims.append({'start': base + 250.0, 'end': base + 265.0})
        # Half the findings start just before the hold, leaving a short outside part.
        start = round(base + rng.choice([rng.uniform(0.0, 190.0), rng.uniform(93.0, 99.5)]), 1)
        end = round(min(base + 380.0, start + rng.uniform(4.0, 260.0)), 1)
        findings.append((start, end, rng.choice([0.3, 0.7, 0.95, 0.95]),
                         rng.choice(['sponsor'] * 5 + ['self_promo'])))
    return findings, holds, kept, fp, trims


def _slot(span):
    return int((span[0] - 100.0) // SLOT)


@pytest.mark.parametrize('seed', range(40))
def test_every_fragment_has_exactly_one_line(seed, caplog):
    findings, holds, kept, fp, trims = _scenario(seed)
    cuts = [{'start': 20.0, 'end': 60.0}]
    run = _run(findings, holds=holds, cuts=cuts, kept=kept, fp=fp, trims=trims,
               caplog=caplog)

    assert len(run.lines) == len(set(run.lines))
    assert sum(run.stats['pass2_outcomes'].values()) == len(run.lines)
    by_span = {}
    for start, end, outcome in run.lines:
        by_span.setdefault((start, end), []).append(outcome)
    # Each marker the pass returns is a final fragment with its one line.
    for ad in [*run.output[1], *run.output[3]]:
        outcomes = by_span.get((round(ad['start'], 1), round(ad['end'], 1)), [])
        assert len(outcomes) == 1, (ad['start'], ad['end'], run.lines)
        expected = ('kept' if ad.get('action_applied') == 'keep'
                    else f"held:{ad['hold_reason']}" if ad.get('held_for_review')
                    else 'cut')
        assert outcomes == [expected]
    for i, (fs, fe, *_rest) in enumerate(findings):
        lines = sorted((s, e) for s, e, _o in run.lines if _slot((s, e)) == i)
        assert lines, (i, fs, fe, run.lines)
        # Lines of one finding never overlap, so no audio is reported twice.
        for (_s1, e1), (s2, _e2) in zip(lines, lines[1:], strict=False):
            assert s2 >= e1 - 0.1, (i, lines)
        # No part of the finding, protected or not, ends without a line.
        missing = subtract_spans([(fs, fe)], lines)
        assert all(hi - lo <= 0.1 for lo, hi in missing), (i, fs, fe, missing, run.lines)


def _validate_with_ledger(processed, original, fp_corrections=()):
    fake_db = MagicMock()
    fake_db.get_false_positive_corrections.return_value = list(fp_corrections)
    fake_db.get_confirmed_corrections.return_value = []
    ledger = Pass2Ledger()
    segments = [{'start': t, 'end': t + 30.0, 'text': 'spoken content here'}
                for t in range(0, 600, 30)]
    kept = processing._validate_verification_ads(
        'example-podcast', 'a1b2c3d4e5f6', processed, original, segments,
        ads_to_remove=[], episode_description=None, min_cut_confidence=0.8,
        db=fake_db, processed_duration=600.0, ledger=ledger)
    return kept, ledger


def test_validator_merge_records_the_absorbed_finding_as_covered(caplog):
    processed = [{'start': 100.0, 'end': 130.0, 'confidence': 0.9},
                 {'start': 133.0, 'end': 160.0, 'confidence': 0.9}]
    original = [dict(p) for p in processed]
    (_proc, kept_orig), ledger = _validate_with_ledger(processed, original)
    assert kept_orig == [original[0]]
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert _ledger_lines(caplog.records) == [(133.0, 160.0, 'covered')]


def test_validator_reject_is_recorded_once(caplog):
    processed = [{'start': 100.0, 'end': 130.0, 'confidence': 0.9}]
    original = [dict(p) for p in processed]
    (_proc, kept_orig), ledger = _validate_with_ledger(
        processed, original, fp_corrections=[{'start': 95.0, 'end': 135.0}])
    assert kept_orig == []
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert _ledger_lines(caplog.records) == [(100.0, 130.0, 'rejected:validator')]


def test_validator_clamp_past_the_audio_end_is_not_called_covered(caplog):
    processed = [{'start': 100.0, 'end': 130.0, 'confidence': 0.9},
                 {'start': 610.0, 'end': 640.0, 'confidence': 0.9}]
    original = [dict(p) for p in processed]
    (_proc, kept_orig), ledger = _validate_with_ledger(processed, original)
    assert kept_orig == [original[0]]
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')
    assert _ledger_lines(caplog.records) == [(610.0, 640.0, 'dropped:validator_clamped')]


def test_surviving_markers_carry_their_outcome(caplog):
    run = _run([(200.0, 260.0), (400.0, 460.0, 0.7), (600.0, 660.0, 0.95, 'self_promo'),
                (1400.0, 1460.0, 0.3)], caplog=caplog)
    stamped = sorted((a['start'], a['pass2_outcome']) for a in [*run.output[1], *run.output[3]])
    assert stamped == [(200.0, 'cut'), (400.0, 'held:verification_miss'), (600.0, 'kept')]
    assert run.stats['pass2_outcomes']['dropped:below_miss_floor'] == 1

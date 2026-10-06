"""Pass-2 evidence inside a pass-1 hold is reviewed at its own span, not dropped."""
import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('hold_release_review_test_')

from ad_reviewer import (
    ReviewResult, ReviewVerdict, _review_inconclusive_reason, split_resurrection_pool,
)
from ad_validator import Decision
from audio_processor import get_replacement_duration
from config import (
    HOLD_REASON_TRANSCRIPT_DIFFERENTIAL, PASS2_AUTOAPPROVE_HOLD_REASONS,
    PASS2_REVIEWED_RELEASE_HOLD_REASONS, is_pending_review,
)
from main_app import processing
from utils.markers import EDGE_TOLERANCE, is_reviewer_rejected, reviewer_hold_stands
from main_app.verification_reconciliation import (
    Pass2Ledger, WordEdges, _gate_verification_ads_by_confidence,
)
from utils.time import adjust_timestamp
from verification_pass import _build_timestamp_map, _map_to_original
from tests.unit.pass2_test_utils import (
    NO_SPLICE, _approval_db, _ctx, _hold, _recut_validate, _release_confirm, _spans,
    _user_corrections, _verdict, drive_verification_pass,
)

INCONCLUSIVE = 'reviewer_inconclusive_bounds'


def _proc(start, end, confidence=0.95):
    return {'start': start, 'end': end, 'confidence': confidence,
            'validation': {'decision': 'ACCEPT', 'adjusted_confidence': confidence}}


def _orig(start, end, confidence=0.95):
    return {'start': start, 'end': end, 'confidence': confidence,
            'sponsor': 'Acme', 'reason': 'sponsor read'}


def _gate(proc, orig, holds, **kwargs):
    return _gate_verification_ads_by_confidence(
        proc, orig, min_cut_confidence=0.8, pass1_held_markers=holds, **kwargs)


def _review(monkeypatch, candidates, verdicts, protection=None, enabled=True):
    released, _pairs, calls = _review_pairs(
        monkeypatch, candidates, verdicts, protection=protection, enabled=enabled)
    return released, calls


def _review_pairs(monkeypatch, candidates, verdicts, protection=None, enabled=True,
                  pass1_cuts=(), covered=(), ledger=None):
    calls = []

    def review(**kwargs):
        calls.append(kwargs)
        return ReviewResult(verdicts=list(verdicts))

    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: enabled)
    monkeypatch.setattr(processing, '_build_reviewer',
                        lambda db, det: SimpleNamespace(review=review))
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model')
    monkeypatch.setattr(processing.ad_detector, 'get_verification_provider',
                        lambda: None)
    holds = [c[1] for c in candidates]
    protection = protection or processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[],
        holds=holds, pass1_cuts=[])
    released, pairs = processing._review_hold_release_candidates(
        _ctx(), candidates, [], protection, pass1_cuts=list(pass1_cuts),
        covered=list(covered), ledger=ledger)
    return released, pairs, calls


# ---------- Gate: build the reviewable subspan ----------

def test_supported_subspan_inside_hold_becomes_a_release_candidate():
    hold = _hold(1000.0, 1100.0)
    proc, orig = [_proc(1040.0, 1060.0)], [_orig(1040.0, 1060.0)]

    cut, ui, held, n, candidates = _gate(proc, orig, [hold])

    assert (cut, ui, held, n) == ([], [], [], 0)
    assert 'pass2_corroborated' not in hold
    [(orig_sub, owner)] = candidates
    assert owner is hold
    assert (orig_sub['start'], orig_sub['end']) == (1040.0, 1060.0)
    assert orig_sub['held_for_review'] is True
    # The dropped finding itself can never be resurrected.
    assert orig[0]['held_for_review'] is True


def test_finding_sent_to_hold_review_is_not_logged_as_dropped(caplog):
    ledger = Pass2Ledger()
    with caplog.at_level('INFO', logger='podcast.audio'):
        _gate([_proc(1040.0, 1060.0)], [_orig(1040.0, 1060.0)], [_hold(1000.0, 1100.0)],
              ledger=ledger)
        _gate([_proc(1040.0, 1060.0, 0.5)], [_orig(1040.0, 1060.0, 0.5)],
              [_hold(1000.0, 1100.0)], ledger=ledger)
        ledger.emit('example-podcast', 'a1b2c3d4e5f6')

    messages = [r.getMessage() for r in caplog.records]
    assert [m for m in messages if m.startswith('Sent pass-2 span')] == [
        'Sent pass-2 span 1040.0s-1060.0s to hold review']
    assert not any('Dropping' in m for m in messages)
    assert [m for m in messages if 'Pass-2 span ' in m] == [
        '[example-podcast:a1b2c3d4e5f6] Pass-2 span 1040.0s-1060.0s: covered:pass1_hold'] * 2


def test_candidate_is_clipped_to_the_hold_in_original_coordinates():
    cuts = [{'start': 100.0, 'end': 200.0}]
    beep = get_replacement_duration()
    hold = _hold(300.0, 400.0)
    proc = _proc(adjust_timestamp(380.0, cuts, beep), adjust_timestamp(420.0, cuts, beep))
    ts_map = _build_timestamp_map(cuts)
    orig = _orig(_map_to_original(proc['start'], ts_map, beep),
                 _map_to_original(proc['end'], ts_map, beep))

    *_rest, candidates = _gate([proc], [orig], [hold])

    [(orig_sub, _owner)] = candidates
    assert (orig_sub['start'], orig_sub['end']) == pytest.approx((380.0, 400.0))


def test_fast_path_corroboration_still_stamps_without_a_candidate():
    hold = _hold(1000.0, 1100.0)
    *_rest, n, candidates = _gate([_proc(1001.0, 1099.0)], [_orig(1001.0, 1099.0)], [hold])

    assert n == 1 and candidates == []
    assert hold['pass2_corroborated'] is True


@pytest.mark.parametrize('orig_span,reason,kwargs', [
    ((1040.0, 1045.0), NO_SPLICE, {}),
    ((1040.0, 1060.0), 'no_cue_evidence', {}),
    ((1040.0, 1060.0), NO_SPLICE, {'cue_gate_enabled': True}),
    ((1040.0, 1060.0), NO_SPLICE, {'hard_barriers_orig': [{'start': 1050.0, 'end': 1055.0}]}),
])
def test_no_candidate_when_the_subspan_is_unsupported(orig_span, reason, kwargs):
    hold = _hold(1000.0, 1100.0, reason)
    *_rest, candidates = _gate([_proc(*orig_span)], [_orig(*orig_span)], [hold], **kwargs)
    assert candidates == []


def test_low_confidence_finding_gives_no_candidate():
    hold = _hold(1000.0, 1100.0)
    *_rest, candidates = _gate([_proc(1040.0, 1060.0, 0.5)],
                               [_orig(1040.0, 1060.0, 0.5)], [hold])
    assert candidates == []


def test_subspan_stops_at_a_neighbouring_hold():
    hold = _hold(1000.0, 1100.0)
    other = _hold(1080.0, 1200.0, 'max_duration')
    *_rest, candidates = _gate([_proc(1040.0, 1100.0)], [_orig(1040.0, 1100.0)],
                               [hold, other])
    [(orig_sub, owner)] = candidates
    assert owner is hold
    assert (orig_sub['start'], orig_sub['end']) == (1040.0, 1080.0)


def test_measured_members_narrow_the_subspan():
    hold = _hold(1000.0, 1100.0)
    orig = _orig(1030.0, 1080.0)
    orig['merged_protected_start'], orig['merged_protected_end'] = 1040.0, 1070.0
    orig['merged_member_spans'] = [
        {'start': 1040.0, 'end': 1070.0, 'stage': 'fingerprint',
         'fingerprint_match_start': 1045.0, 'fingerprint_match_end': 1065.0}]
    *_rest, candidates = _gate([_proc(1030.0, 1080.0)], [orig], [hold])
    [(orig_sub, _owner)] = candidates
    assert (orig_sub['start'], orig_sub['end']) == (1045.0, 1065.0)


def test_members_with_an_unmeasured_gap_use_the_longest_run():
    hold = _hold(1000.0, 1100.0, INCONCLUSIVE)
    orig = _orig(1010.0, 1090.0)
    orig['merged_protected_start'], orig['merged_protected_end'] = 1010.0, 1090.0
    orig['merged_member_spans'] = [
        {'start': 1010.0, 'end': 1020.0, 'stage': 'cue_pair'},
        {'start': 1050.0, 'end': 1080.0, 'stage': 'cue_pair'}]
    *_rest, candidates = _gate([_proc(1010.0, 1090.0)], [orig], [hold])
    [(orig_sub, _owner)] = candidates
    assert (orig_sub['start'], orig_sub['end']) == (1050.0, 1080.0)


def test_later_fast_path_corroboration_prunes_the_candidate():
    hold = _hold(1000.0, 1100.0)
    proc = [_proc(1040.0, 1060.0), _proc(1001.0, 1099.0)]
    orig = [_orig(1040.0, 1060.0), _orig(1001.0, 1099.0)]
    *_rest, n, candidates = _gate(proc, orig, [hold])
    assert n == 1 and candidates == []
    assert hold['pass2_corroborated_span'] == {'start': 1001.0, 'end': 1099.0}


def test_review_skips_a_hold_corroborated_after_gating(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    hold['pass2_corroborated'] = True
    hold['pass2_corroborated_span'] = {'start': 1001.0, 'end': 1099.0}
    released, _calls = _review(monkeypatch, candidates,
                               [_verdict('confirmed', 1040.0, 1060.0)])
    assert released == 0
    assert hold['pass2_corroborated_span'] == {'start': 1001.0, 'end': 1099.0}
    assert 'pass2_reviewed_release' not in hold


def test_edges_move_inward_off_a_split_word():
    segments = [{'start': 1000.0, 'end': 1100.0, 'words': [
        {'start': 1039.5, 'end': 1040.5}, {'start': 1059.6, 'end': 1060.4}]}]
    edges = WordEdges(segments)
    assert edges.inside(1040.0, 'start') == 1040.5
    assert edges.inside(1060.0, 'end') == 1059.6
    assert edges.inside(1045.0, 'start') == 1045.0
    assert edges.inside(1040.5, 'start') == 1040.5


def _linear_word_edge(segments, value, edge):
    for seg in segments:
        for word in seg.get('words') or []:
            lo, hi = word.get('start'), word.get('end')
            if lo is None or hi is None:
                continue
            if lo < value - EDGE_TOLERANCE and hi > value + EDGE_TOLERANCE:
                return hi if edge == 'start' else lo
    return value


@pytest.mark.parametrize('seed', range(20))
def test_indexed_word_edges_match_a_linear_scan(seed):
    rng = random.Random(seed)
    words, t = [], 0.0
    for _ in range(200):
        t += rng.uniform(0.0, 0.6)
        words.append({'start': t, 'end': t + rng.uniform(0.0, 1.5)})
    if seed % 2:
        rng.shuffle(words)
    words.append({'start': None, 'end': 3.0})
    segments = [{'words': words[:100]}, {'words': words[100:]}, {'text': 'untimed'}]
    edges = WordEdges(segments)
    for _ in range(200):
        value = rng.uniform(-1.0, t + 2.0)
        for edge in ('start', 'end'):
            assert edges.inside(value, edge) == _linear_word_edge(segments, value, edge)


def test_disjoint_findings_give_one_candidate_each():
    hold = _hold(1000.0, 1100.0)
    proc = [_proc(1010.0, 1030.0), _proc(1050.0, 1090.0)]
    orig = [_orig(1010.0, 1030.0), _orig(1050.0, 1090.0)]
    *_rest, candidates = _gate(proc, orig, [hold])
    assert [(s['start'], s['end']) for s, _h in candidates] == [
        (1010.0, 1030.0), (1050.0, 1090.0)]
    assert all(h is hold for _s, h in candidates)


def test_overlapping_findings_keep_the_longest_candidate():
    hold = _hold(1000.0, 1100.0)
    proc = [_proc(1010.0, 1030.0), _proc(1020.0, 1090.0), _proc(1040.0, 1060.0)]
    orig = [_orig(1010.0, 1030.0), _orig(1020.0, 1090.0), _orig(1040.0, 1060.0)]
    *_rest, candidates = _gate(proc, orig, [hold])
    assert [(s['start'], s['end']) for s, _h in candidates] == [(1020.0, 1090.0)]


def test_released_hold_gives_no_second_candidate():
    hold = _hold(1000.0, 1100.0)
    hold['pass2_reviewed_release'] = {'start': 1040.0, 'end': 1060.0}
    *_rest, candidates = _gate([_proc(1040.0, 1060.0)], [_orig(1040.0, 1060.0)], [hold])
    assert candidates == []


def test_resurrect_band_finding_over_a_hold_is_never_cut(monkeypatch):
    hold = _hold(1000.0, 1100.0)
    proc, orig = [_proc(1040.0, 1060.0, 0.7)], [_orig(1040.0, 1060.0, 0.7)]
    cut, ui, held, _n, _c = _gate(proc, orig, [hold])

    assert split_resurrection_pool(orig, ui, 0.8) == []
    result = ReviewResult(verdicts=[ReviewVerdict(
        pool='resurrection', pass_num=2, verdict='resurrect',
        original_start=1040.0, original_end=1060.0, reasoning='Acme read')])
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage',
                        lambda *a, **k: None)
    monkeypatch.setattr(processing, 'split_resurrection_pool', lambda *a, **k: orig)
    monkeypatch.setattr(processing, '_build_reviewer',
                        lambda db, det: SimpleNamespace(review=lambda **kw: result))
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model')
    processing._apply_pass2_reviewer(_ctx(), cut, ui, held, proc, orig, [], 0.8)

    assert cut == [] and ui == []


# ---------- Review: release only a confirmed subspan ----------

def _candidate(hold_span=(1000.0, 1100.0), sub=(1040.0, 1060.0), reason=NO_SPLICE):
    hold = _hold(*hold_span, reason)
    *_rest, candidates = _gate([_proc(*sub)], [_orig(*sub)], [hold])
    return candidates


def test_confirmed_subspan_is_released_and_filed_trimmed(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]

    released, calls = _review(monkeypatch, candidates,
                              [_verdict('confirmed', 1040.0, 1060.0)])

    assert released == 1
    [call] = calls
    assert [(a['start'], a['end']) for a in call['accepted_ads']] == [(1040.0, 1060.0)]
    assert call['resurrection_eligible'] == []
    assert call['pass_num'] == 2
    assert hold['pass2_reviewed_release'] == {'start': 1040.0, 'end': 1060.0}
    assert hold['pass2_corroborated'] is True
    assert is_pending_review(hold)

    db = _approval_db(monkeypatch)
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    kwargs = db.create_pattern_correction.call_args.kwargs
    assert kwargs['original_bounds'] == {'start': 1000.0, 'end': 1100.0}
    assert kwargs['corrected_bounds'] == {'start': 1040.0, 'end': 1060.0}


def test_inconclusive_hold_reason_is_releasable(monkeypatch):
    candidates = _candidate(reason=INCONCLUSIVE)
    released, _calls = _review(monkeypatch, candidates,
                               [_verdict('confirmed', 1040.0, 1060.0)])
    assert released == 1
    db = _approval_db(monkeypatch)
    assert processing._file_corroborated_hold_approvals(
        's', 'e', [candidates[0][1]],
        corrections=_user_corrections('s', 'e')) == 1
    assert db.create_pattern_correction.called


@pytest.mark.parametrize('verdict', [
    _verdict('reject', 1040.0, 1060.0, reasoning='Host conversation'),
    _verdict('inconclusive', 1040.0, 1060.0, inconclusive_hold=True),
    _verdict('failure', 1040.0, 1060.0, success=False),
    _verdict('adjust', 1040.0, 1060.0, adjusted=(1035.0, 1062.0), boundary_conflict=True),
    _verdict('confirmed', 1040.0, 1060.0, reasoning='This is not an ad, it is host conversation'),
    # Leaves the hold without covering the subspan's start.
    _verdict('adjust', 1040.0, 1060.0, adjusted=(1045.0, 1150.0)),
])
def test_unsuccessful_review_keeps_the_whole_hold(monkeypatch, verdict):
    candidates = _candidate()
    hold = candidates[0][1]
    before = dict(hold)

    released, _calls = _review(monkeypatch, candidates, [verdict])

    assert released == 0
    record = hold.pop('pass2_hold_review')
    assert record['verdict'] == verdict.verdict and record['span'] == [1040.0, 1060.0]
    assert hold == before
    db = _approval_db(monkeypatch)
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 0
    db.create_pattern_correction.assert_not_called()


def test_adjust_inside_the_hold_releases_the_adjusted_span(monkeypatch):
    candidates = _candidate()
    released, _calls = _review(monkeypatch, candidates, [
        _verdict('adjust', 1040.0, 1060.0, adjusted=(1036.0, 1064.0))])
    assert released == 1
    assert candidates[0][1]['pass2_reviewed_release'] == {'start': 1036.0, 'end': 1064.0}


def test_adjust_into_another_hold_keeps_the_whole_hold(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    other = _hold(1062.0, 1070.0, 'max_duration')
    protection = processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[],
        holds=[hold, other], pass1_cuts=[])
    released, _calls = _review(monkeypatch, candidates, [
        _verdict('adjust', 1040.0, 1060.0, adjusted=(1040.0, 1065.0))],
        protection=protection)
    assert released == 0
    assert 'pass2_reviewed_release' not in hold


def test_confirm_over_a_gated_pass2_hold_keeps_the_whole_hold(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    pass2_hold = _hold(1050.0, 1055.0, 'verification_miss')
    protection = processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[],
        holds=[hold, pass2_hold], pass1_cuts=[])
    released, _calls = _review(monkeypatch, candidates,
                               [_verdict('confirmed', 1040.0, 1060.0)],
                               protection=protection)
    assert released == 0
    assert 'pass2_reviewed_release' not in hold


def test_inside_adjust_returns_no_outside_pieces(monkeypatch):
    candidates = _candidate()
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1040.0, 1060.0, adjusted=(1036.0, 1064.0))])
    assert released == 1 and pairs == []


def test_covering_adjust_releases_the_hold_part_and_returns_the_outside_piece(monkeypatch):
    cuts = [{'start': 100.0, 'end': 200.0}]
    candidates = _candidate(sub=(1060.0, 1080.0))
    hold = candidates[0][1]
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1060.0, 1080.0, adjusted=(1010.0, 1110.0),
                 reasoning='Acme read runs past the hold')], pass1_cuts=cuts)

    assert released == 1
    assert hold['pass2_reviewed_release'] == {'start': 1010.0, 'end': 1100.0}
    assert hold['pass2_released_spans'] == [{'start': 1010.0, 'end': 1100.0}]
    [(proc, orig)] = pairs
    assert (orig['start'], orig['end']) == (1100.0, 1110.0)
    beep = get_replacement_duration()
    assert (proc['start'], proc['end']) == (adjust_timestamp(1100.0, cuts, beep),
                                            adjust_timestamp(1110.0, cuts, beep))
    for piece in (proc, orig):
        assert 'held_for_review' not in piece
        assert piece['source'] == 'reviewer'
        assert piece['reason'] == 'Acme read runs past the hold'
        assert piece['confidence'] == 0.95


@pytest.mark.parametrize('adjusted,keep', [
    ((1065.0, 1110.0), None),
    ((1010.0, 1110.0), (1104.0, 1108.0)),
])
def test_covering_adjust_that_misses_the_subspan_or_meets_a_keep_is_not_released(
        monkeypatch, adjusted, keep):
    candidates = _candidate(sub=(1060.0, 1080.0))
    hold = candidates[0][1]
    protection = processing.build_protection(
        kept=[{'start': keep[0], 'end': keep[1]}] if keep else [], category_kept=[],
        user_trims=[], fp_corrections=[], holds=[hold], pass1_cuts=[])
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1060.0, 1080.0, adjusted=adjusted)], protection=protection)
    assert (released, pairs) == (0, [])
    assert 'pass2_reviewed_release' not in hold


def test_covering_adjust_into_another_hold_is_not_released(monkeypatch):
    candidates = _candidate(sub=(1060.0, 1080.0))
    hold = candidates[0][1]
    other = _hold(1105.0, 1150.0, 'max_duration')
    protection = processing.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[],
        holds=[hold, other], pass1_cuts=[])
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1060.0, 1080.0, adjusted=(1010.0, 1110.0))], protection=protection)
    assert (released, pairs) == (0, [])


def test_outside_piece_already_cut_or_found_gives_no_pair(monkeypatch):
    candidates = _candidate(sub=(1060.0, 1080.0))
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1060.0, 1080.0, adjusted=(1010.0, 1130.0))],
        pass1_cuts=[{'start': 1100.0, 'end': 1115.0}],
        covered=[{'start': 1112.0, 'end': 1140.0}])
    assert released == 1 and pairs == []


def test_short_outside_piece_is_dropped_with_a_reason(monkeypatch):
    ledger = Pass2Ledger()
    candidates = _candidate(sub=(1060.0, 1080.0))
    released, pairs, _calls = _review_pairs(monkeypatch, candidates, [
        _verdict('adjust', 1060.0, 1080.0, adjusted=(1010.0, 1104.0))], ledger=ledger)
    assert released == 1 and pairs == []
    stats = {}
    ledger.emit(run_stats=stats)
    assert stats['pass2_outcomes'] == {'dropped:short_fragment': 1}


@pytest.mark.parametrize('verdict,expected', [
    (_verdict('confirmed', 1060.0, 1080.0), ((1060.0, 1080.0), [])),
    (_verdict('adjust', 1060.0, 1080.0, adjusted=(1050.0, 1090.0)), ((1050.0, 1090.0), [])),
    (_verdict('adjust', 1060.0, 1080.0, adjusted=(990.0, 1110.0)),
     ((1000.0, 1100.0), [(990.0, 1000.0), (1100.0, 1110.0)])),
    (_verdict('adjust', 1060.0, 1080.0, adjusted=(1070.0, 1110.0)), None),
    (_verdict('adjust', 1060.0, 1080.0, adjusted=(1100.0, 1110.0)), None),
    (_verdict('reject', 1060.0, 1080.0), None),
    (_verdict('adjust', 1060.0, 1080.0), None),
    # A released part under MIN_AD_DURATION.
    (_verdict('adjust', 1095.0, 1099.0, adjusted=(1094.0, 1110.0)), None),
])
def test_released_span_by_verdict(verdict, expected):
    hold = _hold(1000.0, 1100.0)
    sub = {'start': verdict.original_start, 'end': verdict.original_end}
    got = processing._released_span(verdict, sub, hold, [])
    if expected is None:
        assert got is None
    else:
        span, outside = got
        assert ((span['start'], span['end']), outside) == expected


def test_review_disabled_keeps_the_hold(monkeypatch):
    candidates = _candidate()
    released, calls = _review(monkeypatch, candidates,
                              [_verdict('confirmed', 1040.0, 1060.0)], enabled=False)
    assert released == 0 and calls == []
    assert 'pass2_reviewed_release' not in candidates[0][1]


def test_repeated_detection_files_one_confirm(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    _review(monkeypatch, candidates, [_verdict('confirmed', 1040.0, 1060.0)])
    filed = {'start': 1000.0, 'end': 1100.0, 'correction_type': 'confirm',
             'auto_filed': True, 'hold_reason': hold['hold_reason'],
             'confirmed_span': {'start': 1040.0, 'end': 1060.0}}
    db = _approval_db(monkeypatch, confirmed=[filed])

    *_rest, again = _gate([_proc(1040.0, 1060.0)], [_orig(1040.0, 1060.0)], [hold])
    assert again == []
    assert processing._file_corroborated_hold_approvals('s', 'e', [hold], corrections=_user_corrections('s', 'e')) == 1
    db.create_pattern_correction.assert_not_called()


# ---------- Recut: auto-filed confirm keeps the remainder held ----------

@pytest.mark.parametrize('reason', [NO_SPLICE, 'estimated_pattern_bounds'])
def test_auto_filed_confirm_cuts_the_subspan_and_holds_the_remainders(reason):
    hold = _hold(1000.0, 1100.0, reason)
    hold.update(confidence=0.95, reason='Acme sponsor read', detection_stage='claude',
                pass2_corroborated=True,
                pass2_corroborated_span={'start': 1040.0, 'end': 1060.0},
                pass2_hold_review={'span': [1040.0, 1060.0], 'verdict': 'inconclusive',
                                   'reason': 'timeout'})
    confirm = _release_confirm((1000.0, 1100.0), (1040.0, 1060.0), reason=None)

    ads = _recut_validate([hold], [confirm])

    spans = {(a['start'], a['end']): a for a in ads}
    assert set(spans) == {(1000.0, 1040.0), (1040.0, 1060.0), (1060.0, 1100.0)}
    assert spans[(1040.0, 1060.0)]['validation']['decision'] == Decision.ACCEPT.value
    for key in ((1000.0, 1040.0), (1060.0, 1100.0)):
        rest = spans[key]
        assert rest['validation']['decision'] == Decision.REVIEW.value
        assert rest['held_for_review'] is True
        assert rest['hold_reason'] == reason
        assert rest['pass2_hold_remainder'] is True
        assert 'pass2_corroborated' not in rest
        assert 'pass2_hold_review' not in rest

    # A second recut over the saved result keeps the same three markers.
    saved = []
    for a in ads:
        a = {k: v for k, v in a.items() if k != 'validation'}
        a['was_cut'] = (a['start'], a['end']) == (1040.0, 1060.0)
        saved.append(a)
    again = _recut_validate(saved, [confirm])
    assert sorted((a['start'], a['end'], bool(a.get('held_for_review'))) for a in again) == [
        (1000.0, 1040.0, True), (1040.0, 1060.0, False), (1060.0, 1100.0, True)]


def test_fresh_detection_matching_an_auto_confirm_keeps_remainders_held():
    fresh = {'start': 1000.0, 'end': 1100.0, 'confidence': 0.95,
             'reason': 'Acme sponsor read', 'detection_stage': 'claude'}
    confirm = _release_confirm((1000.0, 1100.0), (1040.0, 1060.0), reason=None)
    confirm['hold_reason'] = INCONCLUSIVE

    ads = _recut_validate([fresh], [confirm])

    assert sorted((a['start'], a['end'], a['validation']['decision'],
                   a.get('hold_reason')) for a in ads) == [
        (1000.0, 1040.0, 'REVIEW', INCONCLUSIVE),
        (1040.0, 1060.0, 'ACCEPT', None),
        (1060.0, 1100.0, 'REVIEW', INCONCLUSIVE)]


def test_user_trimmed_confirm_still_keeps_the_trimmed_audio_unheld():
    hold = _hold(1000.0, 1100.0)
    hold.update(confidence=0.95, reason='Acme sponsor read', detection_stage='claude')
    confirm = _release_confirm((1000.0, 1100.0), (1040.0, 1060.0), reason=None)
    confirm.pop('auto_filed')

    ads = _recut_validate([hold], [confirm])

    assert not any(a.get('pass2_hold_remainder') for a in ads)


class _AbstainError(Exception):
    body = {'error': {'reason': 'insufficient_evidence', 'stage': 'evidence',
                      'score': 0.41, 'threshold': 0.6}}


def test_inconclusive_hold_review_records_the_reason(monkeypatch, caplog):
    candidates = _candidate()
    hold = candidates[0][1]
    reason = _review_inconclusive_reason(_AbstainError())
    with caplog.at_level('INFO', logger='podcast.audio'):
        released, _calls = _review(monkeypatch, candidates, [
            _verdict('inconclusive', 1040.0, 1060.0, reasoning=reason)])
    assert released == 0
    line = next(r.getMessage() for r in caplog.records
                if 'returned inconclusive' in r.getMessage())
    assert reason in line
    assert 'reviewer_reasoning' not in hold
    assert hold['pass2_hold_review'] == {
        'span': [1040.0, 1060.0], 'verdict': 'inconclusive', 'reason': reason}
    # The hold stays a plain hold: no reviewer verdict or source is stamped.
    assert 'reviewer_verdict' not in hold and 'source' not in hold
    assert 'pass2_reviewed_release' not in hold
    assert is_pending_review(hold)
    assert not is_reviewer_rejected(hold)
    assert not reviewer_hold_stands(hold, [])


def test_confirmed_hold_review_records_no_inconclusive_reason(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    released, _calls = _review(monkeypatch, candidates,
                               [_verdict('confirmed', 1040.0, 1060.0)])
    assert released == 1
    assert hold['pass2_reviewed_release'] == {'start': 1040.0, 'end': 1060.0}
    assert 'pass2_hold_review' not in hold and 'reviewer_reasoning' not in hold


def test_release_clears_a_stale_hold_review(monkeypatch):
    candidates = _candidate()
    hold = candidates[0][1]
    hold['pass2_hold_review'] = {'span': [1040.0, 1060.0], 'verdict': 'inconclusive',
                                 'reason': 'Reviewer abstained.'}
    released, _calls = _review(monkeypatch, candidates,
                               [_verdict('confirmed', 1040.0, 1060.0)])
    assert released == 1
    assert 'pass2_hold_review' not in hold


def test_hold_review_keeps_the_pass1_reviewer_reasoning(monkeypatch):
    hold = dict(_hold(1000.0, 1100.0, INCONCLUSIVE),
                reviewer_reasoning='Reviewer abstained: transcript gap.')
    candidates = [(_orig(1040.0, 1060.0), hold)]
    _review(monkeypatch, candidates, [_verdict(
        'reject', 1040.0, 1060.0, reasoning='Host conversation')])
    assert hold['reviewer_reasoning'] == 'Reviewer abstained: transcript gap.'
    assert hold['pass2_hold_review']['reason'] == 'Host conversation'


def test_recut_after_inconclusive_hold_review_keeps_the_hold_pending(monkeypatch):
    hold = dict(_hold(1000.0, 1100.0, 'max_duration'), confidence=0.95, reason='sponsor read')
    candidates = [(_orig(1040.0, 1060.0), hold)]
    _review(monkeypatch, candidates, [_verdict(
        'inconclusive', 1040.0, 1060.0,
        reasoning=_review_inconclusive_reason(_AbstainError()))])
    stored = {k: v for k, v in hold.items() if not k.startswith('_')}

    db = MagicMock()
    db.get_episode.return_value = {'ad_markers_json': json.dumps([stored])}
    db.get_episode_corrections.return_value = []
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = []
    db.get_podcast_by_slug.return_value = {'id': 42}
    db.get_podcast_cue_settings_overrides.return_value = {'max_ad_duration_override': 60.0}
    db.get_episode_audio_analysis.return_value = None
    db.get_episode_dai_differential.return_value = None
    db.resolve_segment_actions.return_value = {}
    db.get_setting.return_value = None
    db.get_setting_bool.side_effect = lambda k, **kw: kw.get('default', False)
    db.get_setting_float.side_effect = lambda k, default=None: default
    monkeypatch.setattr(processing, 'db', db)

    ads_to_remove, all_ads, _keep, rejects = (
        processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80, corrections=_user_corrections('slug', 'ep')))
    assert ads_to_remove == [] and rejects == []
    [after] = all_ads
    assert is_pending_review(after)
    assert after['hold_reason'] == 'max_duration'
    assert 'reviewer_verdict' not in after and 'source' not in after


def test_a_nan_word_end_does_not_hide_later_words():
    segments = [{'words': [{'start': 1.0, 'end': float('nan')},
                           {'start': 5.0, 'end': 6.0}, {'start': 9.0, 'end': 10.0}]}]
    edges = WordEdges(segments)
    for value, edge in ((5.5, 'start'), (9.5, 'end'), (1.5, 'start'), (7.0, 'end')):
        assert edges.inside(value, edge) == _linear_word_edge(segments, value, edge)
    assert edges.inside(5.5, 'start') == 6.0


# ---------- Verification pass: a covering adjust releases the hold part ----------

def _drive_covering(holds, findings, verdicts, **kwargs):
    seen = {}

    def pass2_reviewer(ctx, cut, *args, **kw):
        seen['cut'] = _spans(cut)

    run = drive_verification_pass(findings, holds=holds, pass2_reviewer=pass2_reviewer,
                                  hold_verdicts=verdicts, **kwargs)
    run.reviewer = seen
    return run


def _filed_confirms(monkeypatch, holds):
    db = _approval_db(monkeypatch)
    processing._file_corroborated_hold_approvals(
        's', 'e', holds, corrections=_user_corrections('s', 'e'))
    return [c.kwargs for c in db.create_pattern_correction.call_args_list]


def test_covering_adjust_releases_the_hold_part_and_cuts_the_outside_piece(monkeypatch):
    # Production shape: the review moved a held read's edges past the hold's end.
    hold = dict(_hold(609.0, 680.8), confidence=0.95, reason='Acme sponsor read',
                detection_stage='claude')
    run = _drive_covering([hold], [(661.8, 679.2, 0.95)], [
        _verdict('adjust', 661.8, 679.2, adjusted=(615.0, 692.0))])

    assert run.hold_reviews == [[(661.8, 679.2)]]
    assert hold['pass2_reviewed_release'] == {'start': 615.0, 'end': 680.8}
    assert hold['pass2_released_spans'] == [{'start': 615.0, 'end': 680.8}]
    assert run.output[7] == 1
    assert is_pending_review(hold)
    # The outside piece reached the pass-2 reviewer and the render.
    assert run.reviewer['cut'] == [(680.8, 692.0)]
    assert _spans(run.output[1]) == [(680.8, 692.0)]
    assert run.output[1][0]['was_cut'] is True
    assert (680.8, 692.0) in _spans(run.rendered['requested'])
    assert run.stats['pass2_outcomes'] == {'covered:pass1_hold': 1, 'cut': 1}

    [filed] = _filed_confirms(monkeypatch, [hold])
    assert filed['original_bounds'] == {'start': 609.0, 'end': 680.8}
    assert filed['corrected_bounds'] == {'start': 615.0, 'end': 680.8}
    # 609.0-615.0 stays uncut; a remainder under MIN_AD_DURATION gets no held marker.
    ads = _recut_validate([hold], [_release_confirm((609.0, 680.8), (615.0, 680.8))])
    assert [(a['start'], a['end'], a['validation']['decision']) for a in ads] == [
        (615.0, 680.8, Decision.ACCEPT.value)]
    ads = _recut_validate([dict(hold, start=600.0)],
                          [_release_confirm((600.0, 680.8), (615.0, 680.8))])
    assert sorted((a['start'], a['end'], a['validation']['decision']) for a in ads) == [
        (600.0, 615.0, Decision.REVIEW.value), (615.0, 680.8, Decision.ACCEPT.value)]


def test_hold_review_runs_before_the_pass2_reviewer():
    hold = _hold(609.0, 680.8)
    order = []
    run = drive_verification_pass(
        [(661.8, 679.2, 0.95)], holds=[hold],
        pass2_reviewer=lambda *a, **k: order.append(('reviewer', 'pass2_reviewed_release' in hold)),
        hold_verdicts=[_verdict('adjust', 661.8, 679.2, adjusted=(615.0, 692.0))])
    assert run.hold_reviews and order == [('reviewer', True)]


def test_covering_adjust_that_misses_the_subspan_keeps_the_hold_whole():
    hold = _hold(609.0, 680.8)
    run = _drive_covering([hold], [(661.8, 679.2, 0.95)], [
        _verdict('adjust', 661.8, 679.2, adjusted=(665.0, 692.0))])
    assert 'pass2_reviewed_release' not in hold
    assert hold['pass2_hold_review']['verdict'] == 'adjust'
    assert run.output[1] == [] and run.rendered == {}


@pytest.mark.parametrize('barrier', ['kept', 'fp', 'trims'])
def test_covering_adjust_across_a_hard_barrier_is_not_released(barrier):
    hold = _hold(609.0, 680.8)
    run = _drive_covering([hold], [(661.8, 679.2, 0.95)], [
        _verdict('adjust', 661.8, 679.2, adjusted=(615.0, 692.0))],
        **{barrier: [{'start': 685.0, 'end': 690.0, 'action_applied': 'keep'}]})
    assert 'pass2_reviewed_release' not in hold
    assert run.output[1] == [] and run.rendered == {}


def test_short_outside_piece_is_dropped_and_the_hold_part_released():
    hold = _hold(609.0, 680.8)
    run = _drive_covering([hold], [(661.8, 679.2, 0.95)], [
        _verdict('adjust', 661.8, 679.2, adjusted=(615.0, 685.0))])
    assert hold['pass2_reviewed_release'] == {'start': 615.0, 'end': 680.8}
    assert run.output[1] == [] and run.rendered == {}
    assert run.stats['pass2_outcomes'] == {
        'covered:pass1_hold': 1, 'dropped:short_fragment': 1}


def test_two_covering_adjusts_on_one_hold_both_release(monkeypatch):
    hold = dict(_hold(1000.0, 1100.0), confidence=0.95, reason='Acme sponsor read',
                detection_stage='claude')
    run = _drive_covering([hold], [(1010.0, 1025.0, 0.95), (1070.0, 1090.0, 0.95)], [
        _verdict('adjust', 1010.0, 1025.0, adjusted=(988.0, 1030.0)),
        _verdict('adjust', 1070.0, 1090.0, adjusted=(1060.0, 1112.0))])

    assert hold['pass2_released_spans'] == [
        {'start': 1000.0, 'end': 1030.0}, {'start': 1060.0, 'end': 1100.0}]
    assert _spans(run.output[1]) == [(988.0, 1000.0), (1100.0, 1112.0)]
    requested = _spans(run.rendered['requested'])
    assert (988.0, 1000.0) in requested and (1100.0, 1112.0) in requested

    filed = _filed_confirms(monkeypatch, [hold])
    assert [f['corrected_bounds'] for f in filed] == [
        {'start': 1000.0, 'end': 1030.0}, {'start': 1060.0, 'end': 1100.0}]
    confirms = [_release_confirm((1000.0, 1100.0), (1060.0, 1100.0)),
                _release_confirm((1000.0, 1100.0), (1000.0, 1030.0))]
    ads = _recut_validate([hold], confirms)
    assert sorted((a['start'], a['end'], a['validation']['decision']) for a in ads) == [
        (1000.0, 1030.0, Decision.ACCEPT.value), (1030.0, 1060.0, Decision.REVIEW.value),
        (1060.0, 1100.0, Decision.ACCEPT.value)]


def test_outside_pieces_of_two_holds_become_one_candidate():
    holds = [_hold(1000.0, 1100.0), _hold(1120.0, 1200.0)]
    run = _drive_covering(holds, [(1060.0, 1080.0, 0.95), (1140.0, 1160.0, 0.95)], [
        _verdict('adjust', 1060.0, 1080.0, adjusted=(1010.0, 1115.0)),
        _verdict('adjust', 1140.0, 1160.0, adjusted=(1105.0, 1190.0))])

    assert [h['pass2_reviewed_release'] for h in holds] == [
        {'start': 1010.0, 'end': 1100.0}, {'start': 1120.0, 'end': 1190.0}]
    assert run.reviewer['cut'] == [(1100.0, 1120.0)]
    assert _spans(run.output[1]) == [(1100.0, 1120.0)]
    assert run.stats['pass2_outcomes'] == {'covered:pass1_hold': 2, 'cut': 1}


def test_transcript_differential_hold_is_reviewed_release_only():
    assert HOLD_REASON_TRANSCRIPT_DIFFERENTIAL in PASS2_REVIEWED_RELEASE_HOLD_REASONS
    assert HOLD_REASON_TRANSCRIPT_DIFFERENTIAL not in PASS2_AUTOAPPROVE_HOLD_REASONS

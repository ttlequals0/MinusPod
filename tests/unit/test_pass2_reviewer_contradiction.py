"""Pass-2 reviewer contradiction holds (regression R1).

_apply_pass2_reviewer used to iterate only result.verdicts: an ad the
reviewer held by contradiction kept its verdict ('confirmed' or 'adjust'),
fell into the stamp/coerce branches, and STAYED in v_ads_to_cut -- the full
span cut silently with no pending-review entry. A held ad must divert out of
the cut list into v_ads_held as an original-coordinate pending marker with
the same shape pass-1 contradiction holds take.
"""
import os
import sys
import tempfile
from types import SimpleNamespace

_test_data_dir = tempfile.mkdtemp(prefix='pass2_contradiction_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('MINUSPOD_DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from main_app import processing
from ad_reviewer import ReviewResult, ReviewVerdict, log_contradiction_event
from config import (HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT,
                    HOLD_REASON_REVIEWER_CONTRADICTION, is_pending_review)

CONTRADICTING = 'This span is not an ad, it is host conversation'
AFFIRMING = 'Confirmed sponsor read for BetterHelp'


def _ctx():
    return SimpleNamespace(
        slug='s', episode_id='e', podcast_name='Pod', episode_title='Ep',
        podcast_description='', episode_description='', podcast_id=1,
    )


def _verdict(verdict, start, end, reasoning, adjusted=None, pool='accepted',
             boundary_conflict=False):
    return ReviewVerdict(
        pool=pool, pass_num=2, verdict=verdict,
        original_start=start, original_end=end,
        adjusted_start=adjusted[0] if adjusted else None,
        adjusted_end=adjusted[1] if adjusted else None,
        reasoning=reasoning, confidence=0.9, model_used='test-model',
        boundary_conflict=boundary_conflict,
    )


def _pair(o_start, o_end, p_start, p_end):
    orig = {'start': o_start, 'end': o_end, 'confidence': 0.95, 'was_cut': True}
    proc = {'start': p_start, 'end': p_end, 'confidence': 0.95, 'was_cut': True}
    return orig, proc


def _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               ads_processed, ads_original, resurrection_eligible=None, **kwargs):
    result = ReviewResult(verdicts=list(verdicts))
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage',
                        lambda *a, **k: None)
    monkeypatch.setattr(processing, 'split_resurrection_pool',
                        lambda *a, **k: resurrection_eligible or [])
    def _review(**kw):
        # Stands in for AdReviewer.review's accepted-pool loop, the single
        # site that emits contradiction telemetry.
        for verdict in result.verdicts:
            log_contradiction_event(
                verdict, model=verdict.model_used,
                slug=kw['episode_meta'].get('slug'),
                episode_id=kw['episode_meta'].get('episode_id'))
        return result

    stub = SimpleNamespace(review=_review)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, det: stub)
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model', raising=False)
    processing._apply_pass2_reviewer(
        _ctx(), v_ads_to_cut, v_ads_for_ui, v_ads_held,
        ads_processed, ads_original, [], 0.80, **kwargs,
    )


def test_pass2_contradiction_confirmed_is_held_not_cut(monkeypatch):
    o1, p1 = _pair(100.0, 160.0, 50.0, 110.0)
    o2, p2 = _pair(300.0, 360.0, 250.0, 310.0)
    v_ads_to_cut = [p1, p2]
    v_ads_for_ui = [o1, o2]
    v_ads_held = []
    verdicts = [
        _verdict('confirmed', 100.0, 160.0, CONTRADICTING),
        _verdict('confirmed', 300.0, 360.0, AFFIRMING),
    ]
    _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               [p1, p2], [o1, o2])

    # The held ad left the cut list and the UI list; the marker went to held.
    assert p1 not in v_ads_to_cut
    assert o1 not in v_ads_for_ui
    assert v_ads_held == [o1]
    # Pass-1 hold shape, in ORIGINAL coordinates, bounds untouched.
    assert o1['start'] == 100.0 and o1['end'] == 160.0
    assert o1['was_cut'] is False
    assert o1['held_for_review'] is True
    assert o1['hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION
    assert o1['reviewer_contradiction'] is True
    assert o1['source'] == 'reviewer'
    assert is_pending_review(o1), "held marker must count as pending review"
    assert p1['was_cut'] is False


def test_pass2_contradiction_guard_logs_once_with_context(monkeypatch, caplog):
    # The pass-2 gate and _apply_reviewer_verdict_to_ad both evaluate the
    # same verdict object; neither may emit, so the guard-fired line stays at
    # exactly one, carrying slug/episode_id/model/boundaries for Loki counting.
    o1, p1 = _pair(100.0, 160.0, 50.0, 110.0)
    v_ads_to_cut = [p1]
    v_ads_for_ui = [o1]
    v_ads_held = []
    verdicts = [_verdict('confirmed', 100.0, 160.0, CONTRADICTING)]
    with caplog.at_level('INFO', logger='ad_reviewer'):
        _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui,
                   v_ads_held, [p1], [o1])
    fired = [line for line in caplog.text.splitlines()
             if 'reviewer_contradiction_guard_fired' in line]
    assert len(fired) == 1, f"expected exactly one guard-fired log, got {fired}"
    assert 'model=test-model' in fired[0]
    assert 'slug=s' in fired[0]
    assert 'episode_id=e' in fired[0]
    assert 'start=100.0' in fired[0] and 'end=160.0' in fired[0]


def test_pass2_contradiction_adjust_is_held_not_coerced_to_cut(monkeypatch):
    # An adjust verdict whose reasoning denies the ad must hold, never reach
    # the adjust->confirmed coercion (which would keep the full span cut).
    # Its adjusted bounds surface as the reviewer's proposed one-tap trim.
    o1, p1 = _pair(100.0, 160.0, 50.0, 110.0)
    v_ads_to_cut = [p1]
    v_ads_for_ui = [o1]
    v_ads_held = []
    verdicts = [
        _verdict('adjust', 100.0, 160.0, CONTRADICTING,
                 adjusted=(110.0, 150.0)),
    ]
    _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               [p1], [o1])

    assert v_ads_to_cut == []
    assert v_ads_for_ui == []
    assert v_ads_held == [o1]
    assert o1['held_for_review'] is True
    assert o1['was_cut'] is False
    # Bounds stay at pass-2 originals; the trim is only a proposal.
    assert o1['start'] == 100.0 and o1['end'] == 160.0
    assert o1['reviewer_proposed_start'] == 110.0
    assert o1['reviewer_proposed_end'] == 150.0
    assert o1['reviewer_verdict'] == 'adjust'


def test_pass2_boundary_conflict_holds_original_and_raw_proposal(monkeypatch):
    original, processed = _pair(100.0, 200.0, 50.0, 150.0)
    original.update(merged_distinct_ads=True,
                    merged_protected_start=100.0,
                    merged_protected_end=200.0)
    cuts, ui, held = [processed], [original], []
    verdict = _verdict('adjust', 100.0, 200.0, AFFIRMING,
                       adjusted=(120.0, 180.0), boundary_conflict=True)

    _run_pass2(monkeypatch, [verdict], cuts, ui, held, [processed], [original])

    assert cuts == []
    assert ui == []
    assert held == [original]
    assert (original['start'], original['end']) == (100.0, 200.0)
    assert original['was_cut'] is False
    assert original['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT
    assert (original['reviewer_proposed_start'], original['reviewer_proposed_end']) == (120.0, 180.0)


def test_pass2_resurrection_boundary_conflict_persists_without_ui_twin(monkeypatch):
    original, processed = _pair(100.0, 200.0, 50.0, 150.0)
    cuts, ui, held = [], [], []
    verdict = _verdict('adjust', 100.0, 200.0, AFFIRMING,
                       adjusted=(120.0, 180.0), pool='resurrection',
                       boundary_conflict=True)

    _run_pass2(monkeypatch, [verdict], cuts, ui, held, [processed], [original],
               resurrection_eligible=[original])

    assert cuts == []
    assert held == [original]
    assert original['was_cut'] is False
    assert original['hold_reason'] == HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT


def test_pass2_boundary_conflict_blocks_later_adjustment(monkeypatch):
    held_original, held_processed = _pair(100.0, 200.0, 100.0, 200.0)
    adjusted_original, adjusted_processed = _pair(150.0, 220.0, 150.0, 220.0)
    cuts = [held_processed, adjusted_processed]
    ui = [held_original, adjusted_original]
    held = []
    verdicts = [
        _verdict('adjust', 100.0, 200.0, AFFIRMING,
                 adjusted=(120.0, 180.0), boundary_conflict=True),
        _verdict('adjust', 150.0, 220.0, AFFIRMING,
                 adjusted=(155.0, 215.0)),
    ]

    _run_pass2(monkeypatch, verdicts, cuts, ui, held,
               [held_processed, adjusted_processed],
               [held_original, adjusted_original])
    processing._hold_adjustments_crossing_final_holds(cuts, ui, held)

    assert cuts == []
    assert ui == []
    assert held == [held_original, adjusted_original]
    assert adjusted_original['hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION


def test_pass2_non_held_ads_unaffected_by_sibling_hold(monkeypatch):
    # Confirmed and rejected siblings keep their pre-fix behavior when one ad
    # in the batch holds.
    o1, p1 = _pair(100.0, 160.0, 50.0, 110.0)   # held
    o2, p2 = _pair(300.0, 360.0, 250.0, 310.0)  # confirmed, stays cut
    o3, p3 = _pair(500.0, 560.0, 450.0, 510.0)  # rejected, removed
    v_ads_to_cut = [p1, p2, p3]
    v_ads_for_ui = [o1, o2, o3]
    v_ads_held = []
    verdicts = [
        _verdict('confirmed', 100.0, 160.0, CONTRADICTING),
        _verdict('confirmed', 300.0, 360.0, AFFIRMING),
        _verdict('reject', 500.0, 560.0, 'not promotional'),
    ]
    _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               [p1, p2, p3], [o1, o2, o3])

    assert v_ads_to_cut == [p2]
    assert v_ads_for_ui == [o2]
    assert v_ads_held == [o1]
    assert o2['reviewer_verdict'] == 'confirmed'
    assert not o2.get('held_for_review')
    assert o3['was_cut'] is False
    assert not o3.get('held_for_review'), "reject is a reject, not a hold"


def test_pass2_no_contradiction_no_holds(monkeypatch):
    # Sanity: an affirming batch changes nothing.
    o1, p1 = _pair(100.0, 160.0, 50.0, 110.0)
    v_ads_to_cut = [p1]
    v_ads_for_ui = [o1]
    v_ads_held = []
    verdicts = [_verdict('confirmed', 100.0, 160.0, AFFIRMING)]
    _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               [p1], [o1])

    assert v_ads_to_cut == [p1]
    assert v_ads_for_ui == [o1]
    assert v_ads_held == []
    assert not o1.get('held_for_review')


def test_pass2_adjust_maps_only_surviving_audio(monkeypatch):
    original, processed = _pair(250.0, 310.0, 151.0, 211.0)
    cuts, ui, held = [processed], [original], []
    verdict = _verdict('adjust', 250.0, 310.0, AFFIRMING, adjusted=(260.0, 300.0))
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 1.0)

    _run_pass2(monkeypatch, [verdict], cuts, ui, held, [processed], [original],
               pass1_cuts=[{'start': 100.0, 'end': 200.0}])

    assert cuts == [processed]
    assert (processed['start'], processed['end']) == (161.0, 201.0)
    assert (original['start'], original['end']) == (260.0, 300.0)
    assert held == []


def test_pass2_adjust_inside_pass1_cut_is_dropped(monkeypatch):
    original, processed = _pair(120.0, 180.0, 10.0, 70.0)
    cuts, ui, held = [processed], [original], []
    verdict = _verdict('adjust', 120.0, 180.0, AFFIRMING, adjusted=(130.0, 170.0))

    _run_pass2(monkeypatch, [verdict], cuts, ui, held, [processed], [original],
               pass1_cuts=[{'start': 100.0, 'end': 200.0}])

    assert cuts == []
    assert ui == []
    assert held == []
    assert processed['was_cut'] is False


def test_pass2_adjust_crossing_protected_range_is_held(monkeypatch):
    original, processed = _pair(250.0, 310.0, 151.0, 211.0)
    cuts, ui, held = [processed], [original], []
    verdict = _verdict('adjust', 250.0, 310.0, AFFIRMING, adjusted=(240.0, 300.0))

    _run_pass2(monkeypatch, [verdict], cuts, ui, held, [processed], [original],
               pass1_cuts=[{'start': 100.0, 'end': 200.0}],
               protected_original_ranges=[{'start': 235.0, 'end': 245.0}])

    assert cuts == []
    assert ui == []
    assert held == [original]
    assert original['held_for_review'] is True
    assert original['reviewer_proposed_start'] == 240.0


def test_adjustment_is_reconciled_against_later_sibling_hold():
    original, processed = _pair(250.0, 310.0, 151.0, 211.0)
    original.update(
        start=240.0, end=300.0, reviewer_moved=True,
        reviewer_original_start=250.0, reviewer_original_end=310.0,
    )
    held = [{'start': 235.0, 'end': 245.0, 'held_for_review': True}]
    cuts, ui = [processed], [original]

    processing._hold_adjustments_crossing_final_holds(cuts, ui, held)

    assert cuts == []
    assert ui == []
    assert held[-1] is original
    assert (original['start'], original['end']) == (250.0, 310.0)
    assert original['reviewer_proposed_start'] == 240.0
    assert original['held_for_review'] is True


def test_adjustment_hold_reconciliation_repeats_for_hold_chain():
    first_original, first_processed = _pair(100.0, 160.0, 100.0, 160.0)
    second_original, second_processed = _pair(150.0, 210.0, 150.0, 210.0)
    first_original.update(
        reviewer_moved=True, reviewer_original_start=100.0,
        reviewer_original_end=160.0,
    )
    second_original.update(
        reviewer_moved=True, reviewer_original_start=150.0,
        reviewer_original_end=210.0,
    )
    held = [{'start': 200.0, 'end': 220.0, 'held_for_review': True}]
    cuts, ui = [first_processed, second_processed], [first_original, second_original]

    processing._hold_adjustments_crossing_final_holds(cuts, ui, held)

    assert cuts == []
    assert ui == []
    assert held[-2:] == [second_original, first_original]

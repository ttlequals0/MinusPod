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
from ad_reviewer import ReviewResult, ReviewVerdict
from config import HOLD_REASON_REVIEWER_CONTRADICTION, is_pending_review

CONTRADICTING = 'This span is not an ad, it is host conversation'
AFFIRMING = 'Confirmed sponsor read for BetterHelp'


def _ctx():
    return SimpleNamespace(
        slug='s', episode_id='e', podcast_name='Pod', episode_title='Ep',
        podcast_description='', episode_description='', podcast_id=1,
    )


def _verdict(verdict, start, end, reasoning, adjusted=None, pool='accepted'):
    return ReviewVerdict(
        pool=pool, pass_num=2, verdict=verdict,
        original_start=start, original_end=end,
        adjusted_start=adjusted[0] if adjusted else None,
        adjusted_end=adjusted[1] if adjusted else None,
        reasoning=reasoning, confidence=0.9, model_used='test-model',
    )


def _pair(o_start, o_end, p_start, p_end):
    orig = {'start': o_start, 'end': o_end, 'confidence': 0.95, 'was_cut': True}
    proc = {'start': p_start, 'end': p_end, 'confidence': 0.95, 'was_cut': True}
    return orig, proc


def _run_pass2(monkeypatch, verdicts, v_ads_to_cut, v_ads_for_ui, v_ads_held,
               ads_processed, ads_original):
    result = ReviewResult(verdicts=list(verdicts))
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage',
                        lambda *a, **k: None)
    monkeypatch.setattr(processing, 'split_resurrection_pool',
                        lambda *a, **k: [])
    stub = SimpleNamespace(review=lambda **kw: result)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, det: stub)
    monkeypatch.setattr(processing.ad_detector, 'get_verification_model',
                        lambda: 'test-model', raising=False)
    processing._apply_pass2_reviewer(
        _ctx(), v_ads_to_cut, v_ads_for_ui, v_ads_held,
        ads_processed, ads_original, [], 0.80,
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

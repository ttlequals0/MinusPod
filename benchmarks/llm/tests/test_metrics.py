import pytest

from benchmark.metrics import (
    BoundaryError,
    NoAdResult,
    boundary_error,
    compliance_score,
    iou,
    match_predictions,
    no_ad_score,
    schema_audit,
    trial_stdev,
)


def test_iou_full_overlap():
    assert iou((0, 100), (0, 100)) == 1.0


def test_iou_disjoint():
    assert iou((0, 50), (60, 100)) == 0.0


def test_iou_partial():
    # overlap=20, union=80 -> 0.25
    assert iou((10, 60), (40, 90)) == pytest.approx(0.25)


def test_iou_touching_boundary_no_overlap():
    assert iou((0, 50), (50, 100)) == 0.0


def test_match_predictions_perfect():
    preds = [(0, 30), (100, 130)]
    truths = [(0, 30), (100, 130)]
    r = match_predictions(preds, truths, threshold=0.5)
    assert r.true_positives == 2
    assert r.false_positives == 0
    assert r.false_negatives == 0
    assert r.f1 == 1.0


def test_match_predictions_one_miss():
    preds = [(0, 30)]
    truths = [(0, 30), (100, 130)]
    r = match_predictions(preds, truths, threshold=0.5)
    assert r.true_positives == 1
    assert r.false_negatives == 1
    assert r.recall == 0.5
    assert r.precision == 1.0


def test_match_predictions_one_false_positive():
    preds = [(0, 30), (200, 230)]
    truths = [(0, 30)]
    r = match_predictions(preds, truths, threshold=0.5)
    assert r.true_positives == 1
    assert r.false_positives == 1
    assert r.recall == 1.0
    assert r.precision == 0.5


def test_match_predictions_below_threshold():
    preds = [(0, 100)]
    truths = [(0, 20)]  # IoU = 20/100 = 0.2
    r = match_predictions(preds, truths, threshold=0.5)
    assert r.true_positives == 0
    assert r.false_positives == 1
    assert r.false_negatives == 1


def test_match_predictions_greedy_one_to_one():
    preds = [(0, 30), (10, 40)]
    truths = [(0, 30)]
    r = match_predictions(preds, truths, threshold=0.3)
    assert r.true_positives == 1
    assert r.false_positives == 1
    assert r.matches[0].iou == pytest.approx(1.0)


def test_boundary_error_returns_none_when_no_matches():
    assert boundary_error([], [], []) is None


def test_boundary_error_basic():
    preds = [(2.0, 28.0)]
    truths = [(0.0, 30.0)]
    r = match_predictions(preds, truths, threshold=0.3)
    assert r.true_positives == 1
    err = boundary_error(preds, truths, r.matches)
    assert err == BoundaryError(start_mae=2.0, end_mae=2.0)


def test_no_ad_pass():
    out = no_ad_score([[], [], []])
    assert out.false_positive_count == 0
    assert out.hallucinated_window_fraction == 0.0
    assert out.passed


def test_no_ad_fail():
    out = no_ad_score([[(0, 10)], [], [(50, 60), (70, 80)]])
    assert out.false_positive_count == 3
    assert out.hallucinated_window_fraction == pytest.approx(2 / 3)
    assert not out.passed


def test_no_ad_empty_input():
    out = no_ad_score([])
    assert out.passed
    assert out.hallucinated_window_fraction == 0.0


@pytest.mark.parametrize("method,expected", [
    ("json_array_direct", 1.0),
    ("markdown_code_block", 0.6),
    ("bracket_fallback", 0.2),
    (None, 0.0),
    ("unknown_method", 0.5),
])
def test_compliance_score(method, expected):
    assert compliance_score(method) == expected


def test_schema_audit_clean():
    ads = [{"start": 10.0, "end": 30.0, "confidence": 0.95, "reason": "x"}]
    v = schema_audit(ads)
    assert v.missing_required == 0
    assert v.wrong_type == 0
    assert v.extra_keys == 0


def test_schema_audit_missing_required():
    ads = [{"end": 30.0}]
    v = schema_audit(ads)
    assert v.missing_required == 1


def test_schema_audit_accepts_start_time_alias():
    ads = [{"start_time": 10.0, "end_time": 30.0}]
    v = schema_audit(ads)
    assert v.missing_required == 0


def test_schema_audit_wrong_type():
    ads = [{"start": "ten", "end": 30.0, "confidence": 1.5}]
    v = schema_audit(ads)
    assert v.wrong_type >= 1


def test_schema_audit_extra_key():
    ads = [{"start": 0.0, "end": 30.0, "frobnitz": "weird"}]
    v = schema_audit(ads)
    assert v.extra_keys == 1
    assert v.extra_key_names == ["frobnitz"]


def test_trial_stdev_single_value():
    assert trial_stdev([0.85]) == 0.0


def test_trial_stdev_multiple():
    val = trial_stdev([0.85, 0.87, 0.83])
    assert val > 0

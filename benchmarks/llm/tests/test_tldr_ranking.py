"""F0.5 headline metric, cross-episode CI, tier grouping, reliability flags."""
from __future__ import annotations

import math

from benchmark import report
from benchmark.metrics import AccuracyResult
from benchmark.report import ModelStats, _assign_tiers, _ci_half_width, _reliability_flags, _render_tldr, _sig_worse, _tier_label


def _mr(tp, fp, fn):
    return AccuracyResult(iou_threshold=0.5, true_positives=tp, false_positives=fp, false_negatives=fn, matches=[])


def test_fbeta_equals_f1_at_beta_1():
    r = _mr(8, 2, 4)
    assert math.isclose(r.fbeta(1.0), r.f1, rel_tol=1e-9)


def test_fbeta_half_favors_precision():
    # precision 0.8 > recall 0.667 -> F0.5 should sit above F1
    r = _mr(8, 2, 4)
    assert r.precision > r.recall
    assert r.fbeta(0.5) > r.f1


def test_fbeta_half_below_f1_when_recall_dominates():
    r = _mr(8, 4, 2)  # precision 0.667 < recall 0.8
    assert r.fbeta(0.5) < r.f1


def test_ci_half_width_zero_for_under_two_points():
    assert _ci_half_width([]) == 0.0
    assert _ci_half_width([0.8]) == 0.0


def test_ci_half_width_matches_t_formula():
    vals = [0.6, 0.8, 1.0]
    got = _ci_half_width(vals)
    expected = report._T_CRIT["two"][2] * 0.2 / math.sqrt(3)  # stdev of [.6,.8,1.0] = 0.2, df=2
    assert math.isclose(got, expected, rel_tol=1e-9)


def _epd(vals):
    return {f"e{i}": v for i, v in enumerate(vals)}


def test_tier_label_never_overflows_past_z():
    assert _tier_label(0) == "A"
    assert _tier_label(25) == "Z"
    assert _tier_label(26) == "AA"
    assert _tier_label(27) == "AB"
    # No tier index ever yields a markdown-breaking '|' or other punctuation.
    assert all(c.isalpha() for i in range(200) for c in _tier_label(i))


def test_sig_worse_false_when_trading_wins():
    # model wins some episodes, loses others -> mean diff ~0 -> not separable
    leader = _epd([0.8, 0.6, 0.9, 0.7, 0.85, 0.65])
    model = _epd([0.7, 0.7, 0.8, 0.8, 0.75, 0.75])
    assert _sig_worse(leader, model) is False


def test_sig_worse_true_when_consistently_lower():
    leader = _epd([0.80, 0.85, 0.90, 0.75, 0.82, 0.78])
    model = _epd([0.50, 0.55, 0.60, 0.45, 0.52, 0.48])
    assert _sig_worse(leader, model) is True


def test_sig_worse_false_for_too_few_shared_episodes():
    assert _sig_worse(_epd([0.8]), _epd([0.2])) is False


def _ms(model, eps):
    s = ModelStats(model=model)
    s.f05_per_episode = _epd(eps)
    s.avg_f05 = sum(eps) / len(eps)
    return s


def test_assign_tiers_paired_groups_close_separates_far():
    leader = _ms("leader", [0.80, 0.85, 0.90, 0.75, 0.82, 0.78])
    close = _ms("close", [0.79, 0.80, 0.91, 0.74, 0.80, 0.81])   # trades wins, ~tied
    far = _ms("far", [0.40, 0.45, 0.50, 0.38, 0.42, 0.41])       # consistently lower
    ranked = sorted([leader, close, far], key=lambda s: s.avg_f05, reverse=True)
    assert _assign_tiers(ranked) == ["A", "A", "B"]


def _stats(model, *, f05, eps, prec, rec, f1, cost, compliance, no_ad_ok=True):
    s = ModelStats(model=model)
    s.avg_f05 = f05
    s.f05_per_episode = {f"e{i}": v for i, v in enumerate(eps)}
    s.precision_per_episode = {f"e{i}": prec for i in range(len(eps))}
    s.recall_per_episode = {f"e{i}": rec for i in range(len(eps))}
    s.avg_precision = prec
    s.avg_recall = rec
    s.avg_f1 = f1
    s.total_episode_cost = cost
    s.json_compliance_mean = compliance
    s.no_ad_pass = {"nad": no_ad_ok}
    return s


def test_reliability_flags():
    clean = _stats("clean", f05=0.8, eps=[0.8], prec=0.8, rec=0.8, f1=0.8, cost=1.0, compliance=1.0)
    brittle = _stats("brittle", f05=0.8, eps=[0.8], prec=0.8, rec=0.8, f1=0.8, cost=1.0, compliance=0.6)
    fails_ctrl = _stats("fc", f05=0.8, eps=[0.8], prec=0.8, rec=0.8, f1=0.8, cost=1.0, compliance=1.0, no_ad_ok=False)
    assert _reliability_flags(clean) == ""
    assert "brittle JSON" in _reliability_flags(brittle)
    assert "fails no-ad control" in _reliability_flags(fails_ctrl)


class _Ep:
    class _T:
        is_no_ad_episode = False
    truth = _T()


def test_render_tldr_uses_f05_tiers_and_flags():
    stats = {
        "brittle-top": _stats("brittle-top", f05=0.82, eps=[0.80, 0.82, 0.84], prec=0.9, rec=0.7, f1=0.79, cost=1.2, compliance=0.60),
        "clean-2": _stats("clean-2", f05=0.81, eps=[0.79, 0.81, 0.83], prec=0.85, rec=0.78, f1=0.81, cost=1.1, compliance=1.0),
        "weak": _stats("weak", f05=0.40, eps=[0.38, 0.40, 0.42], prec=0.45, rec=0.35, f1=0.40, cost=0.0, compliance=0.5),
    }
    out = _render_tldr(stats, [_Ep(), _Ep()])
    assert "### Best Accuracy (F0.5 @ IoU >= 0.5)" in out
    assert "### Best Value (F0.5 per dollar)" in out
    assert "### Best Free-Tier (F0.5)" in out
    assert "| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 |" in out
    # brittle-top ranks first by F0.5 but carries the flag
    assert "brittle JSON" in out
    # the free-tier model (cost 0) appears in its own section
    assert "weak" in out.split("### Best Free-Tier")[1]

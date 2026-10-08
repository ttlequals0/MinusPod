"""Known-value checks for the paired t-test p-value math in report/aggregate.py."""
from __future__ import annotations

import math

from benchmark.report.aggregate import _betai, _paired_t_pvalue


def _p_from_t(t: float, df: int) -> float:
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def test_betai_matches_known_t_table_pvalues():
    assert math.isclose(_p_from_t(2.333, 1), 0.2578, abs_tol=1e-3)
    assert math.isclose(_p_from_t(2.0, 7), 0.0857, abs_tol=1e-3)
    assert math.isclose(_p_from_t(4.0, 10), 0.0025, abs_tol=1e-3)


def test_paired_t_pvalue_none_below_two_shared_episodes():
    assert _paired_t_pvalue({"e1": 1.0}, {"e1": 0.0}) is None
    assert _paired_t_pvalue({}, {}) is None


def test_paired_t_pvalue_identical_series_is_one():
    a = {"e1": 1.0, "e2": 1.0}
    assert _paired_t_pvalue(a, dict(a)) == 1.0


def test_paired_t_pvalue_two_shared_episodes_numeric():
    a = {"e1": 0.9, "e2": 0.6}
    b = {"e1": 0.5, "e2": 0.5}
    p = _paired_t_pvalue(a, b)
    assert p is not None
    assert math.isclose(p, 0.344, abs_tol=1e-3)

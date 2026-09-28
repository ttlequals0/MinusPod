"""Unit tests for the shared covering-confirm check."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator
from utils.markers import auto_confirm_releases, covering_confirm, explicit_override


def test_returns_newest_first_match():
    newest = {'start': 100.0, 'end': 160.0, 'id': 'newest'}
    older = {'start': 100.0, 'end': 160.0, 'id': 'older'}
    assert covering_confirm(100.0, 160.0, [newest, older]) is newest


def test_matches_on_confirmed_span_only():
    corr = {'start': 0.0, 'end': 10.0, 'confirmed_span': {'start': 100.0, 'end': 160.0}}
    assert covering_confirm(100.0, 160.0, [corr]) is corr


def test_matches_on_original_only():
    corr = {'start': 100.0, 'end': 160.0, 'confirmed_span': {'start': 0.0, 'end': 10.0}}
    assert covering_confirm(100.0, 160.0, [corr]) is corr


def test_skips_auto_filed_when_excluded():
    auto = {'start': 100.0, 'end': 160.0, 'auto_filed': True}
    assert covering_confirm(100.0, 160.0, [auto]) is auto
    assert covering_confirm(100.0, 160.0, [auto], where=lambda c: not c.get('auto_filed')) is None


def test_prefer_confirmed_span_ignores_a_stale_original():
    corr = {'start': 100.0, 'end': 160.0, 'confirmed_span': {'start': 0.0, 'end': 10.0}}
    assert covering_confirm(100.0, 160.0, [corr], prefer_confirmed_span=True) is None
    plain = {'start': 100.0, 'end': 160.0}
    assert covering_confirm(100.0, 160.0, [plain], prefer_confirmed_span=True) is plain


def test_auto_confirm_releases():
    auto = {'auto_filed': True, 'hold_reason': 'reviewer_contradiction'}
    assert auto_confirm_releases(auto, 'reviewer_contradiction')
    assert not auto_confirm_releases(auto, 'reviewer_boundary_conflict')
    assert auto_confirm_releases(auto, 'max_duration')
    assert not auto_confirm_releases({'hold_reason': 'max_duration'}, 'max_duration')


def test_where_filter():
    first = {'start': 100.0, 'end': 160.0, 'hold_reason': 'a'}
    second = {'start': 100.0, 'end': 160.0, 'hold_reason': 'b'}
    found = covering_confirm(100.0, 160.0, [first, second],
                             where=lambda c: c.get('hold_reason') == 'b')
    assert found is second


def test_zero_length_span_returns_none():
    corr = {'start': 100.0, 'end': 160.0}
    assert covering_confirm(120.0, 120.0, [corr]) is None
    assert covering_confirm(None, 120.0, [corr]) is None


_PARITY_CASES = [
    ((100.0, 160.0), [{'start': 100.0, 'end': 160.0}]),
    ((100.0, 160.0), [{'start': 100.0, 'end': 160.0, 'auto_filed': True}]),
    ((100.0, 160.0), [{'start': 0.0, 'end': 10.0,
                       'confirmed_span': {'start': 110.0, 'end': 160.0}}]),
    ((100.0, 160.0), [{'start': 100.0, 'end': 125.0}]),
    ((100.0, 160.0), [{'start': 100.0, 'end': 135.0}]),
    ((100.0, 160.0), [{'start': 300.0, 'end': 360.0}]),
    ((100.0, 160.0), []),
]


@pytest.mark.parametrize('span,confirmed', _PARITY_CASES)
def test_explicit_override_matches_validator(span, confirmed):
    validator = AdValidator(episode_duration=3600.0, confirmed_corrections=confirmed)
    marker = {'start': span[0], 'end': span[1]}
    assert explicit_override(marker, confirmed) == (
        validator._matching_confirmed(*span, skip_auto_filed=True) is not None)

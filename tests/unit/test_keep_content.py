"""Tests for keep-content inversion + safety gates (opt-in detection mode)."""
from ad_detector.keep_content import invert_content_to_ads

GATES = dict(
    edge_pad=1.5,
    min_gap=8.0,
    min_coverage=0.55,
    max_removed_fraction=0.45,
    min_ad_seconds=1.0,
    max_single_ad_fraction=0.5,
    max_single_ad_seconds=420.0,
)


def _ads(content, total, **over):
    ads, _info = invert_content_to_ads(content, total, **{**GATES, **over})
    return ads


def _info(content, total, **over):
    _ads_unused, info = invert_content_to_ads(content, total, **{**GATES, **over})
    return info


def test_basic_inversion_complements_content():
    # Content covers 0-50 and 80-100; the 30s gap survives edge-padding (grows
    # content to [0,51.5] and [78.5,100], gap 27s > 8s min_gap) -> one ad span.
    content = [{'start': 0, 'end': 50}, {'start': 80, 'end': 100}]
    ads = _ads(content, 100)
    assert ads is not None
    assert len(ads) == 1
    assert abs(ads[0]['start'] - 51.5) < 0.01
    assert abs(ads[0]['end'] - 78.5) < 0.01
    assert ads[0]['detection_stage'] == 'keep_content'


def test_small_gap_is_bridged_not_cut():
    # A 10s raw gap shrinks to 7s after edge-padding (< 8s min_gap) -> bridged,
    # nothing removed. This is the conservative-by-design behavior.
    content = [{'start': 0, 'end': 60}, {'start': 70, 'end': 100}]
    assert _ads(content, 100) == []


def test_coverage_gate_aborts_when_content_too_sparse():
    # Content covers only 40% -> below the 0.55 floor -> abort (None).
    content = [{'start': 0, 'end': 40}]
    assert _ads(content, 100) is None


def test_removed_fraction_gate_aborts_when_cutting_too_much():
    # Content technically covers > 55% but the inverted removal exceeds the cap
    # when we forbid removing more than a tiny fraction.
    content = [{'start': 0, 'end': 60}]
    # coverage 0.60+pad passes the floor; removal ~0.385 > 0.30 cap -> abort.
    assert _ads(content, 100, max_removed_fraction=0.30) is None
    # With the default 0.45 cap it is allowed.
    assert _ads(content, 100) is not None


def test_min_gap_bridges_micro_pauses():
    # Two content spans 5s apart (< 8s min_gap) bridge into one -> no cut between.
    content = [{'start': 0, 'end': 45}, {'start': 50, 'end': 100}]
    ads = _ads(content, 100)
    assert ads == []  # the 5s gap is bridged and kept; nothing removed


def test_slivers_below_min_ad_seconds_dropped():
    # A real inverted gap below the min_ad floor is dropped. edge_pad=0 so the
    # 12s gap (40-52) is not bridged; the 20s min_ad floor then drops it.
    content = [{'start': 0, 'end': 40}, {'start': 52, 'end': 100}]
    ads = _ads(content, 100, edge_pad=0.0, min_gap=1.0, min_ad_seconds=20.0)
    assert ads == []


def test_max_single_ad_gate_aborts_one_giant_cut():
    # One missing content window -> a single 30s contiguous cut (30% of episode).
    # Coverage (0.70) and removed (0.27) both pass, but a tighter single-cut gate
    # catches the giant block and aborts.
    content = [{'start': 0, 'end': 50}, {'start': 80, 'end': 100}]
    assert _ads(content, 100, max_single_ad_fraction=0.20) is None
    # A looser single-cut gate allows it.
    assert _ads(content, 100, max_single_ad_fraction=0.50) is not None


def test_max_single_ad_seconds_aborts_long_absolute_cut():
    # On a 2-hour episode a single 497s cut is a tiny fraction (0.069), so the
    # fraction gate passes -- but the absolute 420s cap catches it and aborts.
    content = [{'start': 0, 'end': 3000}, {'start': 3500, 'end': 7200}]
    assert _ads(content, 7200) is None
    # A looser absolute cap lets the same cut through.
    assert _ads(content, 7200, max_single_ad_seconds=600.0) is not None


def test_empty_or_zero_duration_returns_none():
    assert _ads([], 100) is None
    assert _ads([{'start': 0, 'end': 50}], 0) is None


def test_info_attributes_the_failing_gate():
    # zero duration
    assert _info([{'start': 0, 'end': 50}], 0)['failed_gate'] == 'zero_duration'
    # no usable content spans
    assert _info([], 100)['failed_gate'] == 'empty_content'
    # coverage too low (40% < 0.55)
    cov = _info([{'start': 0, 'end': 40}], 100)
    assert cov['failed_gate'] == 'coverage'
    assert cov['coverage'] < 0.55
    # removed fraction over a tighter cap
    rem = _info([{'start': 0, 'end': 60}], 100, max_removed_fraction=0.30)
    assert rem['failed_gate'] == 'removed_fraction'
    assert rem['removed_fraction'] > 0.30
    # one cut over the fraction cap
    frac = _info([{'start': 0, 'end': 50}, {'start': 80, 'end': 100}], 100,
                 max_single_ad_fraction=0.20)
    assert frac['failed_gate'] == 'single_cut_fraction'
    assert frac['longest_cut_fraction'] > 0.20
    # one cut over the absolute seconds cap (passes the fraction gate)
    secs = _info([{'start': 0, 'end': 3000}, {'start': 3500, 'end': 7200}], 7200)
    assert secs['failed_gate'] == 'single_cut_seconds'
    assert secs['longest_cut_seconds'] > 420


def test_info_clean_on_success():
    # Assert both halves together: a clean verdict pairs with a real ad list.
    ads, info = invert_content_to_ads(
        [{'start': 0, 'end': 50}, {'start': 80, 'end': 100}], 100, **GATES)
    assert info['failed_gate'] is None
    assert ads and len(ads) == 1
    assert info['merged_content_spans'] == 2
    assert info['coverage'] > 0.55
    assert info['longest_cut_seconds'] > 0


def test_end_ads_when_content_starts_late_and_ends_early():
    # Content 20-80 of 100s -> ads at the head (0-20) and tail (80-100),
    # shrunk by edge_pad. coverage 0.6 passes; removal 0.4-ish under 0.45.
    content = [{'start': 20, 'end': 80}]
    ads = _ads(content, 100)
    assert ads is not None
    assert len(ads) == 2
    assert abs(ads[0]['start'] - 0) < 0.01 and abs(ads[0]['end'] - 18.5) < 0.01
    assert abs(ads[1]['start'] - 81.5) < 0.01 and abs(ads[1]['end'] - 100) < 0.01

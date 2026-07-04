"""Unit tests for the silence boundary snap module (task B3, Phase B)."""
import os
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='silence_snap_unit_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from ad_detector.silence_boundary_snap import snap_ad_boundaries_to_silence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _span(start, end):
    return {'start': start, 'end': end, 'duration': round(end - start, 3)}


def _ad(start, end, **extra):
    a = {'start': start, 'end': end}
    a.update(extra)
    return a


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_no_op_on_empty_ads():
    ads = []
    snap_ad_boundaries_to_silence(ads, [_span(10.0, 10.5)], 2.0, 0.3)
    assert ads == []


def test_no_op_on_empty_spans():
    ads = [_ad(100.0, 160.0)]
    snap_ad_boundaries_to_silence(ads, [], 2.0, 0.3)
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 160.0
    assert 'silence_snap' not in ads[0]


# ---------------------------------------------------------------------------
# Midpoint snap: both edges
# ---------------------------------------------------------------------------

def test_snaps_start_to_silence_midpoint():
    # Silence span: 98.0-99.0, midpoint=98.5, distance from ad start (100.0) = 1.5 <= 2.0
    ads = [_ad(100.0, 160.0)]
    spans = [_span(98.0, 99.0)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 98.5
    assert ads[0]['end'] == 160.0
    assert 'silence_snap' in ads[0]
    assert 'start' in ads[0]['silence_snap']
    assert 'end' not in ads[0]['silence_snap']


def test_snaps_end_to_silence_midpoint():
    # Silence span: 161.0-162.0, midpoint=161.5, distance from ad end (160.0) = 1.5 <= 2.0
    ads = [_ad(100.0, 160.0)]
    spans = [_span(161.0, 162.0)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 161.5
    assert 'silence_snap' in ads[0]
    assert 'end' in ads[0]['silence_snap']
    assert 'start' not in ads[0]['silence_snap']


def test_snaps_both_edges():
    # Start silence: 98.0-99.0 mid=98.5; end silence: 161.0-162.0 mid=161.5
    ads = [_ad(100.0, 160.0)]
    spans = [_span(98.0, 99.0), _span(161.0, 162.0)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 98.5
    assert ads[0]['end'] == 161.5
    assert 'start' in ads[0]['silence_snap']
    assert 'end' in ads[0]['silence_snap']


# ---------------------------------------------------------------------------
# Audit record contents
# ---------------------------------------------------------------------------

def test_snap_record_fields_and_rounding():
    # Silence span: 98.1-99.3, midpoint = 98.7, original start = 100.0, shift = -1.3
    ads = [_ad(100.0, 160.0)]
    spans = [_span(98.1, 99.3)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    rec = ads[0]['silence_snap']['start']
    assert rec['original'] == 100.0
    assert rec['silence_start'] == 98.1
    assert rec['silence_end'] == 99.3
    assert rec['snap_point'] == 98.7   # midpoint, rounded to 3dp
    assert abs(rec['shift_seconds'] - (98.7 - 100.0)) < 0.001
    assert rec['silence_duration'] == round(99.3 - 98.1, 3)
    # All numeric fields must be rounded to 3 decimal places
    for k, v in rec.items():
        if isinstance(v, float):
            assert v == round(v, 3), f"{k}={v} not rounded to 3dp"


# ---------------------------------------------------------------------------
# Max-distance cap
# ---------------------------------------------------------------------------

def test_max_distance_cap_start():
    # Midpoint 97.0, distance from 100.0 = 3.0 > max_distance 2.0 -> no snap
    ads = [_ad(100.0, 160.0)]
    spans = [_span(96.5, 97.5)]   # midpoint 97.0
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 100.0
    assert 'silence_snap' not in ads[0]


def test_max_distance_cap_end():
    # Midpoint 163.0, distance from 160.0 = 3.0 > max_distance 2.0 -> no snap
    ads = [_ad(100.0, 160.0)]
    spans = [_span(162.5, 163.5)]  # midpoint 163.0
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['end'] == 160.0
    assert 'silence_snap' not in ads[0]


# ---------------------------------------------------------------------------
# Min-silence filter
# ---------------------------------------------------------------------------

def test_min_silence_filter_rejects_short_span():
    # Span 0.2s < min_silence_s 0.3 -> rejected
    ads = [_ad(100.0, 160.0)]
    spans = [_span(99.3, 99.5)]   # 0.2s, midpoint 99.4, distance 0.6
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 100.0
    assert 'silence_snap' not in ads[0]


def test_min_silence_filter_accepts_exact_threshold():
    # Span exactly 0.3s at threshold is accepted
    ads = [_ad(100.0, 160.0)]
    spans = [_span(99.15, 99.45)]  # 0.3s, midpoint 99.3
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['start'] == 99.3
    assert 'silence_snap' in ads[0]


# ---------------------------------------------------------------------------
# Shift < 0.01 ignored
# ---------------------------------------------------------------------------

def test_tiny_shift_ignored():
    # midpoint 100.005, shift = 0.005 < 0.01 -> no snap
    ads = [_ad(100.0, 160.0)]
    spans = [_span(100.0, 100.01)]  # midpoint 100.005
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 100.0
    assert 'silence_snap' not in ads[0]


# ---------------------------------------------------------------------------
# Skips cue-snapped edge
# ---------------------------------------------------------------------------

def test_skips_start_edge_that_has_cue_snap():
    # Ad already has cue_snap for start -> silence snap must not re-move start
    ads = [_ad(99.5, 160.0, cue_snap={'start': {'original': 100.0}})]
    spans = [_span(98.0, 99.0), _span(161.0, 162.0)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    # Start must not have moved
    assert ads[0]['start'] == 99.5
    # End may have moved (silence span 161-162)
    assert ads[0]['end'] == 161.5


def test_skips_end_edge_that_has_cue_snap():
    ads = [_ad(100.0, 160.95, cue_snap={'end': {'original': 160.0}})]
    spans = [_span(98.0, 99.0), _span(161.0, 162.0)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ads[0]['end'] == 160.95
    assert ads[0]['start'] == 98.5


# ---------------------------------------------------------------------------
# Same-span exclusion (span used for start cannot be used for end)
# ---------------------------------------------------------------------------

def test_same_span_not_used_for_both_edges():
    # Span A mid=102.0 is within max_distance of BOTH start=100.0 (dist=2.0) and
    # end=104.0 (dist=2.0). Without the id-exclusion path the end edge would select
    # span A as its sole candidate. The exclusion (and the sanity bound, which also
    # kicks in after start snaps) must prevent end from snapping to the same span.
    ads = [_ad(100.0, 104.0)]
    spans = [_span(101.3, 102.7)]  # mid=102.0, dur=1.4; equidistant from both edges
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    rec = ads[0].get('silence_snap', {})
    # Start snaps to 102.0; end must NOT also snap to the same span.
    assert ads[0]['start'] == 102.0, "start should have snapped"
    assert 'end' not in rec, "same span must not snap both start and end"


# ---------------------------------------------------------------------------
# Tie-break: nearest midpoint; ties within 0.1s -> longer silence wins
# ---------------------------------------------------------------------------

def test_nearest_midpoint_wins():
    # Two spans: midpoint 98.5 (dist 1.5) and 97.0 (dist 3.0). Nearest wins.
    ads = [_ad(100.0, 160.0)]
    spans = [_span(98.0, 99.0), _span(96.5, 97.5)]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=5.0, min_silence_s=0.3)
    assert ads[0]['start'] == 98.5   # midpoint of the nearer span


def test_tie_within_01s_longer_silence_wins():
    # Two spans: span A mid=99.0 dist=1.0 dur=0.5; span B mid=99.05 dist=0.95 dur=1.0.
    # Distances round to same 0.1s bucket (both ~1.0); longer silence wins -> span B.
    ads = [_ad(100.0, 160.0)]
    # span A: 98.75-99.25 mid=99.0, dur=0.5, dist from 100.0 = 1.0
    # span B: 98.55-99.55 mid=99.05, dur=1.0, dist from 100.0 = 0.95
    spans = [
        _span(98.75, 99.25),   # mid=99.0, dur=0.5
        _span(98.55, 99.55),   # mid=99.05, dur=1.0
    ]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=5.0, min_silence_s=0.3)
    # Both round to 1.0s bucket; longer span wins -> mid 99.05
    assert ads[0]['start'] == 99.05


# ---------------------------------------------------------------------------
# Guard A: 10s revert -- if snapped ad would fall below MIN_AD_DURATION_FOR_REMOVAL,
# revert the entire ad's silence snap
# ---------------------------------------------------------------------------

def test_guard_revert_when_snapped_ad_too_short():
    # Ad 100.0-115.0 = 15s (>= 10s). Silence snap would move start to 108.0 and
    # end to 113.0 -> snapped duration = 5.0s < 10s. Entire snap reverted.
    ads = [_ad(100.0, 115.0)]
    spans = [
        _span(107.5, 108.5),   # mid=108.0, dist from start=8.0 (within max 10)
        _span(112.5, 113.5),   # mid=113.0, dist from end=2.0
    ]
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=10.0, min_silence_s=0.3)
    # Entire snap reverted
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 115.0
    assert 'silence_snap' not in ads[0]


def test_guard_no_revert_when_pre_snap_ad_was_short():
    # Pre-snap duration is 9s (< 10s). Guard only fires when pre-snap >= 10s.
    # So no revert check applies; snap proceeds.
    ads = [_ad(100.0, 109.0)]
    spans = [_span(98.5, 99.5)]   # mid=99.0, dist=1.0; shift=1.0 >= 0.01
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.3)
    # Pre-snap < 10s so guard does not fire; snap applied
    assert ads[0]['start'] == 99.0
    assert 'silence_snap' in ads[0]


def test_guard_no_revert_when_snapped_duration_still_at_threshold():
    # Ad 100.0-120.0=20s. Snap moves start to 110.0, end unchanged -> 10.0s == threshold.
    # 10.0 >= 10.0 -> no revert.
    ads = [_ad(100.0, 120.0)]
    spans = [_span(109.5, 110.5)]  # mid=110.0, dist=10.0 from start
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=10.0, min_silence_s=0.3)
    assert ads[0]['start'] == 110.0
    assert ads[0]['end'] == 120.0
    assert 'silence_snap' in ads[0]


# ---------------------------------------------------------------------------
# Guard B: 1.0s merge-gap neighbor guard
# ---------------------------------------------------------------------------

def test_guard_merge_gap_start_neighbor():
    # Ad B: 100-160. Preceding ad A ends at 99.5.
    # Silence snap would move ad B start to 99.0 -> gap A.end-B.start = 0.5 < 1.0 -> reject snap.
    ad_a = _ad(50.0, 99.5)
    ad_b = _ad(100.0, 160.0)
    spans = [_span(98.5, 99.5)]   # mid=99.0, dist from 100.0 = 1.0
    snap_ad_boundaries_to_silence([ad_a, ad_b], spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ad_b['start'] == 100.0
    assert 'silence_snap' not in ad_b


def test_guard_merge_gap_end_neighbor():
    # Ad A: 100-160. Following ad B starts at 160.8.
    # Silence snap would move ad A end to 161.0 -> gap = 160.8-161.0 = -0.2 < 1.0 -> reject snap.
    ad_a = _ad(100.0, 160.0)
    ad_b = _ad(160.8, 220.0)
    spans = [_span(160.5, 161.5)]  # mid=161.0, dist=1.0
    snap_ad_boundaries_to_silence([ad_a, ad_b], spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ad_a['end'] == 160.0
    assert 'silence_snap' not in ad_a


def test_guard_merge_gap_allows_large_enough_gap():
    # Preceding ad ends at 95.0; span mid=98.5 is 2.0s from ad_b.start=100.0 (within max)
    # and 3.5s from ad_a.end=95.0 (beyond max_distance=2.0 -> ad_a cannot consume it).
    # Gap from prev_end=95.0 to proposed=98.5 = 3.5 >= 1.0 -> allow.
    ad_a = _ad(50.0, 95.0)
    ad_b = _ad(100.0, 160.0)
    spans = [_span(98.0, 99.0)]   # mid=98.5, dist from ad_b.start=1.5; dist from ad_a.end=3.5
    snap_ad_boundaries_to_silence([ad_a, ad_b], spans, max_distance_s=2.0, min_silence_s=0.3)
    assert ad_b['start'] == 98.5


# ---------------------------------------------------------------------------
# Proposed start < current end / proposed end > current start constraint
# ---------------------------------------------------------------------------

def test_start_snap_cannot_push_past_ad_end():
    # A span whose midpoint is past the ad end must not snap start past end.
    # Use max_distance_s=5.0 but a span so far past end that it can't snap start.
    # span mid=105.0, ad start=100.0 (dist=5.0 at boundary), ad end=101.0;
    # proposed_must_be_less_than=101.0 rejects mid=105.0 for start edge.
    # For end edge: dist from end(101.0) = 4.0 <= 5.0 -> end snaps to 105.0.
    # We need a span that is ONLY reachable by start AND whose mid > ad_end.
    # Solution: tiny ad where any snap would violate start < end. Use a span
    # straddling the ad with mid > ad_end so start-snap is rejected, and also
    # far enough from ad_end that end-snap is also rejected (dist > max_distance).
    ads = [_ad(100.0, 101.0)]
    # mid=101.5 is 1.5s past end (101.0) and 1.5s past start (100.0).
    # Start snap: proposed_must_be_less_than=101.0, mid=101.5 -> rejected.
    # End snap: dist from end=101.0 to mid=101.5 = 0.5 <= max_distance -> end snaps.
    # That's fine - only start is constrained to stay < end.
    # To test that start is NOT pushed past end, place a span entirely before the ad
    # whose midpoint is past end is impossible. Instead test directly:
    # ad is tiny (100.0-100.2), span mid=100.5 > ad end=100.2.
    # Start would snap to 100.5 but 100.5 >= end 100.2 -> rejected by proposed_must_be_less_than.
    ads = [_ad(100.0, 100.2)]
    spans = [_span(100.3, 100.7)]   # mid=100.5 > end=100.2; dist from start=0.5
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 100.0   # start not pushed past end


def test_end_snap_cannot_pull_before_ad_start():
    # A span whose midpoint is before the ad start cannot snap end before start.
    ads = [_ad(100.0, 101.0)]
    spans = [_span(98.5, 99.5)]   # mid=99.0, < start
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=5.0, min_silence_s=0.3)
    # The span is closer to start (dist=1.0) than end (dist=2.0); start snaps to 99.0.
    # End not involved since no other span.
    assert ads[0]['start'] == 99.0 or ads[0]['start'] == 100.0  # depends on which edge wins
    # Regardless, end must not pull before start
    assert ads[0]['end'] >= ads[0]['start']


# ---------------------------------------------------------------------------
# Cross-ad dual-span: ad1.end snaps, ad2.start rejected by merge-gap guard
# ---------------------------------------------------------------------------

def test_cross_ad_dual_span_start_rejected_after_neighbor_snap():
    # ad1=[50,100], ad2=[102,160]. A silence span sits between them at mid=100.5.
    # ad1.end snaps to 100.5 (processed first, gap to ad2.start=102.0-100.5=1.5 >= 1.0).
    # A second span at mid=101.0 is within range of ad2.start=102.0 (dist=1.0).
    # Gap from ad1.post-snap-end=100.5 to proposed ad2.start=101.0 = 0.5 < 1.0 -> rejected.
    ad1 = _ad(50.0, 100.0)
    ad2 = _ad(102.0, 160.0)
    spans = [
        _span(100.3, 100.7),  # mid=100.5; snap ad1.end (dist=0.5)
        _span(100.8, 101.2),  # mid=101.0; candidate for ad2.start (dist=1.0)
    ]
    snap_ad_boundaries_to_silence([ad1, ad2], spans, max_distance_s=2.0, min_silence_s=0.3)
    # ad1.end should have snapped to 100.5
    assert ad1['end'] == 100.5, f"ad1.end expected 100.5, got {ad1['end']}"
    assert 'end' in ad1.get('silence_snap', {}), "ad1 end snap not recorded"
    # ad2.start must remain at 102.0 (merge-gap guard rejected the snap)
    assert ad2['start'] == 102.0, f"ad2.start expected 102.0, got {ad2['start']}"
    assert 'start' not in ad2.get('silence_snap', {}), "ad2 start snap should have been rejected"


# ---------------------------------------------------------------------------
# Both guards on one ad: Guard B rejects end, Guard A reverts the whole ad
# ---------------------------------------------------------------------------

def test_both_guards_trigger_causes_whole_ad_revert():
    # Ad [50,60] is exactly at MIN_AD_DURATION_FOR_REMOVAL=10s.
    # A span near start (mid=55.0) passes Guard B (gap from prev_end=10.0 is large).
    # Start snaps to 55.0 -> snapped duration = 60.0-55.0 = 5.0s < 10.0s -> Guard A reverts.
    # A span near end (mid=60.6) fails Guard B (gap to next_ad.start=61.0 is 0.4s < 1.0).
    # End snap is rejected. snap_record has start only; Guard A then reverts the whole ad.
    # The neighbor ads must remain unchanged.
    prev_ad = _ad(0.0, 10.0)
    ad = _ad(50.0, 60.0)      # 10s exactly, at threshold
    next_ad = _ad(61.0, 120.0)
    spans = [
        _span(54.5, 55.5),    # mid=55.0, dist from ad.start=5.0 (use max_distance=6.0)
        _span(60.3, 60.9),    # mid=60.6, dist from ad.end=0.6; gap to next=0.4 < 1.0
    ]
    snap_ad_boundaries_to_silence(
        [prev_ad, ad, next_ad], spans, max_distance_s=6.0, min_silence_s=0.3
    )
    # Guard A fires: entire ad snap reverted
    assert ad['start'] == 50.0, f"ad.start expected 50.0, got {ad['start']}"
    assert ad['end'] == 60.0, f"ad.end expected 60.0, got {ad['end']}"
    assert 'silence_snap' not in ad, "snap record must be absent after full revert"
    # Neighbors untouched (silence_snap only modifies the ad being processed)
    assert prev_ad['end'] == 10.0
    assert next_ad['start'] == 61.0


# ---------------------------------------------------------------------------
# Bisect bound-edge correctness (2a)
#
# Eligibility criterion: |midpoint - edge| <= max_distance_s.
# midpoint = (start + end) / 2.
# Lower bound cut: end < edge - max_distance_s => mid < edge - max_distance_s => ineligible.
# Upper bound cut: start > edge + max_distance_s => mid > edge + max_distance_s => ineligible.
# ---------------------------------------------------------------------------

def test_lower_bound_span_end_just_inside_is_eligible():
    # edge=100, max_distance=2.0. A zero-width span at end=98.001 has
    # mid=98.001, dist=1.999 <= 2.0 -> eligible and snaps.
    ads = [_ad(100.0, 160.0)]
    spans = [_span(98.001, 98.001)]  # zero-width, mid=98.001, dur=0
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 98.001


def test_lower_bound_span_nonzero_width_end_equals_cutoff_is_ineligible():
    # edge=100, max_distance=2.0, cutoff end = 98.0.
    # Span start=97.0, end=98.0 -> mid=97.5, dist=2.5 > 2.0 -> ineligible.
    # The midpoint criterion (not the bisect cut) rejects this span.
    ads = [_ad(100.0, 160.0)]
    spans = [_span(97.0, 98.0)]  # mid=97.5, dist=2.5
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 100.0
    assert 'silence_snap' not in ads[0]


def test_upper_bound_span_start_just_inside_is_eligible():
    # edge=100, max_distance=2.0. A zero-width span at start=101.999 has
    # mid=101.999, dist=1.999 <= 2.0 -> eligible (with tiny shift > 0.01).
    ads = [_ad(100.0, 160.0)]
    spans = [_span(101.999, 101.999)]  # zero-width, mid=101.999, dist=1.999
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 101.999


def test_upper_bound_span_nonzero_width_start_equals_cutoff_is_ineligible():
    # edge=100, max_distance=2.0, cutoff start = 102.0.
    # Span start=102.0, end=103.0 -> mid=102.5, dist=2.5 > 2.0 -> ineligible.
    # The midpoint criterion rejects this span (start == cutoff, but mid > edge+max).
    ads = [_ad(100.0, 160.0)]
    spans = [_span(102.0, 103.0)]  # mid=102.5, dist=2.5
    snap_ad_boundaries_to_silence(ads, spans, max_distance_s=2.0, min_silence_s=0.0)
    assert ads[0]['start'] == 100.0
    assert 'silence_snap' not in ads[0]

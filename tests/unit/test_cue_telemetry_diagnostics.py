"""Edge-distance, unused-reason, and below-threshold telemetry (#350 Phase 6)."""
from ad_detector.cue_telemetry import build_cue_detection_records
from ad_detector.cue_pair_ads import SKIP_NO_PARTNER
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result(signals=(), near_misses=()):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    r.cue_near_misses = list(near_misses)
    return r


def _tcue(start, end, *, role='boundary', cue_type='ad_break_boundary',
          template_id=1, score=0.85, conf=0.92, label='ding'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'template_id': template_id, 'label': label,
                 'cue_type': cue_type, 'role': role, 'score': score},
    )


def _rec_by_start(recs, start):
    return next(r for r in recs if abs(r['start_s'] - start) < 0.01)


# --- below_threshold rows ---------------------------------------------------

def test_below_threshold_records_from_near_misses():
    nm = {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
          'role': 'boundary', 'start_s': 250.0, 'end_s': 250.5, 'score': 0.7}
    recs = build_cue_detection_records([], _result(near_misses=[nm]))
    assert len(recs) == 1
    r = recs[0]
    assert r['outcome'] == 'below_threshold'
    assert r['start_s'] == 250.0 and r['end_s'] == 250.5
    assert r['match_score'] == 0.7
    assert r['source'] == 'template'


def test_below_threshold_and_match_coexist():
    match = _tcue(100.0, 100.5, score=0.9)
    nm = {'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
          'role': 'boundary', 'start_s': 250.0, 'end_s': 250.5, 'score': 0.7}
    recs = build_cue_detection_records([], _result(signals=[match], near_misses=[nm]))
    outcomes = sorted(r['outcome'] for r in recs)
    assert outcomes == ['below_threshold', 'none']


# --- edge_distance_s --------------------------------------------------------

def test_edge_distance_start_role_uses_ad_starts():
    # start-role cue at end=100.5; nearest ad start is 108.0 -> +7.5.
    cue = _tcue(100.0, 100.5, role='start', cue_type='ad_break_start')
    ads = [{'start': 108.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads)
    assert abs(recs[0]['edge_distance_s'] - 7.5) < 0.001


def test_edge_distance_end_role_uses_ad_ends():
    # end-role cue at start=205.0; nearest ad end is 200.0 -> -5.0.
    cue = _tcue(205.0, 205.5, role='end', cue_type='ad_break_end')
    ads = [{'start': 100.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads)
    assert abs(recs[0]['edge_distance_s'] - (-5.0)) < 0.001


def test_edge_distance_non_ad_is_null():
    cue = _tcue(10.0, 12.0, role='non_ad', cue_type='show_intro')
    ads = [{'start': 100.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads)
    assert recs[0]['edge_distance_s'] is None


def test_edge_distance_uses_pre_snap_edges_not_snapped():
    # The live ad was snapped to 99.55, but the pre-snap edge (100.0) is what
    # edge_distance is measured against.
    cue = _tcue(100.0, 100.5, role='start', cue_type='ad_break_start')
    live_ads = [{'start': 99.55, 'end': 200.0}]
    pre_snap = [{'start': 105.0, 'end': 200.0}]
    recs = build_cue_detection_records(live_ads, _result(signals=[cue]), pre_snap_ads=pre_snap)
    # measured against pre_snap start 105.0 -> +4.5 (cue.end 100.5)
    assert abs(recs[0]['edge_distance_s'] - 4.5) < 0.001


# --- unused_reason taxonomy -------------------------------------------------

def test_unused_reason_advisory_role():
    cue = _tcue(10.0, 12.0, role='non_ad', cue_type='show_intro')
    recs = build_cue_detection_records([], _result(signals=[cue]))
    assert recs[0]['outcome'] == 'none'
    assert recs[0]['unused_reason'] == 'advisory_role'


def test_unused_reason_covered():
    # boundary cue sitting inside an LLM ad span it did not move.
    cue = _tcue(150.0, 150.5, role='boundary')
    ads = [{'start': 100.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads)
    assert recs[0]['unused_reason'] == 'covered'


def test_unused_reason_below_snap_confidence():
    cue = _tcue(500.0, 500.5, role='boundary', conf=0.6)  # below snap 0.80
    recs = build_cue_detection_records([], _result(signals=[cue]),
                                       snap_confidence=0.80)
    assert recs[0]['unused_reason'] == 'below_snap_confidence'


def test_unused_reason_out_of_reach():
    # High-confidence eligible cue far from any ad edge (beyond snap window).
    cue = _tcue(500.0, 500.5, role='start', cue_type='ad_break_start', conf=0.95)
    ads = [{'start': 100.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads,
                                       snap_confidence=0.80, snap_lead_s=4.0, snap_lag_s=2.0)
    assert recs[0]['unused_reason'] == 'out_of_reach'


def test_unused_reason_out_of_reach_start_role_past_ad_start():
    # Role-aware asymmetric reach (finding 10): a start-role cue's END sits 6s PAST
    # the ad start (cue.end = ad_start + 6). Its window is [ad_start-lead, ad_start
    # +lag] on cue.end, i.e. signed d=ad_start-cue.end in [-lag, +lead] = [-4, +10].
    # d = -6 < -4 -> out_of_reach. The old symmetric abs() gate (|-6| <= max(10,4))
    # wrongly called this 'unpaired'.
    cue = _tcue(106.0, 106.0, role='start', cue_type='ad_break_start', conf=0.95)
    ads = [{'start': 100.0, 'end': 300.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads,
                                       snap_confidence=0.80, snap_lead_s=10.0, snap_lag_s=4.0)
    assert recs[0]['unused_reason'] == 'out_of_reach'


def test_unused_reason_in_reach_start_role_within_one_sided_window():
    # Same lead=10/lag=4, but the start-role cue's END is 8s BEFORE the ad start
    # (d = ad_start - cue.end = +8, within [-4, +10]) -> within reach, not out.
    cue = _tcue(92.0, 92.0, role='start', cue_type='ad_break_start', conf=0.95)
    ads = [{'start': 100.0, 'end': 300.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads,
                                       snap_confidence=0.80, snap_lead_s=10.0, snap_lag_s=4.0)
    assert recs[0]['unused_reason'] != 'out_of_reach'


def test_unused_reason_unpaired_default():
    # Eligible, high-conf, within reach of an ad edge but still not used and no
    # pair diagnostics -> unpaired.
    cue = _tcue(103.0, 103.5, role='start', cue_type='ad_break_start', conf=0.95)
    ads = [{'start': 105.0, 'end': 200.0}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads,
                                       snap_confidence=0.80, snap_lead_s=4.0, snap_lag_s=2.0)
    assert recs[0]['unused_reason'] == 'unpaired'


def test_unused_reason_carries_pair_skip_reason():
    cue = _tcue(500.0, 500.5, role='start', cue_type='ad_break_start', conf=0.95)
    diagnostics = {(1, 500.0): SKIP_NO_PARTNER}
    recs = build_cue_detection_records([], _result(signals=[cue]),
                                       pair_skip_diagnostics=diagnostics,
                                       snap_confidence=0.80)
    assert recs[0]['unused_reason'] == SKIP_NO_PARTNER


def test_snap_and_pair_outcomes_have_no_unused_reason():
    cue = _tcue(98.0, 99.5)
    ads = [{'start': 99.55, 'end': 160.0,
            'cue_snap': {'start': {'template_id': 1, 'cue_start': 98.0}}}]
    recs = build_cue_detection_records(ads, _result(signals=[cue]), pre_snap_ads=ads)
    assert recs[0]['outcome'] == 'snap'
    assert recs[0]['unused_reason'] is None

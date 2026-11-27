"""Show intro/outro cues stop the pass-1 cue snap from widening a cut."""
from tests.app_bootstrap import bootstrap

bootstrap('show_cue_snap_test_')
from ad_detector.cue_boundary_snap import snap_ad_boundaries_to_cues, snap_split_edge
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _cue(start, end, role='boundary', remove=True, conf=0.98):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'role': role, 'label': 'x', 'template_id': 1,
                 'remove_with_ad': remove})


def _show(start, end):
    return _cue(start, end, role='non_ad', remove=False)


def _result(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _snap(ad, *signals):
    snap_ad_boundaries_to_cues([ad], _result(*signals), max_boundary_shift_s=15.0)
    return ad


def test_end_snap_over_show_cue_stays_put():
    ad = _snap({'start': 228.0, 'end': 232.62}, _cue(241.0, 243.75), _show(233.08, 238.58))
    assert ad['end'] == 232.62


def test_end_snap_without_show_cue_in_the_way_happens():
    ad = _snap({'start': 228.0, 'end': 232.62}, _cue(241.0, 243.75))
    assert ad['end'] == 243.8


def test_end_snap_past_show_cue_outside_the_widening_happens():
    ad = _snap({'start': 228.0, 'end': 232.62}, _cue(241.0, 243.75), _show(250.0, 255.0))
    assert ad['end'] == 243.8


def test_start_snap_over_show_cue_stays_put():
    ad = _snap({'start': 100.0, 'end': 160.0}, _cue(90.0, 92.0, role='start', remove=False),
               _show(94.0, 96.0))
    assert ad['start'] == 100.0


def test_start_snap_without_show_cue_in_the_way_happens():
    ad = _snap({'start': 100.0, 'end': 160.0}, _cue(90.0, 92.0, role='start', remove=False))
    assert ad['start'] == 92.05


def test_shrinking_move_with_show_cue_inside_is_unaffected():
    ad = _snap({'start': 100.0, 'end': 160.0},
               _cue(102.0, 103.0, role='start', remove=False),
               _cue(157.0, 158.0, role='end', remove=False),
               _show(120.0, 125.0))
    assert (ad['start'], ad['end']) == (103.05, 156.95)


def test_split_edge_refuses_widening_over_show_cue():
    cues = [_cue(241.0, 243.75), _show(233.08, 238.58)]
    assert snap_split_edge('end', 232.62, 228.0, cues, 15.0) is None
    assert snap_split_edge('end', 232.62, 228.0, cues[:1], 15.0)[0] == 243.8


def test_split_edge_start_refuses_widening_over_show_cue():
    cues = [_cue(98.0, 99.5), _show(99.6, 99.9)]
    assert snap_split_edge('start', 100.0, 160.0, cues, 15.0) is None
    assert snap_split_edge('start', 100.0, 160.0, cues[:1], 15.0)[0] == 97.95


# --- validator and pass 2 ----------------------------------------------------


def test_end_snap_into_a_show_cue_straddling_the_edge_stays_put():
    ad = _snap({'start': 228.0, 'end': 232.62}, _cue(241.0, 243.75), _show(232.0, 235.0))
    assert ad['end'] == 232.62

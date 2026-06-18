"""Unit tests for the cue boundary snap module (#350)."""
from ad_detector.cue_boundary_snap import snap_ad_boundaries_to_cues
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.9, source='template', label='ding', template_id=1):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue',
        confidence=conf,
        details={'source': source, 'label': label, 'template_id': template_id},
    )


# ---------------------------------------------------------------------------
# Start-edge tests
# ---------------------------------------------------------------------------

def test_snap_moves_ad_start_to_cue_end():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with(_cue(start=98.0, end=99.5))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 99.55  # cue end + 0.05 lead
    assert 'cue_snap' in ads[0]
    assert ads[0]['cue_snap']['start']['template_id'] == 1


def test_snap_respects_max_boundary_cap():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with(_cue(start=80.0, end=82.0))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=2.0)
    # cue end (82.0) is 17.95s before original start -> beyond cap, no snap
    assert ads[0]['start'] == 100.0
    assert 'cue_snap' not in ads[0]


def test_snap_no_op_when_no_cues():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with()
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 100.0


def test_snap_skips_low_confidence_cues():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with(_cue(start=98.5, end=99.5, conf=0.5))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 100.0


def test_snap_picks_highest_confidence_when_two_cues_in_window():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with(
        _cue(start=97.0, end=97.8, conf=0.81, label='weak'),
        _cue(start=99.0, end=99.6, conf=0.95, label='strong'),
    )
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['cue_snap']['start']['label'] == 'strong'
    assert ads[0]['start'] == 99.65


def test_snap_never_pushes_past_ad_end():
    # Cue end past the ad's own end should be ignored.
    ads = [{'start': 100.0, 'end': 100.5}]
    result = _result_with(_cue(start=100.4, end=101.0))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 100.0


def test_snap_no_op_when_result_is_none():
    ads = [{'start': 100.0, 'end': 160.0}]
    snap_ad_boundaries_to_cues(ads, None, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 100.0


# ---------------------------------------------------------------------------
# End-edge tests
# ---------------------------------------------------------------------------

def test_snap_moves_ad_end_to_cue_start():
    ads = [{'start': 100.0, 'end': 160.0}]
    # Resume-content stinger plays at the break boundary.
    result = _result_with(_cue(start=161.0, end=161.6, label='resume'))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['end'] == 160.95  # cue start - 0.05 lead
    assert ads[0]['cue_snap']['end']['label'] == 'resume'
    assert ads[0]['start'] == 100.0  # start unchanged


def test_snaps_both_edges_when_cues_bracket_break():
    ads = [{'start': 100.0, 'end': 160.0}]
    result = _result_with(
        _cue(start=98.5, end=99.4, label='intro', template_id=1),
        _cue(start=160.4, end=161.0, label='outro', template_id=2),
    )
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 99.45
    assert ads[0]['end'] == 160.35
    assert ads[0]['cue_snap']['start']['label'] == 'intro'
    assert ads[0]['cue_snap']['end']['label'] == 'outro'


def test_single_cue_cannot_drag_both_edges():
    # One cue near the start of the ad must not also be picked for the end.
    ads = [{'start': 100.0, 'end': 101.0}]
    result = _result_with(_cue(start=98.0, end=99.5))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['start'] == 99.55
    # End stays put because the only cue was used for the start.
    assert ads[0]['end'] == 101.0


def test_end_snap_respects_max_boundary_cap():
    ads = [{'start': 100.0, 'end': 160.0}]
    # Outro cue 20s past the ad end -> beyond a 2s cap.
    result = _result_with(_cue(start=180.0, end=180.6))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=2.0)
    assert ads[0]['end'] == 160.0


def test_end_snap_never_pulls_before_start():
    ads = [{'start': 100.0, 'end': 100.6}]
    # Cue start at 99.8 is before ad start -> rejected as end candidate.
    result = _result_with(_cue(start=99.8, end=100.4))
    snap_ad_boundaries_to_cues(ads, result, max_boundary_shift_s=10.0)
    assert ads[0]['end'] == 100.6

"""A pass-2 outward cue snap must not move an edge across show intro/outro music."""
from tests.app_bootstrap import bootstrap

bootstrap('pass2_cue_snap_show_cue_test_')

from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal
from main_app.pipeline import verify

SETTINGS = {'max_boundary_shift_s': 60.0, 'snap_lead_s': 10.0, 'snap_lag_s': 4.0,
            'min_confidence': 0.8}


def _cue(start, end, role, cue_type):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=0.95,
        details={'source': 'template', 'label': cue_type, 'role': role,
                 'cue_type': cue_type, 'template_id': 7, 'remove_with_ad': True})


def _snap(*cues):
    result = AudioAnalysisResult()
    result.signals = list(cues)
    processed = [{'start': 100.0, 'end': 130.0}]
    original = [{'start': 100.0, 'end': 130.0}]
    verify._snap_pass2_cuts_to_cues(processed, original, result, [], dict(SETTINGS))
    return original[0]


def test_outward_snap_happens_without_show_cue():
    cut = _snap(_cue(95.0, 97.0, 'boundary', 'ad_break_boundary'))
    assert cut['start'] < 100.0


def test_outward_snap_skipped_across_show_cue():
    cut = _snap(_cue(95.0, 97.0, 'boundary', 'ad_break_boundary'),
                _cue(97.5, 98.5, 'non_ad', 'show_outro'))
    assert cut['start'] == 100.0
    assert 'pass2_cue_snap' not in cut


def test_outward_snap_skipped_across_partially_overlapping_show_cue():
    cut = _snap(_cue(95.0, 97.0, 'boundary', 'ad_break_boundary'),
                _cue(94.0, 96.0, 'non_ad', 'show_outro'))
    assert cut['start'] == 100.0
    assert 'pass2_cue_snap' not in cut

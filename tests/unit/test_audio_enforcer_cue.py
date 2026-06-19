"""audio_enforcer cue rendering + runtime has_cue guidance (#350)."""
from audio_enforcer import AudioEnforcer
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.92, source='template', label='ding'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': source, 'label': label},
    )


def test_template_cue_renders_label():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(_result_with(_cue(10.0, 10.4)), 0.0, 60.0)
    assert '"ding" cue' in out
    # The detailed runtime guidance is injected when a cue fires.
    assert 'LABELLED AUDIO CUES' in out


def test_spectral_cue_renders_generic_descriptor():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, source='spectral', label=None)), 0.0, 60.0)
    assert 'Audio cue (ding/stinger)' in out
    assert 'LABELLED AUDIO CUES' in out


def test_no_cue_no_runtime_guidance():
    enforcer = AudioEnforcer()
    vol = AudioSegmentSignal(start=5.0, end=6.0, signal_type='volume_increase', confidence=0.9)
    out = enforcer.format_for_window(_result_with(vol), 0.0, 60.0)
    assert 'LABELLED AUDIO CUES' not in out


def test_cue_outside_window_not_rendered():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(_result_with(_cue(120.0, 120.4)), 0.0, 60.0)
    # No signal in window -> empty, no guidance.
    assert 'LABELLED AUDIO CUES' not in out

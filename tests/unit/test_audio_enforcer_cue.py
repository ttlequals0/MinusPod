"""audio_enforcer cue rendering + runtime has_cue guidance (#350)."""
from audio_enforcer import AudioEnforcer
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.92, source='template', label='ding', role='boundary'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': source, 'label': label, 'role': role},
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


def test_intro_outro_cue_renders_non_ad_guidance():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, label='show intro', role='non_ad')), 0.0, 60.0)
    # Intro/outro is framed as a non-ad marker, not an ad-break cue.
    assert 'NOT an ad boundary' in out
    assert 'SHOW INTRO/OUTRO MARKERS' in out
    # And it does not pull in the ad-break cue guidance on its own.
    assert 'LABELLED AUDIO CUES' not in out


# ---------------------------------------------------------------------------
# Pre/post-roll positional anchors from show intro/outro cues
# ---------------------------------------------------------------------------

def _anchor_cue(start, end, cue_type, conf=0.95):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'cue_type': cue_type,
                 'role': 'non_ad', 'label': cue_type},
    )


def test_preroll_window_gets_position_bias():
    enforcer = AudioEnforcer()
    result = _result_with(_anchor_cue(30.0, 33.0, 'show_intro'))
    # Window entirely before the intro -> pre-roll bias even with no in-window cue.
    out = enforcer.format_for_window(result, 0.0, 25.0)
    assert '=== POSITION ===' in out
    assert 'pre-roll' in out


def test_postroll_window_gets_position_bias():
    enforcer = AudioEnforcer()
    result = _result_with(_anchor_cue(3500.0, 3504.0, 'show_outro'))
    out = enforcer.format_for_window(result, 3600.0, 3700.0)
    assert 'post-roll' in out


def test_in_content_window_has_no_position_bias():
    enforcer = AudioEnforcer()
    result = _result_with(
        _anchor_cue(30.0, 33.0, 'show_intro'),
        _anchor_cue(3500.0, 3504.0, 'show_outro'),
    )
    # A window well inside the content span gets no positional block.
    out = enforcer.format_for_window(result, 1000.0, 1100.0)
    assert '=== POSITION ===' not in out

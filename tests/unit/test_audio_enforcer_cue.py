"""audio_enforcer cue rendering + runtime has_cue guidance (#350)."""
from audio_enforcer import AudioEnforcer, SPECTRAL_CUE_MAX_PER_WINDOW
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
    # The detailed runtime guidance is injected when a template cue fires.
    assert 'LABELLED AUDIO CUES' in out
    # Spectral block must be absent when only a template cue fired.
    assert 'GENERIC AUDIO CUES' not in out


def test_template_cue_renders_span_and_duration():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(_result_with(_cue(10.0, 10.4)), 0.0, 60.0)
    # Span: start-end
    assert '10.0s-10.4s' in out
    # Duration field
    assert '0.4s long' in out


def test_spectral_cue_renders_generic_descriptor():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, source='spectral', label=None)), 0.0, 60.0)
    assert 'Audio cue (generic loudness burst)' in out
    # Spectral-only: GENERIC block present, LABELLED block absent.
    assert 'GENERIC AUDIO CUES' in out
    assert 'LABELLED AUDIO CUES' not in out


def test_spectral_cue_renders_span_and_duration():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, source='spectral', label=None)), 0.0, 60.0)
    assert '10.0s-10.4s' in out
    assert '0.4s long' in out


def test_no_cue_no_runtime_guidance():
    enforcer = AudioEnforcer()
    vol = AudioSegmentSignal(start=5.0, end=6.0, signal_type='volume_increase', confidence=0.9)
    out = enforcer.format_for_window(_result_with(vol), 0.0, 60.0)
    assert 'LABELLED AUDIO CUES' not in out
    assert 'GENERIC AUDIO CUES' not in out


def test_cue_outside_window_not_rendered():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(_result_with(_cue(120.0, 120.4)), 0.0, 60.0)
    # No signal in window -> empty, no guidance.
    assert 'LABELLED AUDIO CUES' not in out
    assert 'GENERIC AUDIO CUES' not in out


def test_intro_outro_cue_renders_non_ad_guidance():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, label='show intro', role='non_ad')), 0.0, 60.0)
    # Intro/outro is framed as a non-ad marker, not an ad-break cue.
    assert 'NOT an ad boundary' in out
    assert 'SHOW INTRO/OUTRO MARKERS' in out
    # And it does not pull in the ad-break cue guidance on its own.
    assert 'LABELLED AUDIO CUES' not in out
    assert 'GENERIC AUDIO CUES' not in out


def test_intro_outro_exception_sentence_present():
    """Pre/post-roll exception sentence must be present in the non_ad guidance."""
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(
        _result_with(_cue(10.0, 10.4, label='show intro', role='non_ad')), 0.0, 60.0)
    assert 'pre-roll ad may end exactly where the intro starts' in out
    assert 'post-roll ad may begin where the outro ends' in out


def test_spectral_cap_keeps_top_n_by_confidence():
    """Cap test: SPECTRAL_CUE_MAX_PER_WINDOW spectral cues kept; remainder omitted."""
    enforcer = AudioEnforcer()
    # Build SPECTRAL_CUE_MAX_PER_WINDOW + 2 spectral cues with varied confidence.
    total = SPECTRAL_CUE_MAX_PER_WINDOW + 2
    signals = [
        _cue(float(i * 5), float(i * 5 + 0.4), conf=0.80 + i * 0.01,
             source='spectral', label=None)
        for i in range(total)
    ]
    out = enforcer.format_for_window(_result_with(*signals), 0.0, 300.0)
    # Exactly SPECTRAL_CUE_MAX_PER_WINDOW cue lines (each contains 'generic loudness burst').
    rendered = out.count('generic loudness burst')
    assert rendered == SPECTRAL_CUE_MAX_PER_WINDOW, (
        f"Expected {SPECTRAL_CUE_MAX_PER_WINDOW} rendered spectral cues, got {rendered}"
    )
    # The omission note must appear.
    assert '+2 more unlabelled audio cues omitted for brevity' in out


def test_spectral_cap_fails_if_cap_removed():
    """Sentinel: if SPECTRAL_CUE_MAX_PER_WINDOW is removed/set to None this must fail."""
    # This test relies on test_spectral_cap_keeps_top_n_by_confidence above;
    # it exists to document the sensitivity requirement.
    assert isinstance(SPECTRAL_CUE_MAX_PER_WINDOW, int) and SPECTRAL_CUE_MAX_PER_WINDOW > 0


def test_template_cues_never_capped():
    """Template cues must all render regardless of count."""
    enforcer = AudioEnforcer()
    total = SPECTRAL_CUE_MAX_PER_WINDOW + 3
    signals = [
        _cue(float(i * 5), float(i * 5 + 0.4), conf=0.92, source='template', label='ding')
        for i in range(total)
    ]
    out = enforcer.format_for_window(_result_with(*signals), 0.0, 300.0)
    rendered = out.count('"ding" cue')
    assert rendered == total, (
        f"Expected all {total} template cues rendered, got {rendered}"
    )
    assert 'omitted for brevity' not in out


def test_mixed_source_guidance_blocks():
    """When both template and spectral cues fire, both guidance blocks appear."""
    enforcer = AudioEnforcer()
    tmpl = _cue(10.0, 10.4, source='template', label='ding')
    spec = _cue(20.0, 20.4, source='spectral', label=None)
    out = enforcer.format_for_window(_result_with(tmpl, spec), 0.0, 60.0)
    assert 'LABELLED AUDIO CUES' in out
    assert 'GENERIC AUDIO CUES' in out


def test_mixed_source_flags_independent():
    """Guidance blocks must be gated independently: merging flags would make this fail."""
    enforcer = AudioEnforcer()
    # Only spectral -- LABELLED must be absent.
    spec_only = _result_with(_cue(10.0, 10.4, source='spectral', label=None))
    out_spec = enforcer.format_for_window(spec_only, 0.0, 60.0)
    assert 'GENERIC AUDIO CUES' in out_spec
    assert 'LABELLED AUDIO CUES' not in out_spec

    # Only template -- GENERIC must be absent.
    tmpl_only = _result_with(_cue(10.0, 10.4, source='template', label='ding'))
    out_tmpl = enforcer.format_for_window(tmpl_only, 0.0, 60.0)
    assert 'LABELLED AUDIO CUES' in out_tmpl
    assert 'GENERIC AUDIO CUES' not in out_tmpl


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

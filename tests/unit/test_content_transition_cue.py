"""content_transition cue type (#350 follow-up): config role/label and the
type-aware LLM prompt wording in audio_enforcer."""
from audio_enforcer import AudioEnforcer
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal
from config import (
    AUDIO_CUE_TYPES,
    AUDIO_CUE_ROLE_NON_AD,
    AUDIO_CUE_TYPE_CONTENT_TRANSITION,
    audio_cue_type_label,
    audio_cue_type_role,
)


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _content_transition_cue(start=10.0, end=10.4, conf=0.92, label='jingle'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'label': label, 'role': 'non_ad',
                 'cue_type': AUDIO_CUE_TYPE_CONTENT_TRANSITION},
    )


def test_content_transition_is_non_ad():
    assert AUDIO_CUE_TYPE_CONTENT_TRANSITION in AUDIO_CUE_TYPES
    assert audio_cue_type_role(AUDIO_CUE_TYPE_CONTENT_TRANSITION) == AUDIO_CUE_ROLE_NON_AD
    assert audio_cue_type_label(AUDIO_CUE_TYPE_CONTENT_TRANSITION) == 'content transition'


def test_content_transition_prompt_wording():
    enforcer = AudioEnforcer()
    out = enforcer.format_for_window(_result_with(_content_transition_cue()), 0.0, 60.0)
    # Its own descriptor + guidance, not the intro/outro "show open/close" wording.
    assert 'CONTENT TRANSITION MARKERS' in out
    assert 'may or may not sit at an ad boundary' in out
    assert "show's open/close" not in out
    assert 'SHOW INTRO/OUTRO MARKERS' not in out
    # Not framed as an ad-break cue either.
    assert 'LABELLED AUDIO CUES' not in out
    assert 'GENERIC AUDIO CUES' not in out


def test_content_transition_distinct_from_intro_outro():
    enforcer = AudioEnforcer()
    intro = AudioSegmentSignal(
        start=5.0, end=5.4, signal_type='audio_cue', confidence=0.92,
        details={'source': 'template', 'label': 'show intro', 'role': 'non_ad',
                 'cue_type': 'show_intro'})
    out = enforcer.format_for_window(_result_with(intro), 0.0, 60.0)
    # Intro still gets the show open/close framing, not the content-transition one.
    assert 'SHOW INTRO/OUTRO MARKERS' in out
    assert 'CONTENT TRANSITION MARKERS' not in out

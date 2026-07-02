"""Reviewer cue-evidence prompt section (#350)."""
from config import AUDIO_CUE_TYPE_CONTENT_TRANSITION
from ad_reviewer import _format_cue_section
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.92, label='ding'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'label': label},
    )


def test_nearby_cue_rendered_for_both_edges():
    analysis = _result_with(_cue(99.0, 99.5, label='intro'), _cue(160.0, 160.5, label='outro'))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'AUDIO CUE EVIDENCE' in out
    assert 'near AD START' in out and '"intro"' in out
    assert 'near AD END' in out and '"outro"' in out


def test_low_confidence_cue_skipped():
    analysis = _result_with(_cue(99.0, 99.5, conf=0.5))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert out == ''


def test_cue_pair_origin_rendered():
    out = _format_cue_section(
        audio_analysis=None, ad_start=100.0, ad_end=200.0,
        cue_pair={'start': {'label': 'a'}, 'end': {'label': 'b'}},
    )
    assert 'CUE-PAIR ORIGIN' in out


def test_cue_snap_applied_rendered():
    out = _format_cue_section(
        audio_analysis=None, ad_start=100.0, ad_end=200.0,
        cue_snap={'start': {'label': 'ding', 'original': 98.0}},
    )
    assert 'CUE SNAP APPLIED' in out


def test_nothing_relevant_is_empty():
    assert _format_cue_section(audio_analysis=None, ad_start=1.0, ad_end=2.0) == ''


def _non_ad_cue(start, end, cue_type, conf=0.92, label='jingle'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'label': label, 'role': 'non_ad',
                 'cue_type': cue_type},
    )


def test_content_transition_rendered_in_its_own_group():
    analysis = _result_with(_non_ad_cue(99.0, 99.5, AUDIO_CUE_TYPE_CONTENT_TRANSITION))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'CONTENT TRANSITION MARKERS' in out
    assert 'may or may not be an ad boundary' in out.lower()
    # Must NOT be lumped with the show intro/outro group or its wording.
    assert 'SHOW INTRO/OUTRO MARKERS' not in out
    assert "show's own intro/outro" not in out


def test_show_intro_outro_still_rendered_as_non_boundary():
    analysis = _result_with(_non_ad_cue(99.0, 99.5, 'show_intro', label='theme'))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'SHOW INTRO/OUTRO MARKERS' in out
    assert 'CONTENT TRANSITION MARKERS' not in out


# ---- Phase 10 tests ----

def _spectral_cue(start, end, conf=0.92, label='bump'):
    """Spectral (non-template) ad-role cue."""
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'label': label},  # no 'source' key -> spectral
    )


def test_spectral_cue_renders_as_weak_evidence_not_ground_truth():
    # Spectral cue must NOT land in the template ground-truth bucket.
    analysis = _result_with(_spectral_cue(99.0, 99.5))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'GENERIC AUDIO CUES NEARBY' in out
    assert 'weak evidence' in out
    assert 'ground-truth' not in out
    # loudness burst phrasing required
    assert 'loudness burst' in out


def test_template_cue_renders_as_ground_truth_not_weak_evidence():
    # Template cue must NOT land in the spectral weak-evidence bucket.
    analysis = _result_with(_cue(99.0, 99.5, label='ding'))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'AUDIO CUE EVIDENCE' in out
    assert 'ground-truth' in out
    assert 'GENERIC AUDIO CUES NEARBY' not in out


def test_content_transition_trailing_sentence_updated():
    analysis = _result_with(_non_ad_cue(99.0, 99.5, AUDIO_CUE_TYPE_CONTENT_TRANSITION))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'aligning a boundary to a marker when the transcript supports it' in out
    assert 'a marker never forces a cut' in out
    # old wording must be gone
    assert 'Use these as supporting evidence' not in out


def test_intro_outro_exception_sentence_present():
    analysis = _result_with(_non_ad_cue(99.0, 99.5, 'show_intro', label='theme'))
    out = _format_cue_section(audio_analysis=analysis, ad_start=100.0, ad_end=160.0)
    assert 'pre-roll ad may end exactly where the intro starts' in out
    assert 'post-roll ad may begin where the outro ends' in out


def test_bucket_radius_follows_setting():
    # Cue at 80 s from the ad edge: inside default radius (60 s) -> NOT included
    # when radius is 30 s, but included at 90 s radius.
    cue = _cue(20.0, 20.5, label='ding')   # 80 s before ad_start=100
    analysis = _result_with(cue)
    out_narrow = _format_cue_section(
        audio_analysis=analysis, ad_start=100.0, ad_end=160.0, bucket_radius=30.0
    )
    out_wide = _format_cue_section(
        audio_analysis=analysis, ad_start=100.0, ad_end=160.0, bucket_radius=90.0
    )
    assert 'AUDIO CUE EVIDENCE' not in out_narrow
    assert 'AUDIO CUE EVIDENCE' in out_wide


def test_ambiguous_snap_note_rendered():
    snap = {
        'start': {'label': 'ding', 'original': 98.0, 'ambiguous': True, 'candidates': 3},
    }
    out = _format_cue_section(audio_analysis=None, ad_start=100.0, ad_end=200.0,
                               cue_snap=snap)
    assert 'CUE SNAP APPLIED' in out
    assert '3 other cues nearby' in out


def test_unambiguous_snap_has_no_candidates_note():
    snap = {'end': {'label': 'outro', 'original': 201.0}}
    out = _format_cue_section(audio_analysis=None, ad_start=100.0, ad_end=200.0,
                               cue_snap=snap)
    assert 'CUE SNAP APPLIED' in out
    assert 'other cues nearby' not in out

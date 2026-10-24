"""Unit tests for per-template cue cut markers (#874)."""
from ad_detector.cue_cut_ads import synthesize_ads_from_cue_matches as synth
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _cue(start, end, cue_type='show_intro', cut=None, conf=0.9, tid=1, source='template'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': source, 'label': 'ding', 'template_id': tid,
                 'cue_type': cue_type, 'cut_matches': cut},
    )


def _result(*sigs):
    r = AudioAnalysisResult()
    r.signals = list(sigs)
    return r


def test_emits_marker_with_contract_shape():
    ads, diag = synth([], _result(_cue(10.0, 14.0, cut=True)), False, 0.5)
    assert len(ads) == 1 and diag == {}
    assert ads[0] == {
        'start': 10.0, 'end': 14.0, 'confidence': 0.9,
        'reason': 'audio_cue_match', 'detection_stage': 'cue_match',
        'category': 'intro',
        'cue_match': {'template_id': 1, 'label': 'ding',
                      'cue_type': 'show_intro', 'cue_start': 10.0},
    }


def test_category_mapping():
    expected = {'show_intro': 'intro', 'show_outro': 'outro',
                'ad_break_boundary': 'sponsor', 'ad_break_start': 'sponsor',
                'ad_break_end': 'sponsor'}
    for i, (cue_type, cat) in enumerate(expected.items()):
        ads, _ = synth([], _result(_cue(100.0 * (i + 1), 100.0 * (i + 1) + 2,
                                        cue_type=cue_type, cut=True)), False, 0.5)
        assert ads[0]['category'] == cat


def test_content_transition_never_cuts():
    r = _result(_cue(10.0, 12.0, cue_type='content_transition', cut=True))
    assert synth([], r, True, 0.5)[0] == []


def test_template_off_beats_feed_on():
    assert synth([], _result(_cue(10.0, 12.0, cut=False)), True, 0.5)[0] == []


def test_feed_on_applies_to_inherit():
    assert len(synth([], _result(_cue(10.0, 12.0, cut=None)), True, 0.5)[0]) == 1


def test_inherit_with_feed_off_emits_nothing():
    assert synth([], _result(_cue(10.0, 12.0, cut=None)), False, 0.5)[0] == []


def test_overlapping_existing_ad_skipped_with_diagnostic():
    existing = [{'start': 8.0, 'end': 60.0}]
    ads, diag = synth(existing, _result(_cue(10.0, 12.0, cut=True)), False, 0.5)
    assert ads == existing
    assert diag == {(1, 10.0): 'covered_by_existing_ad'}


def test_near_miss_llm_ad_does_not_suppress_cut():
    existing = [{'start': 2.0, 'end': 7.4}]
    ads, diag = synth(existing, _result(_cue(10.0, 10.4, cut=True)), False, 0.5)
    assert len(ads) == 2 and diag == {}
    existing = [{'start': 2.0, 'end': 10.2}]
    assert synth(existing, _result(_cue(10.0, 10.4, cut=True)), False, 0.5)[0] == existing


def test_below_confidence_skipped_with_diagnostic():
    ads, diag = synth([], _result(_cue(10.0, 12.0, cut=True, conf=0.4)), False, 0.5)
    assert ads == []
    assert diag == {(1, 10.0): 'below_pair_confidence'}


def test_spectral_cue_ignored_and_output_sorted():
    r = _result(_cue(500.0, 502.0, cue_type='show_outro', cut=True),
                _cue(5.0, 7.0, cut=True, source='spectral'),
                _cue(20.0, 22.0, cut=True, tid=2))
    ads, _ = synth([{'start': 200.0, 'end': 260.0}], r, False, 0.5)
    assert [a['start'] for a in ads] == [20.0, 200.0, 500.0]


def test_no_analysis_returns_input():
    ads = [{'start': 1.0, 'end': 2.0}]
    assert synth(ads, None, True, 0.5) == (ads, {})

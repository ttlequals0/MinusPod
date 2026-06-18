"""Unit tests for cue-pair ad synthesis (#350)."""
from ad_detector.cue_pair_ads import synthesize_ads_from_cue_pairs
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.9, label='ding', template_id=1):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue',
        confidence=conf,
        details={'source': 'template', 'label': label, 'template_id': template_id},
    )


def test_pair_synthesizes_ad_when_no_existing_ad_overlaps():
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(220.0, 220.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.55
    assert ads[0]['end'] == 219.95
    assert ads[0]['reason'] == 'audio_cue_pair'
    assert ads[0]['detection_stage'] == 'cue_pair'
    assert ads[0]['cue_pair']['start']['label'] == 'ding'


def test_pair_skipped_when_existing_ad_covers_it():
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(220.0, 220.5),
    )
    existing = [{'start': 105.0, 'end': 215.0}]
    ads = synthesize_ads_from_cue_pairs(existing, result)
    # Existing ad covers the pair; no synthesis.
    assert len(ads) == 1
    assert ads[0] is existing[0]


def test_pair_within_min_break_skipped():
    # 1.5 s gap is too short to be a break.
    result = _result_with(_cue(100.0, 100.5), _cue(102.0, 102.5))
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_pair_beyond_max_break_skipped():
    result = _result_with(_cue(100.0, 100.5), _cue(800.0, 800.5))
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_low_confidence_cue_excluded():
    result = _result_with(
        _cue(100.0, 100.5, conf=0.7),
        _cue(220.0, 220.5, conf=0.95),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_three_cues_form_one_pair_then_orphan():
    # Cues 1+2 form the break, cue 3 has no partner -> ignored.
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(200.0, 200.5),
        _cue(900.0, 900.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.55
    assert ads[0]['end'] == 199.95


def test_four_cues_form_two_pairs():
    # Two complete brackets: (1,2) and (3,4).
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(200.0, 200.5),
        _cue(900.0, 900.5),
        _cue(1100.0, 1100.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 2
    assert ads[0]['start'] == 100.55
    assert ads[1]['start'] == 900.55


def test_no_result_returns_input_unchanged():
    existing = [{'start': 100.0, 'end': 160.0}]
    ads = synthesize_ads_from_cue_pairs(existing, None)
    assert ads == existing

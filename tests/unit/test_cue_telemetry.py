"""Unit tests for the cue detection telemetry builder (#350 follow-up)."""
from ad_detector.cue_telemetry import build_cue_detection_records
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _tcue(start, end, template_id=1, label='ding', cue_type='ad_break_boundary',
          role='boundary', score=0.85, conf=0.92):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'template_id': template_id, 'label': label,
                 'cue_type': cue_type, 'role': role, 'score': score},
    )


def _spectral(start, end, conf=0.9):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'prominence_db': 8.0},
    )


def test_pair_outcome_recorded():
    result = _result_with(_tcue(100.0, 100.5), _tcue(300.0, 300.5))
    ads = [{'start': 100.55, 'end': 299.95,
            'cue_pair': {'start': {'template_id': 1, 'cue_start': 100.0},
                         'end': {'template_id': 1, 'cue_start': 300.0}}}]
    recs = build_cue_detection_records(ads, result)
    assert [r['outcome'] for r in recs] == ['pair', 'pair']
    assert recs[0]['match_score'] == 0.85


def test_snap_outcome_recorded():
    result = _result_with(_tcue(98.0, 99.5))
    ads = [{'start': 99.55, 'end': 160.0,
            'cue_snap': {'start': {'template_id': 1, 'cue_start': 98.0}}}]
    recs = build_cue_detection_records(ads, result)
    assert recs[0]['outcome'] == 'snap'


def test_unused_cue_outcome_none():
    result = _result_with(_tcue(500.0, 500.5))
    recs = build_cue_detection_records([], result)
    assert recs[0]['outcome'] == 'none'


def test_spectral_cue_not_recorded():
    result = _result_with(_spectral(100.0, 100.5), _tcue(300.0, 300.5))
    recs = build_cue_detection_records([], result)
    assert len(recs) == 1
    assert recs[0]['source'] == 'template'


def test_no_analysis_returns_empty():
    assert build_cue_detection_records([{'start': 0, 'end': 1}], None) == []

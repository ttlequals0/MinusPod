"""Unit tests for the detected-cues merge (#350 follow-up)."""
from audio_analysis.detected_cues import build_detected_cues


def _cue(start, end, **details):
    return {'start': start, 'end': end, 'signal_type': 'audio_cue', 'details': details}


def test_merges_and_labels_sources():
    cues = [
        _cue(100.0, 100.5, source='template', label='ding',
             cue_type='ad_break_boundary', score=0.88),
        _cue(300.0, 300.4, source='spectral', prominence_db=9.2),
    ]
    loud = [{'start': 500.0, 'end': 500.3, 'prominenceDb': 11.1}]
    out = build_detected_cues(cues, loud)
    assert [o['source'] for o in out] == ['template', 'spectral', 'loud_spot']
    assert out[0]['cueType'] == 'ad_break_boundary' and out[0]['score'] == 0.88
    assert out[2]['prominenceDb'] == 11.1


def test_loud_spot_near_cue_is_deduped():
    cues = [_cue(100.0, 100.5, source='spectral')]
    loud = [{'start': 100.2, 'end': 100.6, 'prominenceDb': 10.0},
            {'start': 800.0, 'end': 800.3, 'prominenceDb': 8.0}]
    starts = [o['start'] for o in build_detected_cues(cues, loud)]
    assert 100.2 not in starts   # within tolerance of the cue -> dropped
    assert 800.0 in starts


def test_sorted_and_limited():
    loud = [{'start': float(i), 'end': i + 0.2, 'prominenceDb': 5.0} for i in range(150)]
    out = build_detected_cues([], loud, limit=10)
    assert len(out) == 10
    assert out == sorted(out, key=lambda x: x['start'])


def test_empty_inputs():
    assert build_detected_cues([], []) == []

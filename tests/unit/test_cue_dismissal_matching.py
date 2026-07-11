"""mark_dismissed_candidates: fingerprint-window suppression, synthetic data."""
from audio_analysis.cue_candidates import mark_dismissed_candidates

# A distinctive 8-int window embedded twice in an otherwise-quiet fingerprint.
# 1 int = 1 "second" here (duration == len), keeping span math trivial.
_WINDOW = [0xDEADBEEF, 0xCAFEBABE, 0x12345678, 0x9ABCDEF0,
           0x0F0F0F0F, 0xF0F0F0F0, 0x55555555, 0xAAAAAAAA]


def _target():
    ints = [(i * 2654435761) % (2 ** 32) for i in range(100)]
    ints[20:28] = _WINDOW
    ints[70:78] = _WINDOW
    return (ints, float(len(ints)))


def test_marks_overlapping_candidates():
    candidates = [
        {'start': 20.0, 'end': 28.0, 'kind': 'recurring'},   # first occurrence
        {'start': 71.0, 'end': 76.0, 'kind': 'recurring'},   # inside second
        {'start': 40.0, 'end': 45.0, 'kind': 'recurring'},   # elsewhere
    ]
    dismissals = [{'id': 7, 'raw_ints': list(_WINDOW)}]
    marked = mark_dismissed_candidates(candidates, dismissals, _target(), 0.95)
    assert marked == 2
    assert candidates[0]['dismissed'] is True and candidates[0]['dismissalId'] == 7
    assert candidates[1]['dismissed'] is True
    assert candidates[1]['dismissalId'] == 7
    assert 'dismissed' not in candidates[2]


def test_no_dismissals_no_change():
    candidates = [{'start': 20.0, 'end': 28.0}]
    assert mark_dismissed_candidates(candidates, [], _target(), 0.95) == 0
    assert 'dismissed' not in candidates[0]


def test_empty_window_skipped():
    candidates = [{'start': 20.0, 'end': 28.0}]
    marked = mark_dismissed_candidates(
        candidates, [{'id': 1, 'raw_ints': []}], _target(), 0.95)
    assert marked == 0

"""transcript_gaps: uncovered stretches between transcript segments."""
from utils.text import transcript_gaps


def _seg(start, end):
    return {'start': start, 'end': end, 'text': 'x'}


def test_gap_at_or_over_the_minimum_is_reported():
    segs = [_seg(0.0, 10.0), _seg(18.0, 20.0), _seg(25.0, 30.0)]
    assert transcript_gaps(segs, 8.0) == [(10.0, 18.0)]


def test_a_long_segment_covers_later_short_ones():
    # Running max of ends: the 0-100 segment covers the 10-20 to 40-50 pause.
    segs = [_seg(0.0, 100.0), _seg(10.0, 20.0), _seg(40.0, 50.0), _seg(120.0, 130.0)]
    assert transcript_gaps(segs, 8.0) == [(100.0, 120.0)]


def test_no_segments_no_gaps():
    assert transcript_gaps([], 8.0) == []
    assert transcript_gaps([_seg(0.0, 5.0)], 1.0) == []

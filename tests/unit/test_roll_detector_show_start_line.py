"""roll_detector.show_start_line: the line in the episode head where the show itself starts."""
from tests.app_bootstrap import bootstrap

bootstrap('roll_detector_show_start_line_test_')

import roll_detector


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


SEGMENTS = [_seg(0.0, 20.0, 'Get twenty percent off at acme.com with code GARDEN'),
            _seg(20.0, 30.0, 'Welcome back to Garden Hours, I am your host'),
            _seg(30.0, 60.0, 'Today we talk about soil')]


def test_first_line_naming_the_show():
    assert roll_detector.show_start_line(SEGMENTS, 'Garden Hours: Soil and Seeds') == (20.0, 30.0)


def test_show_start_phrase_without_the_name():
    segments = [SEGMENTS[0], _seg(20.0, 30.0, 'Hello and welcome, everyone'), SEGMENTS[2]]
    assert roll_detector.show_start_line(segments, 'Completely Different Name') == (20.0, 30.0)


def test_nothing_past_the_preroll_window():
    late = [_seg(0.0, 10.0, 'ad'), _seg(10.0 + roll_detector.MAX_PREROLL_DURATION, 200.0,
                                        'Welcome back to Garden Hours')]
    assert roll_detector.show_start_line(late, 'Garden Hours') is None


def test_no_segments_or_no_show_line():
    assert roll_detector.show_start_line([], 'Garden Hours') is None
    assert roll_detector.show_start_line(SEGMENTS[:1] + SEGMENTS[2:], '') is None
    # Without a name the show-start phrases still mark the line.
    assert roll_detector.show_start_line(SEGMENTS, '') == (20.0, 30.0)

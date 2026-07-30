"""Windowed chapter boundary detection.

A single call over a multi-hour transcript both capped the count (a hardcoded
min(duration / 600, 6)) and clustered the boundaries it did return, leaving long
untouched stretches at the end. Detection now runs a window at a time.
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('chapters_windowed_test_')

import database  # noqa: E402
from chapters_generator import ChaptersGenerator  # noqa: E402
from config import (  # noqa: E402
    STAGE_TUNABLE_PAYLOAD_KEYS, resolve_chapter_geometry,
)
from llm_client import invalidate_provider_cache  # noqa: E402


def _set(key, value):
    database.Database().set_setting(key, str(value))
    # get_stage_tunable reads through llm_client's 5s TTL cache, so a write
    # here is invisible until that is flushed.
    invalidate_provider_cache()


def _clear_geometry():
    for key in ('chapter_target_seconds', 'chapter_window_seconds',
                'chapter_max_boundaries', 'chapter_min_duration_seconds'):
        database.Database().set_setting(key, '')
    invalidate_provider_cache()


class ScriptedBoundaries:
    """Returns one boundary per call, placed inside the requested window."""

    def __init__(self, fail_on=(), empty_on=()):
        self.calls = []
        self.fail_on = set(fail_on)
        self.empty_on = set(empty_on)

    def __call__(self, transcript, start, end, num_splits,
                 episode_description=None, hints=None, previous_title=None):
        index = len(self.calls)
        self.calls.append({
            'start': start, 'end': end, 'num_splits': num_splits,
            'previous_title': previous_title,
        })
        if index in self.fail_on:
            raise RuntimeError('llm exploded')
        if index in self.empty_on:
            return []
        return [{'original_time': start + (end - start) / 2,
                 'title': f'Topic {index}'}]


def _segments(duration=10000, step=10):
    return [
        {'start': i, 'end': i + step,
         'text': f'segment {i} with plenty of words to clear the transcript '
                 'length gate a b c d e f g h i j k l m n o p'}
        for i in range(0, duration, step)
    ]


def _generator(detector):
    gen = ChaptersGenerator(api_key='test')
    gen._detect_topic_boundaries = detector
    return gen


def test_a_long_episode_is_split_into_windows():
    _clear_geometry()
    detector = ScriptedBoundaries()
    gen = _generator(detector)
    gen._detect_boundaries_windowed(_segments(10000), 10000)
    # 10000s at the 2700s default window is four windows.
    assert len(detector.calls) == 4
    assert detector.calls[0]['start'] == 0
    assert detector.calls[1]['start'] == 2700


def test_windows_do_not_overlap_or_leave_gaps():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    for a, b in zip(detector.calls, detector.calls[1:]):
        assert a['end'] == b['start']
    assert detector.calls[-1]['end'] == 10000


def test_boundaries_requested_scale_with_the_target():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    # A 2700s window at a 600s target asks for four.
    assert detector.calls[0]['num_splits'] == 4


def test_a_smaller_target_asks_for_more_boundaries():
    _clear_geometry()
    _set('chapter_target_seconds', 300)
    detector = ScriptedBoundaries()
    _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    assert detector.calls[0]['num_splits'] == 9
    _clear_geometry()


def test_previous_window_title_is_passed_forward():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    assert detector.calls[0]['previous_title'] is None
    assert detector.calls[1]['previous_title'] == 'Topic 0'


def test_one_failing_window_keeps_the_others():
    _clear_geometry()
    detector = ScriptedBoundaries(fail_on=(1,))
    gen = _generator(detector)
    found = gen._detect_boundaries_windowed(_segments(10000), 10000)
    assert len(found) == 3
    assert gen._topic_detection_failed is True


def test_one_empty_window_is_not_a_failure():
    """A stretch can genuinely be a single topic."""
    _clear_geometry()
    detector = ScriptedBoundaries(empty_on=(2,))
    gen = _generator(detector)
    found = gen._detect_boundaries_windowed(_segments(10000), 10000)
    assert len(found) == 3
    assert gen._topic_detection_failed is False


def test_every_window_empty_is_a_failure():
    _clear_geometry()
    detector = ScriptedBoundaries(empty_on=(0, 1, 2, 3))
    gen = _generator(detector)
    assert gen._detect_boundaries_windowed(_segments(10000), 10000) == []
    assert gen._topic_detection_failed is True


def test_the_boundary_cap_stops_further_windows():
    _clear_geometry()
    _set('chapter_max_boundaries', 2)
    detector = ScriptedBoundaries()
    found = _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    assert len(found) == 2
    assert len(detector.calls) == 2
    _clear_geometry()


def test_a_short_tail_window_is_skipped():
    """A trailing stretch shorter than one target chapter has no boundary to
    find, so it does not earn an LLM call."""
    _clear_geometry()
    detector = ScriptedBoundaries()
    # 2700 + 100: the second window is 100s, well under the 600s target.
    _generator(detector)._detect_boundaries_windowed(_segments(2800), 2800)
    assert len(detector.calls) == 1


def test_the_old_hardcoded_cap_is_gone():
    """A long episode must be able to exceed the previous ceiling of 6."""
    _clear_geometry()
    _set('chapter_window_seconds', 600)
    _set('chapter_target_seconds', 600)
    detector = ScriptedBoundaries()
    found = _generator(detector)._detect_boundaries_windowed(_segments(10000), 10000)
    assert len(found) > 6
    _clear_geometry()


def test_geometry_keys_are_registered_for_the_api():
    payload = {p for p, _, _ in STAGE_TUNABLE_PAYLOAD_KEYS}
    for key in ('chapterTargetSeconds', 'chapterWindowSeconds',
                'chapterMaxBoundaries', 'chapterMinDurationSeconds'):
        assert key in payload


class TestGeometryClamping:
    """resolve_chapter_geometry clamps, so a stored or env-supplied combination
    that never saw the API's cross-field check still yields workable geometry.
    """

    def test_target_is_clamped_to_the_window(self):
        _clear_geometry()
        _set('chapter_target_seconds', 3600)
        _set('chapter_window_seconds', 900)
        target, window, _, _ = resolve_chapter_geometry()
        assert target == 900
        assert window == 900
        _clear_geometry()

    def test_min_duration_is_clamped_to_the_target(self):
        _clear_geometry()
        _set('chapter_target_seconds', 120)
        _set('chapter_min_duration_seconds', 900)
        target, _, _, min_duration = resolve_chapter_geometry()
        assert min_duration <= target
        _clear_geometry()

    def test_defaults_pass_through_unclamped(self):
        _clear_geometry()
        target, window, max_boundaries, min_duration = resolve_chapter_geometry()
        assert (target, window, max_boundaries, min_duration) == (600, 2700, 40, 180)

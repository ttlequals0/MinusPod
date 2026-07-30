"""Windowed chapter boundary detection.

A single call over a multi-hour transcript both capped the count (a hardcoded
min(duration / 600, 6)) and clustered the boundaries it did return, leaving long
untouched stretches at the end. Detection now runs a window at a time.
"""

from types import SimpleNamespace

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('chapters_windowed_test_')

import database  # noqa: E402
from chapters_generator import ChaptersGenerator, _format_hints_block  # noqa: E402
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
    """Returns one boundary per call, placed inside the requested window.

    Mirrors the real contract: None for a failed LLM call, [] for a
    legitimately empty window."""

    def __init__(self, fail_on=(), empty_on=()):
        self.calls = []
        self.fail_on = set(fail_on)
        self.empty_on = set(empty_on)

    def __call__(self, transcript, start, end, num_splits,
                 episode_description=None, anchors=None, hints=None,
                 previous_title=None):
        index = len(self.calls)
        self.calls.append({
            'start': start, 'end': end, 'num_splits': num_splits,
            'episode_description': episode_description, 'anchors': anchors,
            'hints': hints, 'previous_title': previous_title,
        })
        if index in self.fail_on:
            return None
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


def _windowed(gen, duration=10000, **kwargs):
    # Callers pass geometry in; resolve it here the way generate_chapters does.
    target, window, max_boundaries, _ = resolve_chapter_geometry()
    return gen._detect_boundaries_windowed(
        _segments(duration), duration, target, window, max_boundaries, **kwargs)


def test_a_long_episode_is_split_into_windows():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector))
    # 10000s at the 2700s default window is four windows.
    assert len(detector.calls) == 4
    assert detector.calls[0]['start'] == 0
    assert detector.calls[1]['start'] == 2700


def test_windows_do_not_overlap_or_leave_gaps():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector))
    for a, b in zip(detector.calls, detector.calls[1:]):
        assert a['end'] == b['start']
    assert detector.calls[-1]['end'] == 10000


def test_boundaries_requested_scale_with_the_target():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector))
    # A 2700s window at a 600s target asks for four.
    assert detector.calls[0]['num_splits'] == 4


def test_a_smaller_target_asks_for_more_boundaries():
    _clear_geometry()
    _set('chapter_target_seconds', 300)
    detector = ScriptedBoundaries()
    _windowed(_generator(detector))
    assert detector.calls[0]['num_splits'] == 9
    _clear_geometry()


def test_previous_window_title_is_passed_forward():
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector))
    assert detector.calls[0]['previous_title'] is None
    assert detector.calls[1]['previous_title'] == 'Topic 0'


def test_description_anchors_reach_their_window():
    """Show-notes anchors past the first window must not be lost, and each
    window gets only its own range."""
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector), episode_description='55:00 Late Topic')
    anchors_by_call = [c['anchors'] for c in detector.calls]
    assert anchors_by_call[0] == []
    assert ('55:00', 'Late Topic') in anchors_by_call[1]


def test_anchorless_description_goes_to_window_zero_only():
    """Prose without timestamps repeats no tokens across later windows."""
    _clear_geometry()
    detector = ScriptedBoundaries()
    _windowed(_generator(detector), episode_description='A show about tech.')
    assert detector.calls[0]['episode_description'] == 'A show about tech.'
    assert all(c['episode_description'] is None for c in detector.calls[1:])


def test_hints_are_filtered_to_each_window():
    _clear_geometry()
    hints = [
        {'type': 'seam', 'time': 100.0, 'category': 'sponsor'},
        {'type': 'range', 'start': 3000.0, 'end': 3030.0, 'category': 'sponsor'},
    ]
    detector = ScriptedBoundaries()
    _windowed(_generator(detector), hints=hints)
    assert detector.calls[0]['hints'] == [hints[0]]
    assert detector.calls[1]['hints'] == [hints[1]]
    assert detector.calls[2]['hints'] == []


def test_one_failing_window_keeps_the_others():
    _clear_geometry()
    detector = ScriptedBoundaries(fail_on=(1,))
    gen = _generator(detector)
    found = _windowed(gen)
    assert len(found) == 3
    assert gen._topic_detection_failed is True


def test_one_empty_window_is_not_a_failure():
    """A stretch can genuinely be a single topic."""
    _clear_geometry()
    detector = ScriptedBoundaries(empty_on=(2,))
    gen = _generator(detector)
    found = _windowed(gen)
    assert len(found) == 3
    assert gen._topic_detection_failed is False


def test_every_window_empty_is_a_failure():
    _clear_geometry()
    detector = ScriptedBoundaries(empty_on=(0, 1, 2, 3))
    gen = _generator(detector)
    assert _windowed(gen) == []
    assert gen._topic_detection_failed is True


def test_every_window_failing_is_a_failure():
    _clear_geometry()
    detector = ScriptedBoundaries(fail_on=(0, 1, 2, 3))
    gen = _generator(detector)
    assert _windowed(gen) == []
    assert gen._topic_detection_failed is True


def test_the_boundary_cap_stops_further_windows():
    _clear_geometry()
    _set('chapter_max_boundaries', 2)
    detector = ScriptedBoundaries()
    found = _windowed(_generator(detector))
    assert len(found) == 2
    assert len(detector.calls) == 2
    _clear_geometry()


def test_a_short_tail_window_is_skipped():
    """A trailing stretch shorter than one target chapter has no boundary to
    find, so it does not earn an LLM call."""
    _clear_geometry()
    detector = ScriptedBoundaries()
    # 2700 + 100: the second window is 100s, well under the 600s target.
    _windowed(_generator(detector), duration=2800)
    assert len(detector.calls) == 1


def test_the_old_hardcoded_cap_is_gone():
    """A long episode must be able to exceed the previous ceiling of 6."""
    _clear_geometry()
    _set('chapter_window_seconds', 600)
    _set('chapter_target_seconds', 600)
    detector = ScriptedBoundaries()
    found = _windowed(_generator(detector))
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


def test_range_hint_straddling_a_seam_reaches_both_windows():
    _clear_geometry()
    hints = [{'type': 'range', 'start': 2690.0, 'end': 2712.0,
              'category': 'sponsor'}]
    detector = ScriptedBoundaries()
    _windowed(_generator(detector), hints=hints)
    assert detector.calls[0]['hints'] == hints
    assert detector.calls[1]['hints'] == hints


class _PromptCapture:
    """Stub LLM client recording the prompt _detect_topic_boundaries sends."""

    def __init__(self):
        self.prompts = []

    def messages_create(self, **kwargs):
        self.prompts.append(kwargs['messages'][0]['content'])
        return SimpleNamespace(content='05:30 Captured Topic')


def _capture_prompt(**kwargs):
    gen = ChaptersGenerator(api_key='test')
    stub = _PromptCapture()
    gen._llm_client = stub
    gen._detect_topic_boundaries(
        kwargs.pop('transcript', '[00:10] hello world'),
        kwargs.pop('start', 0.0), kwargs.pop('end', 2700.0),
        kwargs.pop('num_splits', 4), **kwargs)
    return stub.prompts[0]


class TestChapterPromptSetting:
    """The chapter topic-detection prompt is an editable setting rendered via
    render_prompt/apply_override, matching the other prompt settings."""

    def teardown_method(self):
        db = database.Database()
        db.reset_setting('chapter_prompt')
        db.set_setting('chapter_prompt_override', '', is_default=True)

    def test_custom_prompt_setting_reaches_the_llm(self):
        database.Database().set_setting(
            'chapter_prompt', 'Find {num_splits} topics in: {transcript}',
            is_default=False)
        assert _capture_prompt() == 'Find 4 topics in: [00:10] hello world'

    def test_override_is_appended(self):
        database.Database().set_setting(
            'chapter_prompt_override', 'Prefer interview boundaries.',
            is_default=False)
        assert _capture_prompt().endswith(
            'ADDITIONAL INSTRUCTIONS (these take precedence):\n'
            'Prefer interview boundaries.')

    def test_default_render_matches_the_old_fstring(self):
        """With neither setting customized, the rendered prompt must be
        byte-identical to what the pre-setting f-string produced."""
        hints = [{'type': 'seam', 'time': 100.0, 'category': 'sponsor'}]
        prompt = _capture_prompt(hints=hints, previous_title='Ad Talk')

        transcript = '[00:10] hello world'
        num_splits, start_time, end_time = 4, 0.0, 2700.0
        continuation_block = (
            '\n\nThis segment continues the chapter "Ad Talk". Do not '
            'emit a boundary for that same topic; only for a change away from it.')
        description_block = ''
        hints_block = _format_hints_block(hints)
        expected = f"""Analyze this podcast transcript segment and identify {num_splits} major topic changes.

The segment runs from {int(start_time/60)}:{int(start_time%60):02d} to {int(end_time//60)}:{int(end_time%60):02d}.

For each topic change, provide the timestamp (from the [MM:SS] markers) and a short title (3-7 words).

OUTPUT FORMAT:
Return ONLY topic lines, one per line. No introduction, no explanation, no numbering.
Each line must be exactly: MM:SS Topic Title Here

Example:
05:30 Discussion of AI Trends
12:45 New Product Announcements

Only include clear topic transitions, not minor tangents. Skip the very beginning since that's already a chapter.{continuation_block}{description_block}{hints_block}

Transcript:
{transcript}"""
        assert prompt == expected


class TestChapterPromptInjection:
    def test_description_text_cannot_inject_placeholders(self):
        """A feed-controlled value containing '{transcript}' stays literal."""
        from utils.prompt import render_prompt_once
        out = render_prompt_once(
            'A {description_block} B {transcript}',
            description_block='evil {transcript} evil',
            transcript='THE TRANSCRIPT',
        )
        assert out == 'A evil {transcript} evil B THE TRANSCRIPT'

    def test_override_position_comes_from_the_template_only(self):
        """'{override}' inside rendered values is inert; only the operator's
        template places the override."""
        from utils.prompt import render_prompt_once, apply_override
        template = apply_override('Base {transcript}', 'EXTRA')
        out = render_prompt_once(template, transcript='has {override} inside')
        assert out.count('EXTRA') == 1
        assert '{override}' in out

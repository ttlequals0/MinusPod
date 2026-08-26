"""Recurrence hint wiring: per-window injection into the detection prompt."""
import json
from unittest.mock import patch

from ad_detector import AdDetector
from text_recurrence import format_recurrence_hint


def test_get_recent_original_segments_parses_json(temp_db):
    # temp_db fixture: tests/conftest.py:23 (fresh Database per test).
    # upsert_episode kwargs style: tests/unit/test_positional_prior.py:386.
    slug = 'recur-pod'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Recur Pod')
    for i, eid in enumerate(('ep-1', 'ep-2')):
        temp_db.upsert_episode(slug, eid, title=f'Episode {i}',
                               status='processed', original_duration=1800.0,
                               published_at=f'2026-08-{20 + i:02d}T00:00:00Z')
        temp_db.save_original_segments(slug, eid, [
            {'start': 0.0, 'end': 5.0, 'text': f'hello from {eid}'}])
    lists = temp_db.get_recent_original_segments(slug, limit=5)
    assert len(lists) == 2
    assert lists[0][0]['text'].startswith('hello from')
    # exclude_episode_id filters the current episode out
    assert len(temp_db.get_recent_original_segments(
        slug, exclude_episode_id='ep-2')) == 1


def test_window_prompt_contains_hint_only_for_overlapping_window():
    spans = [{'start': 0.0, 'end': 12.0, 'text': 'welcome to the show'}]
    assert 'welcome to the show' in format_recurrence_hint(spans, 0.0, 600.0)
    assert format_recurrence_hint(spans, 1200.0, 1800.0) == ""


class _StubResponse:
    """LLMResponse-shaped duck: the parse path reads .content and .usage."""

    def __init__(self, content):
        self.content = content
        self.usage = {}


def _window(start=0.0, end=60.0):
    return {
        'start': start, 'end': end,
        'segments': [{'start': start, 'end': start + 1.0, 'text': 'hello'}],
    }


def _run_window(detector, *, recurrence_spans, captured):
    def fake_call(*, prompt, window_label, **_kw):
        captured.append(prompt)
        return _StubResponse(json.dumps([])), None

    with patch.object(detector, '_call_llm_for_window', side_effect=fake_call):
        detector._process_single_window(
            window_idx=0, window=_window(), total_windows=1,
            model='m', system_prompt='sys', description_section='',
            podcast_name='p', episode_title='t',
            audio_enforcer=None, audio_analysis=None,
            llm_timeout=30, max_retries=1,
            slug='s', episode_id='e', pass_name='pass1',
            window_label_prefix='Window', validate_timestamps=False,
            recurrence_spans=recurrence_spans,
        )


def test_process_single_window_injects_recurrence_hint_only_when_spans_given():
    detector = AdDetector(api_key='test-key')
    spans = [{'start': 0.0, 'end': 30.0, 'text': 'welcome to the show'}]

    with_spans = []
    _run_window(detector, recurrence_spans=spans, captured=with_spans)
    assert 'RECURRING BOILERPLATE' in with_spans[0]
    assert 'welcome to the show' in with_spans[0]

    without_spans = []
    _run_window(detector, recurrence_spans=None, captured=without_spans)
    assert 'RECURRING BOILERPLATE' not in without_spans[0]

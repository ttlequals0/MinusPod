"""Tests for chapters_generator topic-boundary prompt construction."""
import logging
import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from chapters_generator import (
    ChaptersGenerator, _parse_description_anchors, TOPIC_DETECTION_TEMPERATURE,
    build_segment_hints,
)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Chapter LLM calls go through utils.llm_call, which sleeps between
    retries. Stubs returning empty content are treated as retryable failures
    there, so neutralize the sleeps to keep these tests fast."""
    monkeypatch.setattr('utils.llm_call.time.sleep', lambda _s: None)


@dataclass
class _StubResponse:
    content: str
    model: str = 'stub-model'
    usage: dict = None
    raw_response: object = None


class _RecordingClient:
    """Stub LLM client that records every prompt + kwargs it was asked to send."""

    def __init__(self, canned_text: str = ''):
        self.canned_text = canned_text
        self.prompts: list = []
        self.calls: list = []

    def messages_create(self, **kwargs):
        self.prompts.append(kwargs['messages'][0]['content'])
        self.calls.append(kwargs)
        return _StubResponse(content=self.canned_text)

    @property
    def last_prompt(self) -> str:
        return self.prompts[-1] if self.prompts else ''

    @property
    def topic_prompt(self) -> str:
        """Return the first prompt (topic-detection), raising if absent."""
        for p in self.prompts:
            if 'identify' in p and 'major topic changes' in p:
                return p
        return ''


def _make_generator_with_stub(canned_text: str = '') -> tuple:
    gen = ChaptersGenerator(api_key='test')
    stub = _RecordingClient(canned_text=canned_text)
    gen._llm_client = stub
    return gen, stub


def _long_episode_segments(duration: int = 7200) -> list:
    """Segments for an episode long enough (> MIN_DURATION_FOR_AI) and with
    enough transcript text (> 500 chars) to trigger topic-boundary
    detection."""
    return [
        {'start': i, 'end': i + 10,
         'text': f'segment {i} with enough words to exceed the five '
                 'hundred char transcript gate a b c d e f g'}
        for i in range(0, duration, 10)
    ]


class TestDetectTopicBoundariesPromptSize:
    """Full transcript must reach the LLM (no 8,000-char cap)."""

    def test_long_transcript_not_truncated(self):
        # 40,000 chars of transcript with a unique marker well past the 8k mark.
        filler_before = 'a ' * 6000  # 12,000 chars
        marker = '[TAIL_MARKER_XYZ]'
        filler_after = 'b ' * 10000  # 20,000 chars
        transcript = f'[00:00] start\n{filler_before}\n[60:00] {marker}\n{filler_after}'
        assert len(transcript) > 8000

        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript=transcript,
            start_time=0.0,
            end_time=7200.0,
            num_splits=6,
        )

        assert marker in stub.last_prompt, (
            'Marker past byte 8000 must appear in the prompt; '
            'otherwise transcript is being truncated.'
        )
        assert transcript in stub.last_prompt, (
            'Full transcript must appear verbatim in the prompt.'
        )

    def test_num_splits_reaches_prompt(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] hello world',
            start_time=0.0,
            end_time=120.0,
            num_splits=4,
        )
        assert 'identify 4 major topic changes' in stub.last_prompt


class TestDetectTopicBoundariesParsing:
    """LLM output parsing: valid MM:SS lines become chapters, noise is dropped."""

    def test_parses_mmss_lines_within_range(self):
        canned = "05:30 Opening segment\n45:00 Guest interview\n90:15 Closing\n"
        gen, _ = _make_generator_with_stub(canned_text=canned)
        chapters = gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=7200.0,
            num_splits=3,
        )
        assert [c['title'] for c in chapters] == [
            'Opening segment', 'Guest interview', 'Closing',
        ]
        assert [int(c['original_time']) for c in chapters] == [330, 2700, 5415]

    def test_rejects_timestamps_outside_range(self):
        canned = "05:30 Inside\n99:99 garbage\n200:00 Outside\n"
        gen, _ = _make_generator_with_stub(canned_text=canned)
        chapters = gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
        )
        assert [c['title'] for c in chapters] == ['Inside']


class TestDetectTopicBoundariesDescription:
    """Episode description is injected into the prompt with an ordering instruction."""

    def test_description_reaches_prompt_when_provided(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
            episode_description='00:00 Intro\n05:30 Main\n15:00 Guest',
        )
        # Description contains parseable anchors -> candidate-boundary path.
        assert '00:00 Intro' in stub.last_prompt
        assert '15:00 Guest' in stub.last_prompt
        assert 'Candidate boundaries from show notes' in stub.last_prompt

    def test_no_description_block_when_empty(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
            episode_description=None,
        )
        assert 'Episode description' not in stub.last_prompt
        assert 'prefer those timestamps' not in stub.last_prompt

    def test_whitespace_only_description_is_ignored(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
            episode_description='   \n\t  ',
        )
        assert 'Episode description' not in stub.last_prompt


class TestAdjustSegmentsForAds:
    """Raw segments + ads_removed must project onto the post-ad-removal timeline."""

    def test_no_ads_returns_segments_unchanged(self):
        gen = ChaptersGenerator(api_key='test')
        segs = [{'start': 0, 'end': 10, 'text': 'a'},
                {'start': 10, 'end': 20, 'text': 'b'}]
        assert gen._adjust_segments_for_ads(segs, []) == segs
        assert gen._adjust_segments_for_ads(segs, None) == segs

    def test_drops_segments_entirely_inside_ad(self):
        gen = ChaptersGenerator(api_key='test')
        segs = [
            {'start': 0, 'end': 10, 'text': 'before'},
            {'start': 15, 'end': 25, 'text': 'inside-ad'},
            {'start': 40, 'end': 50, 'text': 'after'},
        ]
        ads = [{'start': 10, 'end': 30}]
        out = gen._adjust_segments_for_ads(segs, ads)
        texts = [s['text'] for s in out]
        assert 'inside-ad' not in texts
        assert texts == ['before', 'after']

    def test_shifts_post_ad_segments_by_ad_duration(self):
        gen = ChaptersGenerator(api_key='test')
        segs = [
            {'start': 0, 'end': 10, 'text': 'before'},
            {'start': 40, 'end': 50, 'text': 'after'},
        ]
        ads = [{'start': 10, 'end': 30}]
        out = gen._adjust_segments_for_ads(segs, ads)
        assert out[0]['start'] == 0 and out[0]['end'] == 10
        assert out[1]['start'] == 20 and out[1]['end'] == 30


class TestGenerateChaptersUnifiedEntryPoint:
    """Both pipeline and regen call the same method and get the same shape."""

    def _segments(self, duration: int = 1200):
        """Build a synthetic segment list covering `duration` seconds with enough text."""
        return [
            {'start': i, 'end': i + 10,
             'text': f'segment {i} with enough words to exceed the five hundred char gate a b c d e f g'}
            for i in range(0, duration, 10)
        ]

    def test_empty_segments_returns_empty_chapters(self):
        gen, _ = _make_generator_with_stub(canned_text='')
        out = gen.generate_chapters([])
        assert out == {'version': '1.2.0', 'chapters': []}

    def test_regen_path_no_ads_removed(self):
        gen, stub = _make_generator_with_stub(canned_text='10:00 Middle section\n')
        segs = self._segments(duration=1800)
        out = gen.generate_chapters(
            segments=segs,
            episode_description=None,
            podcast_name='Show',
            episode_title='Ep',
        )
        assert out['version'] == '1.2.0'
        assert len(out['chapters']) >= 1
        assert 'Transcript:' in stub.topic_prompt

    def test_pipeline_path_applies_ad_adjustment(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        # Build enough segments so the post-adjustment transcript exceeds the
        # 500-char gate and _detect_topic_boundaries actually runs.
        text_filler = 'words ' * 20  # 120 chars/segment
        segs = [
            {'start': i, 'end': i + 10, 'text': f'pre {text_filler}'}
            for i in range(0, 500, 10)
        ] + [
            {'start': 500, 'end': 560, 'text': 'ad body should be dropped'},
        ] + [
            {'start': i, 'end': i + 10, 'text': f'post {text_filler}'}
            for i in range(560, 1400, 10)
        ]
        ads = [{'start': 500, 'end': 560}]
        gen.generate_chapters(
            segments=segs,
            ads_removed=ads,
            podcast_name='Show',
            episode_title='Ep',
        )
        topic_prompt = stub.topic_prompt
        assert topic_prompt, 'Topic-detection prompt must have been sent'
        # Post-adjustment: the segment originally at 560 should now start at 500 (08:20).
        assert '[08:20] post' in topic_prompt
        assert 'ad body should be dropped' not in topic_prompt


class TestParseDescriptionAnchors:
    """Deterministic show-note timestamp extraction."""

    def test_extracts_plain_mmss_lines(self):
        desc = "00:00 Intro\n05:30 Main topic\n15:00 Guest interview"
        assert _parse_description_anchors(desc) == [
            ('00:00', 'Intro'),
            ('05:30', 'Main topic'),
            ('15:00', 'Guest interview'),
        ]

    def test_extracts_bracketed_format(self):
        desc = "Show notes:\n[00:30] Welcome\n[12:45] Deep dive"
        result = dict(_parse_description_anchors(desc))
        assert result['00:30'] == 'Welcome'
        assert result['12:45'] == 'Deep dive'

    def test_extracts_parenthesized_format(self):
        desc = "(0:00) Intro\n(5:30) Topic A"
        result = dict(_parse_description_anchors(desc))
        assert result['0:00'] == 'Intro'
        assert result['5:30'] == 'Topic A'

    def test_strips_html_wrappers(self):
        desc = "<p>00:00 Intro</p><br/>05:30 Main<br>15:00 Guest"
        result = dict(_parse_description_anchors(desc))
        assert result['00:00'] == 'Intro'
        assert result['05:30'] == 'Main'
        assert result['15:00'] == 'Guest'

    def test_empty_when_no_timestamps(self):
        desc = "Just a regular description with no timestamps."
        assert _parse_description_anchors(desc) == []

    def test_empty_for_none_or_blank(self):
        assert _parse_description_anchors(None) == []
        assert _parse_description_anchors('') == []
        assert _parse_description_anchors('   \n  ') == []

    def test_sorted_by_time(self):
        desc = "15:00 Late\n05:30 Mid\n00:00 Start"
        anchors = _parse_description_anchors(desc)
        assert [ts for ts, _ in anchors] == ['00:00', '05:30', '15:00']

    def test_drops_too_short_or_numeric_titles(self):
        desc = "00:00 A\n05:30 12345\n10:00 Real Title"
        result = dict(_parse_description_anchors(desc))
        assert '00:00' not in result  # title too short
        assert '05:30' not in result  # title is digits only
        assert result['10:00'] == 'Real Title'


class TestDescriptionAnchorPromptInjection:
    """Anchors found in the description go into the prompt as candidate boundaries."""

    def test_anchors_inject_candidate_block(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
            episode_description="00:00 Intro\n05:30 Main\n15:00 Guest",
        )
        prompt = stub.last_prompt
        assert 'Candidate boundaries from show notes:' in prompt
        assert '00:00 Intro' in prompt
        assert '05:30 Main' in prompt
        assert '15:00 Guest' in prompt
        assert 'Episode description:' not in prompt

    def test_no_anchors_falls_back_to_plain_description(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
            episode_description="A discussion about modern podcasting and AI.",
        )
        prompt = stub.last_prompt
        assert 'Candidate boundaries from show notes:' not in prompt
        assert 'Episode description:' in prompt
        assert 'A discussion about modern podcasting' in prompt


class TestTopicDetectionTemperature:
    """Topic detection runs at the low TOPIC_DETECTION_TEMPERATURE constant."""

    def test_temperature_passed_to_llm(self):
        assert TOPIC_DETECTION_TEMPERATURE == 0.1

        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=3,
        )
        assert stub.calls, 'LLM must have been called'
        assert stub.calls[-1]['temperature'] == TOPIC_DETECTION_TEMPERATURE


class TestChapterTunablesAreConfigDriven:
    """Verify chapters_generator reads from per-stage config, not the legacy
    literals/constants. Pin this so a future refactor can't silently put
    `temperature=0.3` back in place.
    """

    def test_boundary_temperature_reads_from_config(self, monkeypatch):
        from llm_client import _clear_provider_cache
        _clear_provider_cache()
        monkeypatch.setenv('CHAPTER_BOUNDARY_TEMPERATURE', '0.05')

        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
        )
        _clear_provider_cache()
        assert stub.calls[-1]['temperature'] == 0.05

    def test_title_temperature_reads_from_config(self, monkeypatch):
        from llm_client import _clear_provider_cache
        _clear_provider_cache()
        monkeypatch.setenv('CHAPTER_TITLE_TEMPERATURE', '0.9')

        gen, stub = _make_generator_with_stub(canned_text='Title One')
        gen._call_claude_for_titles(
            chapter_requests=[{'index': 0, 'excerpt': 'hello', 'position': 'start'}],
            podcast_name='p', episode_title='e',
        )
        _clear_provider_cache()
        assert stub.calls[-1]['temperature'] == 0.9

    def test_boundary_max_tokens_reads_from_config(self, monkeypatch):
        from llm_client import _clear_provider_cache
        _clear_provider_cache()
        monkeypatch.setenv('CHAPTER_BOUNDARY_MAX_TOKENS', '512')

        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
        )
        _clear_provider_cache()
        assert stub.calls[-1]['max_tokens'] == 512


class TestAdjustSegmentsReplacementDuration:
    def test_shift_compensates_for_beep_insertion(self):
        gen = ChaptersGenerator(api_key='test')
        segs = [
            {'start': 0, 'end': 10, 'text': 'before'},
            {'start': 40, 'end': 50, 'text': 'after'},
        ]
        ads = [{'start': 10, 'end': 30}]
        out = gen._adjust_segments_for_ads(segs, ads, replacement_duration=2.0)
        assert out[1]['start'] == 22 and out[1]['end'] == 32


class _FailingClient:
    """Stub client whose messages_create always raises, counting attempts."""

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def messages_create(self, **kwargs):
        self.calls += 1
        raise self.error


class TestSharedLLMCallPath:
    """Chapter LLM calls must go through utils.llm_call (retries + error
    classification) and degrade gracefully when every retry fails."""

    def _failing_generator(self):
        gen = ChaptersGenerator(api_key='test')
        client = _FailingClient(RuntimeError('transient 500'))
        gen._llm_client = client
        return gen, client

    def test_boundary_failure_retries_then_returns_no_boundaries(self):
        gen, client = self._failing_generator()
        with patch('utils.llm_call.is_retryable_error', return_value=True), \
             patch('utils.llm_call.calculate_backoff', return_value=0.0), \
             patch('chapters_generator.get_llm_max_retries', return_value=2):
            chapters = gen._detect_topic_boundaries(
                transcript='[00:00] x',
                start_time=0.0,
                end_time=1800.0,
                num_splits=3,
            )
        assert chapters == []
        # 3 primary attempts (max_retries=2) + 2 secondary fallback retries.
        assert client.calls == 5

    def test_title_failure_retries_then_degrades_to_generic_titles(self):
        gen, client = self._failing_generator()
        chapters = [
            {'startTime': 0, 'title': None, 'source': 'auto', 'needs_title': True},
            {'startTime': 300, 'title': None, 'source': 'ai', 'needs_title': True},
        ]
        segments = [{'start': 0, 'end': 600, 'text': 'hello world'}]
        with patch('utils.llm_call.is_retryable_error', return_value=True), \
             patch('utils.llm_call.calculate_backoff', return_value=0.0), \
             patch('chapters_generator.get_llm_max_retries', return_value=2):
            out = gen.generate_chapter_titles(chapters, segments, 'Show', 'Ep')
        assert client.calls == 5
        assert out[0]['title'] == 'Introduction'
        assert out[1]['title'] == 'Part 1'
        assert not any(ch['needs_title'] for ch in out)
        assert gen._title_generation_failed is True

    def test_non_retryable_failure_fails_once_and_degrades(self):
        gen, client = self._failing_generator()
        with patch('utils.llm_call.is_retryable_error', return_value=False), \
             patch('chapters_generator.get_llm_max_retries', return_value=2):
            chapters = gen._detect_topic_boundaries(
                transcript='[00:00] x',
                start_time=0.0,
                end_time=1800.0,
                num_splits=3,
            )
        assert chapters == []
        assert client.calls == 1

    def test_full_pipeline_degradation_reported_on_generator(self, caplog):
        """Reproduces the production incident (#530 follow-up): both
        topic-detection and title-generation LLM calls fail on a long
        episode, so it degrades to a single whole-episode chapter. That must
        be visible on the generator instance (chapters_degraded / reason)
        and logged with the 'degraded to a single chapter' wording, not look
        like a normal short episode."""
        gen = ChaptersGenerator(api_key='test')
        client = _FailingClient(RuntimeError('temperature rejected'))
        gen._llm_client = client

        segments = _long_episode_segments()

        with patch('utils.llm_call.is_retryable_error', return_value=False), \
             caplog.at_level(logging.WARNING, logger='chapters_generator'):
            out = gen.generate_chapters(
                segments=segments,
                podcast_name='Show',
                episode_title='Ep',
                episode_id='ep-degraded',
            )

        assert out['chapters'] == [{'startTime': 1, 'title': 'Introduction'}]
        assert gen.chapters_degraded is True
        assert 'topic detection failed' in gen.chapters_degradation_reason
        assert 'title generation failed' in gen.chapters_degradation_reason
        assert any(
            'chapters degraded to a single chapter' in rec.message
            for rec in caplog.records
        )


class _SplitTextClient:
    """Stub client returning unparseable prose for topic-detection prompts
    and a real title otherwise -- isolates the topic-detection gap from
    title-generation failure."""

    def messages_create(self, **kwargs):
        prompt = kwargs['messages'][0]['content']
        if 'major topic changes' in prompt:
            text = "Nothing here resembles a clean topic transition."
        else:
            text = "Introduction"
        return _StubResponse(content=text)


class TestEmptyTopicDetectionDegradation:
    """A response that succeeds at the API level but yields zero parsed
    boundaries (e.g. empty content, or nothing matching the timestamp
    pattern) must be treated the same as a failed detection call on a long
    episode -- silently returning a single whole-episode chapter with no
    degraded flag hides the failure from operators (matches the live
    incident: 87-minute episode, 'Generated 1 chapters', no warning)."""

    def test_empty_result_on_long_episode_flags_degraded(self, caplog):
        """response is non-None and non-empty (so call_llm's own
        EmptyCompletionError retry path never kicks in) but nothing in it
        matches the MM:SS topic-line pattern, e.g. because extended-thinking
        output drifted from the requested format. _detect_topic_boundaries
        returns [] with no exception; that must still degrade the run."""
        gen = ChaptersGenerator(api_key='test')
        gen._llm_client = _SplitTextClient()

        with caplog.at_level(logging.WARNING, logger='chapters_generator'):
            out = gen.generate_chapters(
                segments=_long_episode_segments(duration=5400),
                podcast_name='Show',
                episode_title='Ep',
                episode_id='ep-empty-topics',
            )

        assert out['chapters'] == [{'startTime': 1, 'title': 'Introduction'}]
        assert gen.chapters_degraded is True
        assert gen.chapters_degradation_reason
        assert any(
            'chapters degraded to a single chapter' in rec.message
            for rec in caplog.records
        )

    def test_raising_topic_detection_still_flags_degraded(self, monkeypatch):
        """Existing exception-path behavior must be unchanged."""
        gen = ChaptersGenerator(api_key='test')
        gen._llm_client = object()
        monkeypatch.setattr(
            gen, '_detect_topic_boundaries',
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')),
        )

        out = gen.generate_chapters(
            segments=_long_episode_segments(duration=5400),
            podcast_name='Show',
            episode_title='Ep',
            episode_id='ep-raising',
        )

        assert out['chapters'] == [{'startTime': 1, 'title': 'Introduction'}]
        assert gen.chapters_degraded is True
        assert 'topic detection failed' in gen.chapters_degradation_reason

    def test_short_episode_single_chapter_not_flagged(self):
        """A genuinely short episode never reaches AI topic detection
        (MIN_DURATION_FOR_AI gate); ending up with one chapter there is
        normal, not degradation."""
        gen, stub = _make_generator_with_stub(canned_text='Intro Title')
        segments = [{'start': 0, 'end': 300, 'text': 'short episode'}]

        out = gen.generate_chapters(
            segments=segments,
            podcast_name='Show',
            episode_title='Ep',
            episode_id='ep-short',
        )

        assert out['chapters'] == [{'startTime': 1, 'title': 'Intro Title'}]
        assert gen.chapters_degraded is False
        # AI topic detection is never invoked below MIN_DURATION_FOR_AI.
        assert stub.topic_prompt == ''

    def test_chapter_calls_do_not_force_json_response_format(self):
        # Chapter prompts expect line-based text; the JSON-object response
        # format is a detection-window concern (call_llm_for_window only).
        gen, stub = _make_generator_with_stub(canned_text='05:30 Topic A\n')
        gen._detect_topic_boundaries(
            transcript='[00:00] x',
            start_time=0.0,
            end_time=1800.0,
            num_splits=1,
        )
        assert stub.calls[-1].get('response_format') is None


# Same mixed remove+beep applied cut list as
# tests/unit/test_beep_timeline_mapping.py's MIXED_CUTS, cross-checked there
# against utils.time.adjust_timestamp/merge_cut_spans directly.
_MIXED_CUTS = [
    {'start': 100.0, 'end': 130.0, 'replacement_duration': 2.0},   # remove
    {'start': 200.0, 'end': 210.0, 'replacement_duration': 10.0},  # beep
]


class TestBuildSegmentHints:
    """build_segment_hints maps markers onto the processed timeline via the
    existing utils.time.adjust_timestamp mapping helper."""

    def test_remove_marker_becomes_single_seam(self):
        markers = [{'start': 100.0, 'end': 130.0, 'action_applied': 'remove',
                    'category': 'sponsor'}]
        hints = build_segment_hints(markers, _MIXED_CUTS)
        assert hints == [{'type': 'seam', 'time': pytest.approx(100.0),
                          'category': 'sponsor'}]

    def test_beep_marker_becomes_range(self):
        markers = [{'start': 200.0, 'end': 210.0, 'action_applied': 'beep',
                    'category': 'cross_promo'}]
        hints = build_segment_hints(markers, _MIXED_CUTS)
        assert len(hints) == 1
        assert hints[0]['type'] == 'range'
        assert hints[0]['start'] == pytest.approx(172.0)
        assert hints[0]['end'] == pytest.approx(182.0)
        assert hints[0]['category'] == 'cross_promo'

    def test_keep_marker_becomes_range(self):
        # 400s sits after both cuts: 28s shift from the remove span (30 - 2
        # clip), zero extra from the beep span (10s span, 10s replacement).
        markers = [{'start': 400.0, 'end': 420.0, 'action_applied': 'keep',
                    'category': 'self_promo'}]
        hints = build_segment_hints(markers, _MIXED_CUTS)
        assert hints[0]['type'] == 'range'
        assert hints[0]['start'] == pytest.approx(372.0)
        assert hints[0]['end'] == pytest.approx(392.0)

    def test_mixed_remove_and_beep_markers_both_present(self):
        markers = [
            {'start': 100.0, 'end': 130.0, 'action_applied': 'remove', 'category': 'sponsor'},
            {'start': 200.0, 'end': 210.0, 'action_applied': 'beep', 'category': 'intro'},
        ]
        hints = build_segment_hints(markers, _MIXED_CUTS)
        assert [h['type'] for h in hints] == ['seam', 'range']
        assert hints[0]['category'] == 'sponsor'
        assert hints[1]['category'] == 'intro'

    def test_pending_review_marker_has_no_action_applied_and_is_skipped(self):
        # Markers held for review are never stamped with action_applied
        # until resolved; they must not leak into hints as if resolved.
        markers = [{'start': 100.0, 'end': 130.0, 'category': 'sponsor',
                    'held_for_review': True}]
        assert build_segment_hints(markers, _MIXED_CUTS) == []

    def test_no_markers_returns_empty(self):
        assert build_segment_hints(None, _MIXED_CUTS) == []
        assert build_segment_hints([], _MIXED_CUTS) == []

    def test_no_cuts_maps_identity(self):
        # No applied cuts at all (e.g. an episode with only kept segments):
        # processed time equals original time.
        markers = [{'start': 50.0, 'end': 60.0, 'action_applied': 'keep',
                    'category': 'sponsor'}]
        hints = build_segment_hints(markers, None)
        assert hints[0]['start'] == 50.0
        assert hints[0]['end'] == 60.0

    def test_unknown_category_normalizes_to_sponsor(self):
        markers = [{'start': 100.0, 'end': 130.0, 'action_applied': 'remove',
                    'category': 'not_a_real_category'}]
        hints = build_segment_hints(markers, _MIXED_CUTS)
        assert hints[0]['category'] == 'sponsor'


class TestSegmentHintsPromptInjection:
    """The topic-detection prompt gets a hints block only when hints exist,
    and is otherwise byte-identical to before hints existed."""

    def test_hints_block_present_when_hints_given(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        hints = [
            {'type': 'seam', 'time': 90.0, 'category': 'sponsor'},
            {'type': 'range', 'start': 300.0, 'end': 330.0, 'category': 'cross_promo'},
        ]
        gen._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
            hints=hints,
        )
        prompt = stub.last_prompt
        assert 'Detected ad/segment positions:' in prompt
        assert '01:30 ad/segment break (sponsor)' in prompt
        assert '05:00-05:30 ad/segment (cross_promo)' in prompt
        assert 'CANDIDATE boundaries' in prompt
        assert 'not' in prompt.lower() and 'just because' in prompt

    def test_no_hints_block_when_hints_none(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
            hints=None,
        )
        assert 'Detected ad/segment positions:' not in stub.last_prompt

    def test_prompt_byte_identical_with_empty_hints(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
        )
        baseline = stub.last_prompt

        gen2, stub2 = _make_generator_with_stub(canned_text='')
        gen2._detect_topic_boundaries(
            transcript='[00:00] x', start_time=0.0, end_time=1800.0, num_splits=3,
            hints=[],
        )
        assert stub2.last_prompt == baseline


class TestGenerateChaptersHintsWiring:
    """generate_chapters composes hints from segment_markers/marker_cuts and
    threads them into the topic-detection call -- a synthetic end-to-end
    check on the composed prompt, not on model behavior."""

    def _segments(self, duration: int = 1800) -> list:
        return [
            {'start': i, 'end': i + 10,
             'text': f'segment {i} with enough words to exceed the five hundred char gate a b c d e f g'}
            for i in range(0, duration, 10)
        ]

    def test_segment_markers_reach_topic_prompt(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        markers = [{'start': 300.0, 'end': 330.0, 'action_applied': 'remove',
                    'category': 'sponsor'}]
        gen.generate_chapters(
            segments=self._segments(),
            podcast_name='Show', episode_title='Ep',
            segment_markers=markers,
        )
        assert 'Detected ad/segment positions:' in stub.topic_prompt
        assert '05:00 ad/segment break (sponsor)' in stub.topic_prompt

    def test_no_segment_markers_no_hints_block(self):
        gen, stub = _make_generator_with_stub(canned_text='')
        gen.generate_chapters(
            segments=self._segments(),
            podcast_name='Show', episode_title='Ep',
        )
        assert stub.topic_prompt, 'Topic-detection prompt must have been sent'
        assert 'Detected ad/segment positions:' not in stub.topic_prompt

    def test_marker_cuts_used_instead_of_ads_removed_for_hint_mapping(self):
        """Regen-endpoint call shape: ads_removed is omitted (segments are
        already ad-adjusted), marker_cuts is supplied separately just to map
        hint positions."""
        gen, stub = _make_generator_with_stub(canned_text='')
        markers = [{'start': 100.0, 'end': 130.0, 'action_applied': 'remove',
                    'category': 'sponsor'}]
        cuts = [{'start': 100.0, 'end': 130.0, 'replacement_duration': 2.0}]
        gen.generate_chapters(
            segments=self._segments(),
            podcast_name='Show', episode_title='Ep',
            segment_markers=markers, marker_cuts=cuts,
        )
        # Nothing precedes this cut, so the seam maps to its own start (100s).
        assert '01:40 ad/segment break (sponsor)' in stub.topic_prompt

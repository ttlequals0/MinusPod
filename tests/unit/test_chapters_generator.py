"""Tests for chapters_generator topic-boundary prompt construction."""
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from chapters_generator import ChaptersGenerator, _parse_description_anchors, TOPIC_DETECTION_TEMPERATURE


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

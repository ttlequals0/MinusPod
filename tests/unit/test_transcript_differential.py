"""Tests for the upstream transcript fetch, parsers and aligner."""
import random
import time
from pathlib import Path

import pytest
import requests

import transcript_differential as td
from transcript_differential import (
    MAX_UPSTREAM_TRANSCRIPT_BYTES,
    UpstreamTranscript,
    align,
    fetch_upstream_transcript,
    parse_transcript,
    spans_overlapping,
    upstream_tokens,
    whisper_tokens,
)
from utils.safe_http import ResponseTooLargeError

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures' / 'upstream_transcript'
URL = 'https://cdn.example.com/ep1/transcript'
WORD_S = 0.4


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _texts(t: UpstreamTranscript) -> list[str]:
    return [c['text'] for c in t.cues]


# ---------- parsers ----------

class TestParsers:
    def test_vtt_strips_voice_tags_speakers_entities_and_notes(self):
        t = parse_transcript(_fixture('sample.vtt'), 'text/vtt', URL)
        assert t.timed is True
        assert t.mime == 'text/vtt'
        assert t.source_url == URL
        assert _texts(t) == [
            'Welcome back to the show, everyone.',
            'Today we talk about & around compilers.',
            "It's really fun.",
        ]
        assert t.cues[0]['start'] == 1.0 and t.cues[0]['end'] == 4.5
        assert t.cues[2]['start'] == 62.25 and t.cues[2]['end'] == 65.0

    def test_srt_multiline_cue_and_hour_times(self):
        t = parse_transcript(_fixture('sample.srt'), 'application/srt', URL)
        assert t.timed is True
        assert t.mime == 'application/srt'
        assert _texts(t) == [
            'Welcome back to the show, everyone.',
            'Today we talk about compilers.',
            'Goodbye.',
        ]
        assert t.cues[2]['start'] == 3600.0 and t.cues[2]['end'] == 3602.0

    def test_podcast_json_segments_skip_empty_bodies(self):
        t = parse_transcript(_fixture('sample.json'), 'application/json', URL)
        assert t.timed is True
        assert _texts(t) == ['Welcome back to the show, everyone.',
                             'Today we talk about compilers.']
        assert t.cues[1] == {'start': 4.5, 'end': 8.0,
                             'text': 'Today we talk about compilers.'}

    def test_plain_text_is_untimed_and_drops_speakers_and_timestamps(self):
        t = parse_transcript(_fixture('sample.txt'), 'text/plain', URL)
        assert t.timed is False
        assert all(c['start'] is None and c['end'] is None for c in t.cues)
        assert _texts(t) == ['Welcome back to the show, everyone.',
                             'Today we talk about compilers.',
                             'And timestamps get dropped.']

    def test_html_strips_tags_head_scripts_and_entities(self):
        t = parse_transcript(_fixture('sample.html'), 'text/html', URL)
        assert t.timed is False
        joined = ' '.join(_texts(t))
        assert 'Welcome back to the show, everyone.' in joined
        assert 'compilers & tools.' in joined
        assert 'Second line here.' in joined
        for absent in ('Leo', 'Ann', 'not words', 'color', 'Transcript', '<'):
            assert absent not in joined

    def test_mime_with_charset_parameter(self):
        t = parse_transcript(_fixture('sample.vtt'), 'text/vtt; charset=utf-8', URL)
        assert t.mime == 'text/vtt'

    def test_empty_or_garbage_bodies_return_none(self):
        assert parse_transcript(b'', 'text/vtt', URL) is None
        assert parse_transcript(b'WEBVTT\n\n', None, URL) is None
        assert parse_transcript(b'{"version": "1"}', 'application/json', URL) is None
        assert parse_transcript(b'{not json', 'application/json', URL) is None
        assert parse_transcript(b'\xff\xfe\x00\x00', None, URL) is None


class TestMimeSniffing:
    @pytest.mark.parametrize('name, mime, timed', [
        ('sample.vtt', 'text/vtt', True),
        ('sample.srt', 'application/srt', True),
        ('sample.json', 'application/json', True),
        ('sample.html', 'text/html', False),
        ('sample.txt', 'text/plain', False),
    ])
    def test_sniffs_format_when_mime_is_none(self, name, mime, timed):
        t = parse_transcript(_fixture(name), None, URL)
        assert t is not None
        assert t.mime == mime
        assert t.timed is timed

    def test_strong_signature_beats_a_wrong_declared_mime(self):
        t = parse_transcript(_fixture('sample.vtt'), 'text/plain', URL)
        assert t.mime == 'text/vtt'
        assert t.timed is True

    def test_utf8_bom_is_tolerated(self):
        t = parse_transcript(b'\xef\xbb\xbf' + _fixture('sample.vtt'), None, URL)
        assert t.mime == 'text/vtt'


# ---------- fetch ----------

def _response(body: bytes, status: int = 200, content_type: str | None = None):
    response = requests.Response()
    response.status_code = status
    response._content = body
    if content_type:
        response.headers['Content-Type'] = content_type
    response.iter_content = lambda chunk_size=65536: iter(
        [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)] or [b'']
    )
    response.close = lambda: None
    return response


class TestFetch:
    def test_happy_path_uses_declared_mime(self, monkeypatch):
        seen = {}

        def _get(url, **kwargs):
            seen.update(kwargs, url=url)
            return _response(_fixture('sample.vtt'))
        monkeypatch.setattr('transcript_differential.safe_get', _get)

        t = fetch_upstream_transcript(URL, 'text/vtt')

        assert t.mime == 'text/vtt' and t.source_url == URL and len(t.cues) == 3
        assert seen['url'] == URL
        assert seen['stream'] is True
        assert seen['trust'] == td.URLTrust.FEED_CONTENT
        assert 'User-Agent' in seen['headers']

    def test_falls_back_to_response_content_type(self, monkeypatch):
        monkeypatch.setattr('transcript_differential.safe_get', lambda *a, **k: _response(
            _fixture('sample.json'), content_type='application/json; charset=utf-8'))
        assert fetch_upstream_transcript(URL, None).mime == 'application/json'

    def test_network_error_returns_none(self, monkeypatch):
        def _boom(*a, **k):
            raise requests.ConnectionError('refused')
        monkeypatch.setattr('transcript_differential.safe_get', _boom)
        assert fetch_upstream_transcript(URL, 'text/vtt') is None

    def test_http_error_returns_none(self, monkeypatch):
        monkeypatch.setattr('transcript_differential.safe_get',
                            lambda *a, **k: _response(b'oops', status=500))
        assert fetch_upstream_transcript(URL, 'text/vtt') is None

    def test_oversized_body_returns_none(self, monkeypatch):
        def _too_big(*a, **k):
            raise ResponseTooLargeError('too big')
        monkeypatch.setattr('transcript_differential.safe_get',
                            lambda *a, **k: _response(b'WEBVTT'))
        monkeypatch.setattr('transcript_differential.read_response_capped', _too_big)
        assert fetch_upstream_transcript(URL, 'text/vtt') is None

    def test_cap_constant(self):
        assert MAX_UPSTREAM_TRANSCRIPT_BYTES == 5 * 1024 * 1024

    def test_unparseable_body_returns_none(self, monkeypatch):
        monkeypatch.setattr('transcript_differential.safe_get',
                            lambda *a, **k: _response(b'{"nope": 1}'))
        assert fetch_upstream_transcript(URL, 'application/json') is None

    def test_empty_url_returns_none(self):
        assert fetch_upstream_transcript('', 'text/vtt') is None


# ---------- tokens ----------

class TestTokens:
    def test_whisper_tokens_use_word_times(self):
        segs = [{'start': 0.0, 'end': 2.0, 'text': " Don't stop",
                 'words': [{'word': " Don't", 'start': 0.0, 'end': 0.6},
                           {'word': ' stop.', 'start': 0.6, 'end': 1.1}]}]
        assert whisper_tokens(segs) == [('don', 0.0, 0.6), ('t', 0.0, 0.6),
                                        ('stop', 0.6, 1.1)]

    def test_whisper_tokens_interpolate_without_words(self):
        segs = [{'start': 10.0, 'end': 14.0, 'text': 'One two, three four.'}]
        assert whisper_tokens(segs) == [('one', 10.0, 11.0), ('two', 11.0, 12.0),
                                        ('three', 12.0, 13.0), ('four', 13.0, 14.0)]

    def test_whisper_tokens_interpolate_when_word_times_missing(self):
        segs = [{'start': 0.0, 'end': 2.0, 'text': 'a b',
                 'words': [{'word': 'a'}, {'word': 'b'}]}]
        assert whisper_tokens(segs) == [('a', 0.0, 1.0), ('b', 1.0, 2.0)]

    def test_upstream_tokens_interpolate_inside_timed_cues(self):
        t = UpstreamTranscript(cues=[{'start': 10.0, 'end': 12.0, 'text': 'Hello there'}],
                               timed=True, source_url=URL, mime='text/vtt')
        assert upstream_tokens(t) == [('hello', 10.0), ('there', 11.0)]

    def test_upstream_tokens_untimed(self):
        t = UpstreamTranscript(cues=[{'start': None, 'end': None, 'text': 'Hello there'}],
                               timed=False, source_url=URL, mime='text/plain')
        assert upstream_tokens(t) == [('hello', None), ('there', None)]


# ---------- alignment ----------

def _content(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return [f'w{rng.randrange(3000)}' for _ in range(n)]


def _build(content: list[str], inserts: dict[int, list[str]] | None = None, *,
           insert_word_s: float = WORD_S, upstream_keeps_ad_time: bool = False,
           timed: bool = True, drop: set[int] | None = None):
    """Whisper segments (content plus inserted blocks) and an upstream transcript of the content.

    inserts maps a content index to words heard before it; drop removes content
    indices from the upstream side only.
    """
    inserts = inserts or {}
    drop = drop or set()
    words = []
    up = []
    t_whisper = 0.0
    t_up = 0.0
    for i in range(len(content) + 1):
        for w in inserts.get(i, []):
            words.append({'word': f' {w}', 'start': t_whisper, 'end': t_whisper + insert_word_s})
            t_whisper += insert_word_s
            if upstream_keeps_ad_time:
                t_up += insert_word_s
        if i == len(content):
            break
        words.append({'word': f' {content[i]}', 'start': t_whisper, 'end': t_whisper + WORD_S})
        if i not in drop:
            up.append((content[i], t_up))
        t_whisper += WORD_S
        t_up += WORD_S
    segments = []
    for k in range(0, len(words), 10):
        chunk = words[k:k + 10]
        segments.append({'start': chunk[0]['start'], 'end': chunk[-1]['end'],
                         'text': ''.join(w['word'] for w in chunk), 'words': chunk})
    cues = []
    for k in range(0, len(up), 10):
        chunk = up[k:k + 10]
        cues.append({'start': chunk[0][1] if timed else None,
                     'end': chunk[-1][1] + WORD_S if timed else None,
                     'text': ' '.join(w for w, _ in chunk)})
    transcript = UpstreamTranscript(cues=cues, timed=timed, source_url=URL,
                                    mime='text/vtt' if timed else 'text/plain')
    return segments, transcript


def _ad(n: int, tag: str = 'ad') -> list[str]:
    return [f'{tag}{k}' for k in range(n)]


class TestAlign:
    def test_inserted_45s_block_found_with_word_exact_bounds(self):
        content = _content(3000)  # 20 min at 0.4 s per word
        segs, tr = _build(content, {1500: _ad(112)})  # 112 * 0.4 = 44.8 s

        result = align(segs, tr)

        assert result['status'] == 'ok'
        assert result['timed'] is True
        assert result['coverage'] == pytest.approx(3000 / 3112, abs=1e-6)
        assert len(result['spans']) == 1
        span = result['spans'][0]
        assert span['start'] == pytest.approx(1500 * WORD_S)
        assert span['end'] == pytest.approx(1500 * WORD_S + 112 * WORD_S)
        assert span['words'] == 112
        assert span['offset_confirmed'] is True
        assert span['text_preview'] == ' '.join(_ad(12))

    def test_filler_edits_do_not_create_spans(self):
        content = _content(3000)
        inserts = {i: (['um'] if i % 30 == 0 else ['you', 'know'])
                   for i in range(15, 3000, 15)}
        segs, tr = _build(content, inserts)

        result = align(segs, tr)

        assert result['status'] == 'ok'
        assert result['spans'] == []

    def test_20s_gap_below_min_words_is_ignored(self):
        content = _content(3000)
        segs, tr = _build(content, {1000: _ad(30)}, insert_word_s=20.0 / 30)
        assert align(segs, tr)['spans'] == []

    def test_gap_below_min_seconds_is_ignored(self):
        content = _content(3000)
        segs, tr = _build(content, {1000: _ad(45)}, insert_word_s=8.0 / 45)
        assert align(segs, tr)['spans'] == []

    def test_noise_tokens_inside_a_run_are_absorbed(self):
        content = _content(3000)
        # Three content words shared with upstream sit inside the ad block.
        segs, tr = _build(content, {1000: _ad(50, 'x'), 1003: _ad(50, 'y')})
        spans = align(segs, tr)['spans']
        assert len(spans) == 1
        assert spans[0]['start'] == pytest.approx(1000 * WORD_S)
        assert spans[0]['words'] == 100

    def test_adjacent_gaps_within_3s_merge(self):
        content = _content(3000)
        # 25 + 25 Whisper-only words separated by 5 matched words (2 s).
        segs, tr = _build(content, {1000: _ad(25, 'x'), 1005: _ad(25, 'y')})
        spans = align(segs, tr)['spans']
        assert len(spans) == 1
        assert spans[0]['start'] == pytest.approx(1000 * WORD_S)
        assert spans[0]['end'] == pytest.approx((1005 + 50) * WORD_S)
        assert spans[0]['words'] == 50

    def test_distant_short_gaps_do_not_merge(self):
        content = _content(3000)
        segs, tr = _build(content, {1000: _ad(25, 'x'), 1020: _ad(25, 'y')})
        assert align(segs, tr)['spans'] == []

    def test_low_coverage_is_unreliable_with_no_spans(self):
        content = _content(3000)
        segs, _ = _build(content, {1500: _ad(112)})
        # Upstream shares only the first 30 percent of the Whisper words.
        _, tr = _build(content[:934] + _content(2066, seed=99))
        result = align(segs, tr)
        assert result['status'] == 'unreliable'
        assert result['coverage'] == pytest.approx(0.3, abs=0.02)
        assert result['spans'] == []

    def test_offset_not_confirmed_when_upstream_times_do_not_jump(self):
        content = _content(3000)
        segs, tr = _build(content, {1500: _ad(112)}, upstream_keeps_ad_time=True)
        spans = align(segs, tr)['spans']
        assert len(spans) == 1
        assert spans[0]['offset_confirmed'] is False

    def test_offset_confirmed_for_a_preroll(self):
        content = _content(3000)
        segs, tr = _build(content, {0: _ad(150)})
        spans = align(segs, tr)['spans']
        assert len(spans) == 1
        assert spans[0]['start'] == 0.0
        assert spans[0]['offset_confirmed'] is True

    def test_untimed_transcript_never_confirms_offset(self):
        content = _content(3000)
        segs, tr = _build(content, {1500: _ad(112)}, timed=False)
        result = align(segs, tr)
        assert result['timed'] is False
        assert len(result['spans']) == 1
        assert result['spans'][0]['start'] == pytest.approx(1500 * WORD_S)
        assert result['spans'][0]['offset_confirmed'] is False

    def test_upstream_only_text_does_not_create_spans(self):
        content = _content(3000)
        segs, tr = _build(content, drop=set(range(1000, 1200)))
        # Whisper has extra content the transcript dropped: that IS a gap.
        assert len(align(segs, tr)['spans']) == 1
        segs2, _ = _build(content[:1000] + content[1200:])
        _, tr2 = _build(content)
        assert align(segs2, tr2)['spans'] == []

    def test_empty_inputs(self):
        _, tr = _build(_content(100))
        assert align([], tr) == {'status': 'empty', 'coverage': 0.0,
                                 'timed': True, 'spans': []}
        empty = UpstreamTranscript(cues=[], timed=False, source_url=URL, mime='text/plain')
        segs, _ = _build(_content(100))
        assert align(segs, empty)['status'] == 'empty'

    def test_30k_tokens_align_quickly(self):
        content = _content(30000, seed=3)
        segs, tr = _build(content, {5000: _ad(150, 'x'), 20000: _ad(150, 'y')})
        t0 = time.perf_counter()
        result = align(segs, tr)
        elapsed = time.perf_counter() - t0
        assert result['status'] == 'ok'
        assert len(result['spans']) == 2
        assert elapsed < 5.0, f'align took {elapsed:.2f} s'

    def test_unrelated_30k_transcripts_stay_bounded(self):
        segs, _ = _build(_content(30000, seed=3))
        _, tr = _build(_content(30000, seed=4))
        t0 = time.perf_counter()
        result = align(segs, tr)
        elapsed = time.perf_counter() - t0
        assert result['status'] == 'unreliable'
        assert elapsed < 5.0, f'align took {elapsed:.2f} s'


class TestSpansOverlapping:
    SPANS = [{'start': 100.0, 'end': 200.0}, {'start': 300.0, 'end': 340.0}]

    def test_window_inside_span(self):
        assert spans_overlapping(self.SPANS, 140.0, 160.0) == [self.SPANS[0]]

    def test_fraction_uses_the_shorter_interval(self):
        # 50 s overlap of a 100 s span and a 100 s window: exactly 0.5.
        assert spans_overlapping(self.SPANS, 150.0, 250.0) == [self.SPANS[0]]
        # 10 s overlap of a 40 s span: 0.25.
        assert spans_overlapping(self.SPANS, 330.0, 400.0) == []
        assert spans_overlapping(self.SPANS, 330.0, 400.0, min_fraction=0.25) == [self.SPANS[1]]

    def test_touching_or_degenerate_windows(self):
        assert spans_overlapping(self.SPANS, 200.0, 300.0) == []
        assert spans_overlapping(self.SPANS, 150.0, 150.0) == []
        assert spans_overlapping([], 0.0, 10.0) == []

    def test_window_covering_several_spans(self):
        assert spans_overlapping(self.SPANS, 0.0, 400.0) == self.SPANS

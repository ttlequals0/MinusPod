import logging

import pytest

from utils.segments import (
    TextSegmentLike, WordSegmentLike, is_text_segment, is_word_segment,
    validate_text_segment, validate_word_segment,
    normalize_segments, validate_word_segments,
    join_word_segments, word_spans
)

# These helpers are reused in other tests.
def make_text_segment(text: str,
                      start: float|int = 0.0,
                      end: float|int|None = None,
                      words: list[WordSegmentLike]|None = None,
                      make_words: bool = False,
                      ) -> TextSegmentLike:
    if make_words:
        assert words is None, "Cannot provide both 'words' and 'make_words=True'"
        words = make_words_from_text(text, start, end)
    if end is None:
        end = words[-1]['end'] if words else start + 1
    s: TextSegmentLike = {
        'text': text,
        'start': start,
        'end': end
    }
    if words is not None:
        s['words'] = words
    return s

def make_word_segment(word: str,
                      start: float|int,
                      end: float|int|None = None,
                      ) -> WordSegmentLike:
    end = end if end is not None else start + 1
    s: WordSegmentLike = {'word': word, 'start': start, 'end': end}
    return s

def make_words_from_text(text: str,
                         start: float|int,
                         end: float|int|None = None
                         ) -> list[WordSegmentLike]:
    """
    Generate a list of word segments from the given text, distributed evenly
    between start and end. If end is not provided, it defaults to start + len(words).
    """
    words = text.split()
    if not words:
        return []
    end = end if end is not None else start + len(words)
    delta = (end - start) / len(words)
    words = [
        make_word_segment(f" {word}",
                          round(start + i * delta, 1),
                          round(start + (i + 1) * delta, 1))
        for i, word in enumerate(words)
    ]
    words[-1]['end'] = end
    return words

def verify_text_segment(segment: TextSegmentLike,
                        text: str|None = None, 
                        start: float|None = None, 
                        end: float|None = None,
                        words: list[WordSegmentLike]|None = None,
                        **kwargs
                        ):
    assert segment is not None
    if text is not None:
        assert segment.get('text') == text
    if start is not None:
        assert segment.get('start') == start
    if end is not None:
        assert segment.get('end') == end
    if words is not None:
        assert (actual_words := segment.get('words')) is not None
        for actual, expected in zip(actual_words, words, strict=False):
            verify_word_segment(actual, **expected) # type: ignore
        assert len(actual_words) == len(words)
    for attribute,expected in kwargs.items():
        assert segment.get(attribute) == expected
    for attribute,expected in kwargs.items():
        assert segment.get(attribute) == expected

def verify_word_segment(segment: WordSegmentLike,
                        word: str|None = None, 
                        start: float|None = None, 
                        end: float|None = None,
                        **kwargs
                        ):
    assert segment is not None
    if word is not None:
        assert segment.get('word') == word
    if start is not None:
        assert segment.get('start') == start
    if end is not None:
        assert segment.get('end') == end
    for attribute,expected in kwargs.items():
        assert segment.get(attribute) == expected


class TestJoinWordSegments:
    def test_prefixed_words_join_with_continuation_tokens(self):
        words = [
            make_word_segment(' How', 0),
            make_word_segment(' y', 1),
            make_word_segment("'all", 2),
            make_word_segment(' doin?', 3),
        ]
        assert join_word_segments(words) == "How y'all doin?"

    def test_unprefixed_words_are_space_separated(self):
        words = [
            make_word_segment('How', 0),
            make_word_segment('y', 1),
            make_word_segment("'all ", 2),
            make_word_segment('doin? ', 3),
        ]
        assert join_word_segments(words) == "How y 'all doin?"

    def test_empty_input(self):
        assert word_spans([]) == ('', [])


class TestWordSegment:
    def test_is_word_segment_with_valid_input(self):
        # just the structure matters; values do not need to be valid
        words = [
            {'word': '', 'start': 0.0, 'end': 2.0},
            {'word': '', 'start': 2.0, 'end': 1.0},
            {'word': '', 'start': -1, 'end': 0},
            {'word': '', 'start': float('inf'), 'end': float('nan')},
        ]
        for word in words:
            assert is_word_segment(word)

    def test_is_word_segment_with_invalid_input(self):
        words = [
            None,
            'bad',
            {},
            {'foo': 'bar'},
            {'word': None, 'start': 1.0, 'end': 2.0},
            {'word': '', 'start': 'bad', 'end': 2.0},
            {'word': '', 'start': 1.0, 'end': 'bad'},
        ]
        for word in words:
            assert not is_word_segment(word)

    def test_validate_word_segment_accepts_valid_input(self):
        words: list[WordSegmentLike] = [
            {'word': ' x', 'start': 1.0, 'end': 3.0},
            {'word': ' x', 'start': -10, 'end': 30},
            {'word': ' x', 'start': 0, 'end': 0},
        ]
        for word in words:
            validate_word_segment(word, strict=True)

    def test_validate_word_segment_rejects_invalid_input(self):
        words: list[WordSegmentLike] = [
            {'word': '', 'start': 0.0, 'end': 1.0}, # empty word
            {'word': 'not-space-prefixed', 'start': 0.0, 'end': 1.0},
            {'word': ' negative-duration', 'start': 1.0, 'end': 0.0},
            {'word': ' infinite-start', 'start': float('-inf'), 'end': 1.0},
            {'word': ' infinite-end', 'start': 0.0, 'end': float('inf')},
            {'word': ' nan-start', 'start': float('nan'), 'end': 1.0},
            {'word': ' nan-end', 'start': 0.0, 'end': float('nan')},
        ]
        for word in words:
            try:
                validate_word_segment(word)
                assert False, f"Expected ValueError for word segment: {word}"
            except ValueError:
                pass

    def test_validate_word_segments_accepts_valid_input(self):
        words: list[tuple[str, list[WordSegmentLike]]] = [
            ("empty list", []),
            ("in order, no overlap", [
                {'word': ' a', 'start': -1.0, 'end': 3.0},
                {'word': ' b', 'start': 10, 'end': 10},
                {'word': ' c', 'start': 10.0, 'end': 10000},
            ]),
            ('ordered with overlap', [
                # this is technically invalid, but allowed
                {'word': ' a', 'start': 0.0, 'end': 2.0},
                {'word': ' b', 'start': 1.0, 'end': 2.0},
            ]),
        ]
        for description, word_list in words:
            try:
                validate_word_segments(word_list)
            except Exception as e:
                assert not e, f"Expected valid word segments for: {description}"

    def test_validate_word_segments_rejects_invalid_input(self):
        words: list[tuple[str, list[WordSegmentLike]]] = [
            ("out of order", [
                {'word': ' b', 'start': 1.0, 'end': 2.0},
                {'word': ' a', 'start': 0.0, 'end': 1.0},
            ]),
            ("out of order with overlap", [
                {'word': ' a', 'start': 1.0, 'end': 2.0},
                {'word': ' b', 'start': 0.0, 'end': 2.0},
            ]),
        ]
        for description, word_list in words:
            try:
                validate_word_segments(word_list)
                assert False, f"Expected ValueError for invalid word segments: {description}"
            except ValueError:
                pass

    def test_validate_word_segments_accepts_invalid_timing_when_not_strict(self):
        words: list[WordSegmentLike] = [
            {'word': '', 'start': 0.0, 'end': 1.0}, # empty word
            {'word': 'not-space-prefixed', 'start': 1.0, 'end': 3.0},
            {'word': 'overlapping', 'start': 1.0, 'end': 2.0},
            {'word': 'out-of-order', 'start': 0.0, 'end': 0.0},
            {'word': 'negative-duration', 'start': 100, 'end': 30},
        ]
        validate_word_segments(words, strict=False)


class TestTextSegment:
    def test_is_text_segment_with_valid_input(self):
        # just the structure matters; values do not need to be valid
        segments: list[TextSegmentLike] = [
            {'text': '', 'start': 1.0, 'end': 2.0},
            {'text': '', 'start': 1.0, 'end': 2.0, 'words': []},
            {'text': '', 'start': -1, 'end': -2},
            {'text': '', 'start': float('inf'), 'end': float('nan'),},
        ]

        for segment in segments:
            assert is_text_segment(segment)

    def test_is_text_segment_with_invalid_input(self):
        segments = [
            None,
            'bad',
            {},
            {'foo': 'bar'},
            {'text': None, 'start': 1.0, 'end': 2.0},
            {'text': 'x', 'start': 'bad', 'end': 2.0},
            {'text': 'x', 'start': 1.0, 'end': 'bad'},
            {'text': 'x', 'start': 1.0, 'end': 2.0, 'words': 'bad'},
            {'text': 'x', 'start': 1.0, 'end': 2.0, 'words': {}},
        ]

        for segment in segments:
            assert not is_text_segment(segment)

    def test_validate_text_segment_accepts_valid_input(self):
        segments: list[TextSegmentLike] = [
            {'text': 'typical', 'start': 0.0, 'end': 1.0},
            {'text': '', 'start': 0.0, 'end': 1.0}, # empty text
            {'text': 'integer timing', 'start': 10, 'end': 20},
            {'text': 'zero duration', 'start': 0.0, 'end': 0.0},
            {'text': 'negative timing', 'start': -2.0, 'end': -1.0},
            {'text': 'no words', 'start': 0.0, 'end': 1.0, 'words': []},
            {'text': 'with interpolated', 'start': 0.0, 'end': 1.0, 'interpolated': False},
            {'text': 'with terminal', 'start': 0.0, 'end': 1.0, 'terminal': False},
            {'text': 'with word timed line', 'start': 0.0, 'end': 1.0, 'word_timed_line': False},
        ]

        for segment in segments:
            validate_text_segment(segment)

    def test_validate_text_segment_rejects_invalid_input(self):
        segments: list[TextSegmentLike] = [
            {'text': 'negative duration', 'start': 2.0, 'end': 1.0},
            {'text': 'infinite start', 'start': float('-inf'), 'end': 1.0},
            {'text': 'infinite end', 'start': 0.0, 'end': float('inf')},
            {'text': 'nan start', 'start': float('nan'), 'end': 1.0},
            {'text': 'nan end', 'start': 0.0, 'end': float('nan')},
        ]

        for segment in segments:
            try:
                validate_text_segment(segment)
                assert False, f"Expected ValueError for segment: {segment}"
            except ValueError:
                pass

    def test_validate_text_segment_rejects_malformed_words(self):
        with pytest.raises(TypeError):
            validate_text_segment({'text': 'x', 'start': 1.0, 'end': 2.0, 'words': ['bad']})

    def test_validate_text_segment_allows_invalid_timing_when_not_strict(self):
        segments: list[TextSegmentLike] = [
            {'text': 'negative-duration', 'start': 10.0, 'end': 30.0},
            {'text': 'out-of-order', 'start': 0.0, 'end': 0.0},
            {'text': 'word issues', 'start': 0.0, 'end': 0.0, 'words': [
                {'word': 'negetive-duration', 'start': 1.0, 'end': 0.0},
            ]},
        ]
        for segment in segments:
            validate_text_segment(segment, strict=False)


def _words(spec):
    return [make_word_segment(w, s, e) for w, s, e in spec]


class TestNormalizeSegments:
    def test_empty_list(self):
        assert normalize_segments([]) == []

    def test_sorts_segments_and_coerces_floats(self):
        segments = normalize_segments([
            make_text_segment('  second  ', 5, 6),
            make_text_segment('first', 1, 2),
        ])
        assert [s['text'] for s in segments] == ['first', 'second']
        assert all(isinstance(s[k], float) for s in segments for k in ('start', 'end'))

    def test_clips_end_to_start(self):
        seg = normalize_segments([make_text_segment('x', 3.0, 1.0)])[0]
        assert seg['end'] == 3.0

    def test_overlapping_segments_are_kept(self):
        segments = normalize_segments([
            make_text_segment('a b', 0.0, 2.0),
            make_text_segment('c d', 1.0, 3.0),
        ])
        assert [s['text'] for s in segments] == ['a b', 'c d']

    def test_preserves_extra_fields(self):
        seg = make_text_segment('How', 0, 1, [make_word_segment(' How', 0.0, 1.0) | {'foo': 'baz'}])
        seg['foo'] = 'bar'
        out = normalize_segments([seg])[0]
        verify_text_segment(out, 'How', foo='bar', words=[{'word': ' How', 'foo': 'baz'}])

    def test_invalid_segment_dropped_with_one_warning(self, caplog):
        segments = [
            make_text_segment('good', 0, 1),
            make_text_segment('bad start', float('nan'), 0),
            make_text_segment('bad end', 0, float('inf')),
        ]
        with caplog.at_level(logging.WARNING, logger='utils.segments'):
            valid = normalize_segments(segments)
        assert valid == [segments[0]]
        warnings = [r for r in caplog.records if r.name == 'utils.segments']
        assert len(warnings) == 1
        assert 'Dropped 2' in warnings[0].getMessage()

    def test_text_and_timings_are_never_rewritten_from_words(self):
        seg = make_text_segment('Hello.', 0.0, 9.0, _words([(' Hello', 1.0, 2.0)]))
        out = normalize_segments([seg])[0]
        verify_text_segment(out, 'Hello.', 0.0, 9.0)

    def test_api_style_words_leave_text_untouched(self):
        text = "Hello, world. Visit AT&T.com today!"
        words = _words([('Hello', 0.0, 0.5), ('world', 0.6, 1.0), ('Visit', 1.1, 1.5),
                        ('AT&T.com', 1.6, 2.2), ('today', 2.3, 2.8)])
        out = normalize_segments([make_text_segment(text, 0.0, 3.0, words)])[0]
        verify_text_segment(out, text, 0.0, 3.0)
        assert [w['word'] for w in out['words']][:2] == [' Hello', ' world']

    def test_cjk_words_leave_text_untouched(self):
        words = _words([('你好', 0.0, 0.5), ('世界', 0.5, 1.0)])
        out = normalize_segments([make_text_segment('你好世界', 0.0, 1.0, words)])[0]
        assert out['text'] == '你好世界'


class TestNormalizeWords:
    def test_sorts_and_prefixes_words(self):
        words = _words([(' world.', 1.0, 2.0), ('Hello', 0.0, 1.0)])
        out = normalize_segments([make_text_segment('Hello world.', 0, 2, words)])[0]
        assert [w['word'] for w in out['words']] == [' Hello', ' world.']

    def test_merges_split_words(self):
        words = _words([(' How', 0.0, 1.0), (' y', 1.0, 1.5), ("'all", 1.5, 2.0), (' doin?', 2.0, 2.2)])
        out = normalize_segments([make_text_segment("How y'all doin?", 0, 2.2, words)])[0]
        assert [(w['word'], w['start'], w['end']) for w in out['words']] == [
            (' How', 0.0, 1.0), (" y'all", 1.0, 2.0), (' doin?', 2.0, 2.2)]
        assert out['text'] == "How y'all doin?"

    def test_whisper_split_url_merges_and_text_is_untouched(self):
        words = _words([(' AT', 0.0, 0.2), ('&T', 0.2, 0.4), (' .com', 0.5, 0.9)])
        out = normalize_segments([make_text_segment('AT&T.com', 0.0, 0.9, words)])[0]
        assert [w['word'] for w in out['words']] == [' AT&T', ' .com']
        assert out['text'] == 'AT&T.com'

    def test_same_start_same_text_words_are_both_kept(self):
        words = _words([(' very,', 600.6, 600.9), (' very', 600.6, 600.9), (' good.', 601.0, 601.5)])
        out = normalize_segments([make_text_segment('very, very good.', 600.6, 601.5, words)])[0]
        assert len(out['words']) == 3

    def test_removes_word_overlap(self):
        words = _words([(' Hello', 0.0, 1.0), (' overlap', 0.2, 0.3)])
        out = normalize_segments([make_text_segment('Hello overlap', 0, 1, words)])[0]
        verify_word_segment(out['words'][0], ' Hello', 0.0, 0.2)
        verify_word_segment(out['words'][1], ' overlap', 0.2, 0.3)


class TestWordSpans:
    def test_prefixed_continuation_words(self):
        words = _words([(' visit', 0, 1), (' AT', 1, 2), ('&T', 2, 3), (' .com', 3, 4)])
        text, spans = word_spans(words)
        assert text == 'visit AT&T .com'
        assert [text[a:b] for a, b in spans] == ['visit', 'AT', '&T', '.com']

    def test_unprefixed_words(self):
        words = _words([('visit', 0, 1), ('acme', 1, 2), ('today', 2, 3)])
        text, spans = word_spans(words)
        assert text == 'visit acme today'
        assert [text[a:b] for a, b in spans] == ['visit', 'acme', 'today']


class TestTimedAdEvidenceEnd:
    def test_continuation_words_keep_offsets_in_sync(self):
        from ad_detector.boundaries import _timed_ad_evidence_end
        words = _words([(' a', 0, 1), (' AT', 1, 2), ('&', 2, 3), ('T', 3, 4),
                        ('.', 4, 5), (' ab', 5, 6)])
        utt = {'words': words, 'terminal': False, 'start': 0, 'end': 6}
        assert _timed_ad_evidence_end(utt) == 6

    def test_plain_unprefixed_words(self):
        from ad_detector.boundaries import _timed_ad_evidence_end
        words = _words([('visit', 0, 1), ('acme.com', 1, 2)])
        utt = {'words': words, 'terminal': False, 'start': 0, 'end': 2}
        assert _timed_ad_evidence_end(utt) == 2

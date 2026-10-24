from utils.segments import (
    TextSegmentLike, WordSegmentLike, essential_text, is_text_essentially_same,
    is_text_segment, is_word_segment, normalize_word_segments, 
    validate_text_segment, validate_text_segments, validate_word_segment,
    normalize_segments, validate_word_segments, _text_leading_diff,
    join_word_segments
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
    def test_join_word_segments_infers_space_prefix_from_first_word(self):
        words = [
            make_word_segment(' How', 0),
            make_word_segment(' y', 1),
            make_word_segment("'all", 2),
            make_word_segment(' doin?', 3),
        ]
        joined = join_word_segments(words)
        assert joined == "How y'all doin?", 'joins words with w/o space'

        # remove leading space to test non-space-prefixed inference
        words[0]['word'] = 'How'
        joined = join_word_segments(words)
        assert joined == "How y 'all doin?", 'all words trimmed and space inserted'

    def test_join_word_segments_does_not_insert_spaces_when_space_prefixed_true(self):
        words = [
            make_word_segment('How', 0),
            make_word_segment('y', 1),
            make_word_segment("'all  ", 2),
            make_word_segment(' doin?', 3),
        ]
        joined = join_word_segments(words, space_prefixed=True)
        assert joined == "Howy'all   doin?", 'joins words without trimming'

    def test_join_word_segments_inserts_spaces_when_space_prefixed_false(self):
        words = [
            make_word_segment('How', 0),
            make_word_segment("y'all ", 1),
            make_word_segment(' doin? ', 2),
        ]
        joined = join_word_segments(words, space_prefixed=False)
        assert joined == "How y'all doin?", 'trims all words, inserts spaces'


class TestIsTextEssentiallySame:
    def test_essentially_same_matches_identical_text(self):
        assert is_text_essentially_same("Hello world", "Hello world")
        assert is_text_essentially_same("", "")

    def test_essentially_same_ignores_whitespace(self):
        assert essential_text("Hello world") == "helloworld"
        assert essential_text(" Hell oworld ") == "helloworld"
        assert is_text_essentially_same("Hello world", " Helloworld ")

    def test_essentially_same_ignores_case(self):
        assert essential_text("Hello world") == "helloworld"
        assert essential_text("hello WORLD") == "helloworld"
        assert is_text_essentially_same("Hello world", "hello WORLD")

    def test_essentially_same_detects_difference(self):
        assert not is_text_essentially_same("Hello world", "Hello there")

    def test_essentially_same_compares_non_words_chars_when_no_word_chars(self):
        assert essential_text(" @@@  !!!! ") == "@ !"
        assert essential_text("@@ !") == "@ !"
        assert is_text_essentially_same(" @@@  !!!! ", "@@ !")
        assert is_text_essentially_same("", "  ")


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
            {'text': 'x', 'start': 1.0, 'end': 2.0, 'words': ['bad']},
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

    def test_validate_text_segments_accepts_valid_input(self):
        segments: list[tuple[str, list[TextSegmentLike]]] = [
            ('empty', []),
            ('no overlap', [
                {'text': 'a', 'start': 0.0, 'end': 1.0},
                {'text': 'b', 'start': 1.0, 'end': 2.0},
                {'text': 'b', 'start': 11.0, 'end': 22.0},
            ]),
            ('duplicate', [
                {'text': 'a', 'start': 0.0, 'end': 1.0},
                {'text': 'a', 'start': 0.0, 'end': 1.0},
            ]),
            ('full overlap', [
                {'text': 'a', 'start': 0.0, 'end': 1.0},
                {'text': 'b', 'start': 0.0, 'end': 1.0},
            ]),
            ('partial overlap, same start', [
                {'text': 'in a', 'start': 1.0, 'end': 8.0},
                {'text': 'a', 'start': 1.0, 'end': 10.0},
            ]),
            ('same start, end out of order', [
                # this is technically out of order because end breaks the 
                # (start, end) sorting, but we allow it anyway
                {'text': 'a', 'start': 0.0, 'end': 2.0},
                {'text': 'b', 'start': 0.0, 'end': 1.0},
            ]),
            ('partial overlap, diff start, same end', [
                {'text': 'a', 'start': 1.0, 'end': 4.0},
                {'text': 'in a', 'start': 2.0, 'end': 4.0},
                {'text': 'in both', 'start': 3.0, 'end': 4.0},
            ]),
            ('partial overlap, diff start, diff end', [
                {'text': 'a', 'start': 1.0, 'end': 5.0},
                {'text': 'b', 'start': 2.0, 'end': 10.0},
                {'text': 'c', 'start': 3.0, 'end': 4.0},
            ]),
            ('mix', [
                # 012345
                # a
                #  b
                #  ccc
                #   d
                #   ee
                #     f
                {'text': 'a', 'start': 0.0, 'end': 1.0},
                {'text': 'b', 'start': 1.0, 'end': 2.0},
                {'text': 'c', 'start': 1.0, 'end': 4.0},
                {'text': 'd', 'start': 2.0, 'end': 3.0},
                {'text': 'e', 'start': 2.0, 'end': 4.0},
                {'text': 'f', 'start': 4.0, 'end': 5.0},
            ]),
        ]

        for description, segment_list in segments:
            try:
                validate_text_segments(segment_list)
            except Exception as e:
                assert not e, f"Expected valid segments for: {description}"

    def test_validate_text_segments_rejects_invalid_input(self):
        segments: list[tuple[str, list[TextSegmentLike]]] = [
            ('out of order, diff start', [
                {'text': 'b', 'start': 1.0, 'end': 2.0},
                {'text': 'a', 'start': 0.0, 'end': 1.0},
            ]),
            ('word issues', [
                {'text': 'x', 'start': 0.0, 'end': 1.0, 'words': [
                    {'word': 'negative-duration', 'start': 1.0, 'end': 0.0},
                ]},
            ]),
        ]

        for description, segment_list in segments:
            try:
                validate_text_segments(segment_list)
                assert False, f"Expected invalid segments for {description}"
            except ValueError:
                pass

    def test_validate_word_segments_allows_invalid_timing_when_not_strict(self):
        segments: list[TextSegmentLike] = [
            {'text': 'negative-duration', 'start': 10.0, 'end': 30.0},
            {'text': 'out-of-order', 'start': 0.0, 'end': 0.0},
            {'text': 'word issues', 'start': 0.0, 'end': 0.0, 'words': [
                {'word': 'negetive-duration', 'start': 1.0, 'end': 0.0},
            ]},
        ]
        validate_text_segments(segments, strict=False)


class TestNormalizeSegment:
    def test_normalize_word_segments_with_empty_list(self):
        words = normalize_word_segments([])
        assert words is not None
        assert len(words) == 0

    def test_normalize_segments_with_empty_list(self):
        segments = normalize_segments([])
        assert segments is not None
        assert len(segments) == 0

    def test_normalizes_segments_sets_text_and_timestamp_from_words(self):
        segments = normalize_segments(
            [
                {
                    'text': 'blahhh', # will be replaced
                    'start': 1.0, # will be replaced
                    'end': 1.0, # will be replaced
                    'words': make_words_from_text('Hello world.', 0.0, 2.0),
                },
            ],
            normalize_from_words=True) # default

        assert segments is not None
        assert len(segments) == 1
        verify_text_segment(segments[0], 'Hello world.', 0.0, 2.0)
        assert (words := segments[0].get('words')) is not None
        assert len(words) == 2
        verify_word_segment(words[0], ' Hello', 0.0, 1.0)
        verify_word_segment(words[1], ' world.', 1.0, 2.0)

    def test_normalize_segments_normalizes_from_words(self):
        words = [
            # out of order, not space-prefixed
            make_word_segment('world.', 1.0),
            make_word_segment('Hello', 0.0),
        ]
        segments = normalize_segments([make_text_segment('', 0, 0, words)])

        assert segments is not None
        assert len(segments) == 1
        verify_text_segment(segments[0], 'Hello world.', 0.0, 2.0)
        assert (words := segments[0].get('words')) is not None
        assert len(words) == 2
        verify_word_segment(words[0], ' Hello', 0.0, 1.0)
        verify_word_segment(words[1], ' world.', 1.0, 2.0)

    def test_normalize_segments_does_not_normalize_from_words_when_disabled(self):
        segments = normalize_segments(
            [
                {
                    'text': 'blahhhh.',
                    'start': -1.0,
                    'end': 4.0,
                    'words': make_words_from_text('Hello world.', 0.0)
                }
            ],
            normalize_from_words=False)

        assert segments is not None
        assert len(segments) == 1
        verify_text_segment(segments[0], 'blahhhh.', -1.0, 4.0)

    def test_normalize_segments_removes_duplicate_words(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                {'word': 'Hello', 'start': 0.0, 'end': 1.0},
                {'word': 'world.', 'start': 1.0, 'end': 2.0},
                # duplicates
                {'word': 'world.', 'start': 1.0, 'end': 2.1}, # end doesn't matter
                {'word': 'Hello', 'start': 0.0, 'end': 0.9}, # end doesn't matter
            ])
        ])

        assert segments is not None
        assert len(segments) == 1
        verify_text_segment(segments[0], 'Hello world.', 0.0, 2.1)
        assert (words := segments[0].get('words')) is not None
        assert len(words) == 2
        verify_word_segment(words[0], ' Hello', 0.0, 1.0)
        verify_word_segment(words[1], ' world.', 1.0, 2.1)

    def test_normalize_segments_removes_word_overlap(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                {'word': ' Hello', 'start': 0.0, 'end': 1.0},
                {'word': ' overlap', 'start': 0.2, 'end': 0.3},
            ])
        ])

        assert segments is not None
        verify_text_segment(segments[0], 'Hello overlap', 0.0, 0.3)
        assert (words := segments[0].get('words')) is not None
        assert len(words) == 2
        verify_word_segment(words[0], ' Hello', 0.0, 0.2)
        verify_word_segment(words[1], ' overlap', 0.2, 0.3)

    def test_normalize_segments_removes_overlap_text_only(self):
        words3 = 'First Second Scenario #3.'.split()
        segments = normalize_segments(
            [
                # Scenario #1: s0 contained in s1. Skip s0.
                #   s0--->......|
                #   s1------>...|
                #   s2--------->|
                make_text_segment('Scenario #1 (first).', 10.0, 11.0),
                make_text_segment('Scenario #1 (second).', 10.0, 12.0),
                make_text_segment('Scenario #1 (third).', 10.0, 13.0),

                # Scenario #2: s1 contained in s0. Skip s1
                #   s0--------->|
                #     s1---->...|
                #       s2----->|
                make_text_segment('Scenario #2 (first).', 20.0, 23.0),
                make_text_segment('Scenario #2 (second).', 21.0, 22.0),
                make_text_segment('Scenario #2 (third).', 21.5, 23.0),

                # Scenario #3: Partial overlap, Take s0-s1 and s1
                #   s0----->|
                #      s1----->|
                #         s2----->|
                make_text_segment(' '.join(words3), 30.0, 33.0),
                make_text_segment(' '.join(words3[1:]), 31.0, 34.0),
                make_text_segment(' '.join(words3[2:]), 32.0, 35.0),
            ]
        )

        assert segments is not None
        # assert len(segments) == 5
        verify_text_segment(segments[0], 'Scenario #1 (third).', 10.0, 13.0)
        verify_text_segment(segments[1], 'Scenario #2 (first).', 20.0, 23.0)
        verify_text_segment(segments[2], words3[0], 30.0, 31.0)
        verify_text_segment(segments[3], words3[1], 31.0, 32.0)
        verify_text_segment(segments[4], ' '.join(words3[2:]), 32.0, 35.0)

    def test_text_leading_diff(self):
        assert _text_leading_diff("abc", "abc") == "", "no difference"
        assert _text_leading_diff("abc", "abc...") == "", "no leading difference"
        assert _text_leading_diff("abc", "def") == "abc", "all different"
        assert _text_leading_diff("abc xyz", "abc") == "abc xyz", "no trailing overlap"
        assert _text_leading_diff("abc xyz", "xyz...") == "abc ", "leading difference"
        assert _text_leading_diff("ABC XYZ", "xyz...") == "ABC ", "ignores case differences"
        assert _text_leading_diff("abc xyz!", "xyz,") == "abc ", "ignores punctuation differences"
        assert _text_leading_diff("abc x y z", "x  y  z...") == "abc ", "ignores whitespace differences"
        assert _text_leading_diff("abc xyz", "x y z") == "abc xyz", "matches on words"
        # edge cases
        assert _text_leading_diff("", "xyz...") == "", "all strings begin with an empty string"
        assert _text_leading_diff("abc", "") == "abc", "all strings end with an empty string"
        assert _text_leading_diff("", "") == "", "cant be anything else"

    def test_normalize_segments_removes_overlap_from_words(self):
        segments = normalize_segments(
            [
                # Scenario #1: s0 contained in s1. Skip s0.
                #   s0--->......|
                #   s1------>...|
                #   s2--------->|
                make_text_segment('', 0, 0, make_words_from_text('Scenario #1 (first).', 10.0, 11.0)),
                make_text_segment('', 0, 0, make_words_from_text('Scenario #1 (second).', 10.0, 12.0)),
                make_text_segment('', 0, 0, make_words_from_text('Scenario #1 (third).', 10.0, 13.0)),

                # Scenario #2: s1 contained in s0. Skip s1
                #   s0--------->|
                #     s1---->...|
                #       s2----->|
                make_text_segment('', 0, 0, make_words_from_text('Scenario #2 (first).', 20.0, 23.0)),
                make_text_segment('', 0, 0, make_words_from_text('Scenario #2 (second).', 21.0, 22.0)),
                make_text_segment('', 0, 0, make_words_from_text('Scenario #2 (third).', 21.5, 23.0)),

                # Scenario #3: Partial overlap, Take s0.words[*].start < s1.start
                #   s0----->|
                #      s1----->|
                #         s2----->|
                make_text_segment('', 0, 0, make_words_from_text('First Scenario #3', 30.0)),
                make_text_segment('', 0, 0, make_words_from_text('Second Scenario #3', 31.0)),
                make_text_segment('', 0, 0, make_words_from_text('Third Scenario #3.', 32.0)),
            ]
        )

        assert segments is not None
        assert len(segments) == 5
        verify_text_segment(segments[0], 'Scenario #1 (third).', 10.0, 13.0)
        verify_text_segment(segments[1], 'Scenario #2 (first).', 20.0, 23.0)
        verify_text_segment(segments[2], 'First', 30.0, 31.0)
        verify_text_segment(segments[3], 'Second', 31.0, 32.0)
        verify_text_segment(segments[4], 'Third Scenario #3.', 32.0, 35.0)

    def test_normalize_segments_normalizes_text_and_word_order(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                make_word_segment(' segment.', 2.0, 3.0),
                make_word_segment(' Second', 1.0, 2.0),
            ]),
            make_text_segment('First.', 0.0, 1.0),
        ])

        assert len(segments) == 2
        verify_text_segment(segments[0], 'First.', 0.0, 1.0)
        verify_text_segment(segments[1], 'Second segment.', 1.0, 3.0)
        assert segments[0].get('words') is None
        assert (words := segments[1].get('words')) is not None
        verify_word_segment(words[0], ' Second', 1.0, 2.0)
        verify_word_segment(words[1], ' segment.', 2.0, 3.0)

    def test_normalize_segments_creates_space_prefixed_words(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                make_word_segment('Hello', 0.0),
                make_word_segment('-world.', 1.0),
            ])
        ])

        assert segments is not None
        assert len(segments) == 1
        assert (segment := segments[0]) is not None
        verify_text_segment(segment, 'Hello -world.', 0.0, 2.0)
        assert (words := segment.get('words')) is not None
        assert len(words) == 2
        verify_word_segment(words[0], ' Hello', 0.0, 1.0)
        verify_word_segment(words[1], ' -world.', 1.0, 2.0)

    def test_normalize_segments_merges_split_words(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                make_word_segment(' How', 0.0, 1.0),
                make_word_segment(' y', 1.0, 1.5),
                make_word_segment("'all", 1.5, 2.0),
                make_word_segment(' doin?', 2.0, 2.2),
            ])
        ])

        assert segments is not None
        assert len(segments) == 1
        assert (segment := segments[0]) is not None
        verify_text_segment(segment, "How y'all doin?", 0.0, 2.2)
        assert (words := segment.get('words')) is not None
        assert len(words) == 3
        verify_word_segment(words[0], ' How', 0.0, 1.0)
        verify_word_segment(words[1], " y'all", 1.0, 2.0)
        verify_word_segment(words[2], " doin?", 2.0, 2.2)

    def test_normalize_segments_trims_text_when_no_words(self):
        segments = normalize_segments([
            make_text_segment('   Hello foo.  ', 1.0, 4.0)
        ])

        assert segments is not None
        assert len(segments) == 1
        verify_text_segment(segments[0], 'Hello foo.', 1.0, 4.0)
        assert segments[0].get('words') is None

    def test_normalize_segments_preserves_extra_fields(self):
        segments = normalize_segments([
            make_text_segment('', 0, 0, [
                make_word_segment(' How', 0.0, 1.0)
                | {'foo': 'baz'} # type: ignore extra field
            ])
            | {'foo': 'bar'} # type: ignore extra field
        ])

        verify_text_segment(segments[0],
                            'How',
                            foo='bar',
                            words=[{'word': ' How',
                                    'foo': 'baz'}]
                                    )

    def test_normalize_segments_does_not_throw_when_invalid_segment_arg_is_provided(self):
        segments = [
            make_text_segment('good', 0, 0),
            make_text_segment('bad start', float('nan'), 0),
        ]
        invalid = []
        valid = normalize_segments(segments, invalid_segments=invalid)
        assert len(valid) == 1
        assert len(invalid) == 1
        assert valid[0] is segments[0]
        assert invalid[0] is segments[1]

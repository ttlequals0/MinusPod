"""OpenAI verbose_json returns word timestamps in a top-level array, not
nested per segment (#755). They must be folded into their segments."""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('transcriber_top_words_')
from transcriber import _attach_top_level_words  # noqa: E402


def _segs():
    return [{'start': 0.0, 'end': 1.6, 'text': 'well its about time', 'words': []},
            {'start': 1.6, 'end': 3.0, 'text': 'you got here', 'words': []}]


TOP_WORDS = [
    {'word': 'Well', 'start': 0.0, 'end': 0.48},
    {'word': 'about', 'start': 0.92, 'end': 1.18},
    {'word': 'time', 'start': 1.18, 'end': 1.56},
    {'word': 'here', 'start': 2.16, 'end': 2.42},
]


def test_top_level_words_land_in_the_segment_covering_their_midpoint():
    segs = _segs()
    _attach_top_level_words(segs, TOP_WORDS)

    assert [w['word'] for w in segs[0]['words']] == ['Well', 'about', 'time']
    assert [w['word'] for w in segs[1]['words']] == ['here']


def test_words_are_sorted_within_a_segment_even_if_the_array_is_not():
    segs = _segs()
    _attach_top_level_words(segs, list(reversed(TOP_WORDS)))

    assert [w['start'] for w in segs[0]['words']] == [0.0, 0.92, 1.18]


def test_nested_words_are_left_alone():
    segs = [{'start': 0.0, 'end': 2.0, 'text': 'hi',
             'words': [{'word': 'hi', 'start': 0.0, 'end': 0.5}]}]
    _attach_top_level_words(segs, TOP_WORDS)

    assert [w['word'] for w in segs[0]['words']] == ['hi']


def test_a_word_in_no_segment_goes_to_the_one_nearest_its_span():
    segs = _segs()
    _attach_top_level_words(segs, [{'word': 'trailing', 'start': 9.0, 'end': 9.3}])

    assert segs[1]['words'][0]['word'] == 'trailing'


def test_no_top_level_words_is_a_no_op():
    segs = _segs()
    _attach_top_level_words(segs, [])

    assert segs[0]['words'] == [] and segs[1]['words'] == []


def test_a_word_missing_a_timestamp_is_skipped():
    segs = _segs()
    _attach_top_level_words(segs, [{'word': 'nostamp'}])

    assert segs[0]['words'] == [] and segs[1]['words'] == []

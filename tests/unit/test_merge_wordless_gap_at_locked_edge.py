"""A reviewer-locked edge keeps a gap with speech in it, not a short wordless gap before the next ad."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, ValidationResult


def _seg(start, end, text='words'):
    return {'start': start, 'end': end, 'text': text,
            'words': [{'start': start, 'end': (start + end) / 2},
                      {'start': (start + end) / 2 + 0.05, 'end': end}]}


SEGMENTS = [_seg(412.06, 416.3, 'the obvious choice for contracts'),
            _seg(418.19, 421.94, 'the sound of an ordinary day')]


def _merge(segments, left=None, right=None):
    validator = AdValidator(episode_duration=1140.5, segments=segments)
    left = left or {'start': 394.19, 'end': 416.3, 'reviewer_locked_start': 396.4,
                    'reviewer_locked_end': 416.3}
    right = right or {'start': 418.192, 'end': 448.11}
    merged = validator._merge_close_ads([left, right], ValidationResult(ads=[]))
    return [(m['start'], m['end']) for m in merged]


def test_wordless_gap_after_a_locked_end_merges():
    assert _merge(SEGMENTS) == [(394.19, 448.11)]


def test_wordless_gap_before_a_locked_start_merges():
    left = {'start': 394.19, 'end': 416.3}
    right = {'start': 418.192, 'end': 448.11, 'reviewer_locked_start': 418.192}

    assert _merge(SEGMENTS, left=left, right=right) == [(394.19, 448.11)]


def test_a_word_in_the_gap_keeps_it():
    segments = [*SEGMENTS[:1], _seg(417.0, 417.6, 'and'), *SEGMENTS[1:]]

    assert _merge(segments) == [(394.19, 416.3), (418.192, 448.11)]


def test_a_long_wordless_gap_at_a_locked_edge_stays():
    right = {'start': 422.0, 'end': 448.11}
    segments = [SEGMENTS[0], _seg(422.0, 425.0)]

    assert _merge(segments, right=right) == [(394.19, 416.3), (422.0, 448.11)]


def test_no_transcript_keeps_the_gap():
    assert _merge([]) == [(394.19, 416.3), (418.192, 448.11)]


def test_a_segment_without_word_timings_running_into_the_gap_keeps_it():
    untimed = {'start': 412.06, 'end': 417.5, 'text': 'the obvious choice for contracts'}

    assert _merge([untimed, SEGMENTS[1]]) == [(394.19, 416.3), (418.192, 448.11)]


def test_a_word_ending_within_slop_of_the_gap_start_does_not_block():
    tail = {'start': 412.06, 'end': 416.5, 'text': 'contracts',
            'words': [{'start': 412.06, 'end': 416.5}]}

    assert _merge([tail, SEGMENTS[1]]) == [(394.19, 448.11)]

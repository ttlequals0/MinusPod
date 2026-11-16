"""snip_short_asks: opt-in removal of short host asks and sponsor thanks."""
import pytest

from ad_detector.short_ask_clamp import clamp_short_ask
from utils.short_asks import (
    SHORT_ASK_EDGE_CAP_S, SHORT_ASK_MAX_S, SHORT_ASK_MIN_S,
    floor_for, is_short_ask, stamp_short_ask,
)


def _ad(start=100.0, end=106.0, category='interaction', **extra):
    ad = {'start': start, 'end': end, 'category': category, 'short_ask': True,
          'short_ask_origin': [start, end], 'confidence': 0.9}
    ad.update(extra)
    return ad


def _segs():
    return [
        {'start': 90.0, 'end': 99.0, 'text': 'That was the main story.'},
        {'start': 99.0, 'end': 102.5, 'text': 'If you enjoyed the show'},
        {'start': 102.5, 'end': 106.0, 'text': 'please subscribe.'},
        {'start': 106.0, 'end': 115.0, 'text': 'Now back to the episode.'},
    ]


class TestConstants:
    def test_floor_is_two_seconds(self):
        assert SHORT_ASK_MIN_S == 2.0
        assert SHORT_ASK_MAX_S == 40.0
        assert SHORT_ASK_EDGE_CAP_S == 1.5


class TestStamp:
    def test_keeps_flag_and_stamps_origin_when_on(self):
        ad = stamp_short_ask({'start': 10.0, 'end': 16.0, 'category': 'interaction',
                              'short_ask': True}, True)
        assert ad['short_ask'] is True
        assert ad['short_ask_origin'] == [10.0, 16.0]

    @pytest.mark.parametrize('enabled,category,end', [
        (False, 'interaction', 16.0),
        (True, 'cross_promo', 16.0),
        (True, 'intro', 16.0),
        (True, 'interaction', 60.0),
        (True, 'interaction', 11.0),
    ])
    def test_drops_flag_when_off_wrong_category_or_wrong_length(self, enabled, category, end):
        ad = stamp_short_ask({'start': 10.0, 'end': end, 'category': category,
                              'short_ask': True}, enabled)
        assert 'short_ask' not in ad and 'short_ask_origin' not in ad

    def test_unflagged_marker_untouched(self):
        ad = stamp_short_ask({'start': 10.0, 'end': 16.0, 'category': 'sponsor'}, True)
        assert ad == {'start': 10.0, 'end': 16.0, 'category': 'sponsor'}


class TestFloor:
    @pytest.mark.parametrize('category', ['interaction', 'self_promo', 'sponsor'])
    def test_flagged_gets_short_floor(self, category):
        assert floor_for(_ad(category=category), 10.0) == SHORT_ASK_MIN_S
        assert floor_for(_ad(category=category), 7.0) == SHORT_ASK_MIN_S

    def test_unflagged_keeps_default(self):
        ad = _ad()
        ad.pop('short_ask')
        assert floor_for(ad, 10.0) == 10.0

    def test_flag_without_origin_keeps_default(self):
        ad = _ad()
        ad.pop('short_ask_origin')
        assert floor_for(ad, 10.0) == 10.0

    def test_flagged_over_forty_seconds_keeps_default(self):
        assert floor_for(_ad(end=145.0), 10.0) == 10.0

    def test_flagged_under_floor_keeps_default(self):
        assert floor_for(_ad(end=101.0), 10.0) == 10.0

    def test_flagged_outside_category_set_keeps_default(self):
        assert floor_for(_ad(category='cross_promo'), 10.0) == 10.0

    def test_edge_moved_past_cap_loses_the_floor(self):
        assert not is_short_ask(_ad(end=108.0, short_ask_origin=[100.0, 106.0]))
        assert is_short_ask(_ad(end=107.4, short_ask_origin=[100.0, 106.0]))


class TestClamp:
    def test_snaps_to_sentence_edges_within_cap(self):
        ad = clamp_short_ask(_ad(start=99.4, end=105.2, short_ask_origin=[99.4, 105.2]), _segs())
        assert ad['short_ask'] is True
        assert (ad['start'], ad['end']) == (99.0, 106.0)

    def test_start_mid_sentence_drops_flag(self):
        out = clamp_short_ask(_ad(start=102.5, end=106.0), _segs())
        assert 'short_ask' not in out

    def test_growth_of_four_seconds_is_clamped_to_cap(self):
        segs = [
            {'start': 90.0, 'end': 99.0, 'text': 'Earlier talk.'},
            {'start': 99.0, 'end': 100.0, 'text': 'Please rate the show.'},
            {'start': 100.0, 'end': 106.0, 'text': 'Thanks for listening.'},
            {'start': 106.0, 'end': 110.0, 'text': 'Then more story.'},
        ]
        ad = _ad(start=100.0, end=106.0, short_ask_origin=[100.0, 106.0])
        ad['end'] = 110.0
        out = clamp_short_ask(ad, segs)
        assert out['short_ask'] is True and out['end'] == 106.0

    def test_no_sentence_edge_in_range_drops_flag(self):
        segs = [
            {'start': 99.0, 'end': 103.0, 'text': 'if you like the show and'},
            {'start': 103.0, 'end': 108.0, 'text': 'would like to subscribe then'},
        ]
        out = clamp_short_ask(_ad(), segs)
        assert 'short_ask' not in out and 'short_ask_origin' not in out
        assert (out['start'], out['end']) == (100.0, 106.0)

    def test_start_must_open_a_sentence(self):
        segs = [
            {'start': 95.0, 'end': 100.0, 'text': 'and the host went on and'},
            {'start': 100.0, 'end': 106.0, 'text': 'Please subscribe.'},
        ]
        out = clamp_short_ask(_ad(), segs)
        assert 'short_ask' not in out

    def test_closing_quote_counts_as_sentence_end(self):
        segs = [
            {'start': 90.0, 'end': 100.0, 'text': 'Story ends here.'},
            {'start': 100.0, 'end': 106.0, 'text': 'He said "please subscribe."'},
        ]
        out = clamp_short_ask(_ad(), segs)
        assert out['short_ask'] is True and out['end'] == 106.0

    def test_word_timed_segment_offers_inner_sentence_edges(self):
        words = [('Earlier', 99.0, 99.6), ('bit.', 99.6, 100.0), ('Please', 100.0, 100.8),
                 ('subscribe', 100.8, 101.6), ('now.', 101.6, 102.4)]
        segs = [{'start': 99.0, 'end': 102.4, 'text': 'Earlier bit. Please subscribe now.',
                 'words': [{'word': w, 'start': a, 'end': b} for w, a, b in words]}]
        ad = _ad(start=100.0, end=102.4, short_ask_origin=[100.0, 102.4])
        out = clamp_short_ask(ad, segs)
        assert out['short_ask'] is True
        assert (out['start'], out['end']) == (100.0, 102.4)

    def test_unflagged_marker_untouched(self):
        ad = {'start': 1.0, 'end': 2.0}
        assert clamp_short_ask(ad, _segs()) == {'start': 1.0, 'end': 2.0}


class TestUnderThreeSecondBand:
    @staticmethod
    def _segs(end):
        return [
            {'start': 90.0, 'end': 99.0, 'text': 'That was the story.'},
            {'start': 99.0, 'end': end, 'text': 'Listen wherever you get your podcasts.'},
            {'start': end, 'end': end + 8.0, 'text': 'Now back to it.'},
        ]

    def test_cue_aligned_2_4s_marker_keeps_flag(self):
        ad = _ad(start=99.0, end=101.4)
        out = clamp_short_ask(ad, self._segs(101.4))
        assert out['short_ask'] is True and (out['start'], out['end']) == (99.0, 101.4)
        assert is_short_ask(out) and floor_for(out, 10.0) == SHORT_ASK_MIN_S

    def test_2_5s_marker_aligned_within_half_second_keeps_flag(self):
        out = clamp_short_ask(_ad(start=99.2, end=101.5), self._segs(101.5))
        assert out['short_ask'] is True and (out['start'], out['end']) == (99.0, 101.5)

    def test_misaligned_end_drops_flag_for_a_short_marker(self):
        out = clamp_short_ask(_ad(start=99.0, end=100.5), self._segs(101.4))
        assert 'short_ask' not in out and 'short_ask_origin' not in out
        assert (out['start'], out['end']) == (99.0, 100.5)

    def test_misaligned_start_drops_flag_for_a_short_marker(self):
        out = clamp_short_ask(_ad(start=99.9, end=101.4), self._segs(101.4))
        assert 'short_ask' not in out

    def test_1_9s_marker_is_below_the_floor(self):
        out = clamp_short_ask(_ad(start=99.0, end=100.9), self._segs(100.9))
        assert 'short_ask' not in out

    def test_three_second_marker_keeps_the_wide_edge_cap(self):
        out = clamp_short_ask(_ad(start=99.0, end=101.4), [
            {'start': 90.0, 'end': 99.0, 'text': 'Story.'},
            {'start': 99.0, 'end': 102.0, 'text': 'Please subscribe.'},
            {'start': 102.0, 'end': 110.0, 'text': 'More.'},
        ])
        assert out['short_ask'] is True and out['end'] == 102.0


class TestReviewerTrimIsKept:
    SEGS = [
        {'start': 90.0, 'end': 100.0, 'text': 'Editorial talk about the topic.'},
        {'start': 100.0, 'end': 103.0, 'text': 'And that company is interesting.'},
        {'start': 103.0, 'end': 106.0, 'text': 'Please subscribe and rate us.'},
        {'start': 106.0, 'end': 120.0, 'text': 'Back to the interview now.'},
    ]

    def test_start_trimmed_past_the_cap_keeps_the_trim_and_drops_the_flag(self):
        ad = _ad(start=103.0, end=106.0, short_ask_origin=[100.0, 106.0])
        out = clamp_short_ask(ad, self.SEGS)
        assert 'short_ask' not in out and (out['start'], out['end']) == (103.0, 106.0)

    @pytest.mark.parametrize('start', [102.0, 101.0])
    def test_start_trimmed_mid_sentence_never_moves_back_out(self, start):
        ad = _ad(start=start, end=106.0, short_ask_origin=[100.0, 106.0])
        out = clamp_short_ask(ad, self.SEGS)
        assert out.get('short_ask') is None or out['start'] >= start

    def test_end_trimmed_inward_never_moves_back_out(self):
        segs = [
            {'start': 100.0, 'end': 103.0, 'text': 'Please subscribe and rate us.'},
            {'start': 103.0, 'end': 106.0, 'text': 'And that company is interesting.'},
            {'start': 106.0, 'end': 120.0, 'text': 'Back to the interview now.'},
        ]
        ad = _ad(start=100.0, end=103.0, short_ask_origin=[100.0, 106.0])
        out = clamp_short_ask(ad, segs)
        assert out['end'] <= 103.0

"""Content-based ad boundary extension (extend_ad_boundaries_by_content).

Covers the tail-completion behaviors: the sandwich rule (connector segments
between sponsor mentions don't stop the end walk), phone-number CTA patterns,
and the end-only mode used by the post-reviewer pass.
"""

import pytest

from ad_detector.boundaries import (
    extend_ad_boundaries_by_content,
    _text_has_ad_content,
)


def _seg(start, end, text):
    tokens = text.split()
    step = (end - start) / len(tokens)
    return {
        'start': start, 'end': end, 'text': text,
        'words': [
            {'word': token, 'start': start + index * step,
             'end': end if index == len(tokens) - 1
             else start + (index + 1) * step}
            for index, token in enumerate(tokens)
        ],
    }


def _timed_seg(words):
    return {
        'start': words[0][1],
        'end': words[-1][2],
        'text': ' '.join(word for word, _, _ in words),
        'words': [
            {'word': word, 'start': start, 'end': end}
            for word, start, end in words
        ],
    }


def test_reviewed_trim_does_not_recover_prior_cta_from_mixed_segment():
    segment = _timed_seg([
        ('Visit', 90.0, 90.4), ('acme.com', 90.4, 91.2),
        ('slash', 91.2, 91.5), ('offer.', 91.5, 92.0),
        ('We', 92.1, 92.3), ('thank', 92.3, 92.6),
        ('them', 92.6, 92.8), ('for', 92.8, 93.0),
        ('their', 93.0, 93.2), ('support.', 93.2, 93.8),
        ('Now', 94.1, 94.4), ('the', 94.4, 94.6),
        ('discussion', 94.6, 95.2), ('continues.', 95.2, 96.0),
        ('The', 96.1, 96.3), ('next', 96.3, 96.5),
        ('topic', 96.5, 97.0), ('is', 97.0, 97.2),
        ('security.', 97.2, 98.0),
    ])
    ad = {'start': 80.0, 'end': 94.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment], extend_start=False)

    assert result[0]['end'] == 94.0
    assert 'end_extended_by_content' not in result[0]


def test_reviewed_trim_keeps_terminal_sentence_tail_without_new_ad_words():
    segment = _timed_seg([
        ('Visit', 99.0, 99.2), ('acme.com', 99.2, 99.8),
        ('and', 100.1, 100.3), ('the', 100.3, 100.5),
        ('story', 100.5, 100.9), ('continues.', 100.9, 101.5),
    ])
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment],
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_outro_with_inline_return_does_not_swallow_show_words():
    segments = [
        _seg(100.0, 102.0, 'Visit acme.com.'),
        _seg(102.0, 107.0,
             'We thank them for their support and now back to the story.'),
    ]
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 102.0


def test_mixed_segment_finishes_url_and_thanks_before_show_returns():
    segment = _timed_seg([
        ('Use', 100.0, 100.2), ('code', 100.2, 100.4),
        ('SAVE', 100.4, 100.8), ('at', 100.8, 101.0),
        ('acme', 101.0, 101.3), ('dot', 101.3, 101.5),
        ('com', 101.5, 101.8), ('slash', 101.8, 102.1),
        ('podcast.', 102.1, 102.8), ('We', 102.9, 103.1),
        ('thank', 103.1, 103.4), ('them', 103.4, 103.6),
        ('for', 103.6, 103.8), ('their', 103.8, 104.0),
        ('support.', 104.0, 104.6), ('Now', 105.0, 105.3),
        ('we', 105.3, 105.5), ('return', 105.5, 105.9),
        ('to', 105.9, 106.1), ('the', 106.1, 106.3),
        ('story.', 106.3, 107.0),
    ])
    ad = {'start': 80.0, 'end': 100.7, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment], extend_start=False)

    assert result[0]['end'] == 104.6


def test_straddling_segment_without_complete_timestamps_stays_put():
    segment = {'start': 90.0, 'end': 120.0,
               'text': 'Visit acme dot com. Back to the story.'}
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    assert extend_ad_boundaries_by_content([ad], [segment])[0]['end'] == 100.0
    segment['words'] = [
        {'word': 'Visit', 'start': 90.0, 'end': 90.4},
        {'word': 'acme', 'start': 90.4, 'end': 90.8},
        {'word': 'story.', 'start': None, 'end': 120.0},
    ]
    assert extend_ad_boundaries_by_content([ad], [segment])[0]['end'] == 100.0


def test_start_extension_stops_at_ad_words_inside_mixed_segment():
    segment = _timed_seg([
        ('Host', 90.0, 90.3), ('discussion', 90.3, 91.0),
        ('ends.', 91.0, 91.4), ('Visit', 91.8, 92.2),
        ('acme.com', 92.2, 93.0), ('for', 93.0, 93.2),
        ('the', 93.2, 93.4), ('offer.', 93.4, 94.0),
    ])
    ad = {'start': 94.0, 'end': 110.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment])

    assert result[0]['start'] == 91.8


def test_unknown_speech_does_not_bridge_to_later_matching_url():
    segments = [
        _timed_seg([
            ('Introducing', 90.0, 90.6), ('Acme,', 90.6, 91.0),
            ('the', 91.0, 91.2), ('platform', 91.2, 91.7),
            ('that', 91.7, 92.0), ('makes', 92.0, 92.3),
            ('your', 92.3, 92.6), ('work', 92.6, 92.9),
            ('easier.', 92.9, 101.0),
        ]),
        _timed_seg([
            ('from', 101.2, 101.5), ('planning', 101.5, 102.0),
            ('to', 102.0, 102.2), ('shipping.', 102.2, 103.0),
            ('Acme', 103.2, 103.6), ('saves', 103.6, 104.0),
            ('time.', 104.0, 105.0), ('Try', 105.2, 105.5),
            ('Acme', 105.5, 105.9), ('at', 105.9, 106.1),
            ('acme.com', 106.1, 106.8), ('slash', 106.8, 107.1),
            ('offer.', 107.1, 108.0),
        ]),
    ]
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Older bundled sponsor'}

    result = extend_ad_boundaries_by_content([ad], segments, extend_start=False)

    assert result[0]['end'] == 100.0


def test_unrelated_url_after_unknown_show_speech_does_not_bridge():
    segments = [
        _timed_seg([
            ('Acme', 90.0, 90.5), ('helps', 90.5, 91.0),
            ('teams.', 91.0, 92.0), ('The', 100.2, 100.5),
            ('weather', 100.5, 101.1), ('changed.', 101.1, 102.0),
            ('Beta', 102.5, 103.0), ('posted', 103.0, 103.5),
            ('at', 103.5, 103.7), ('beta.com.', 103.7, 104.5),
        ]),
    ]
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], segments, extend_start=False)

    assert result[0]['end'] == 100.0


def test_generic_visit_in_show_speech_is_not_ad_evidence():
    segments = [
        _timed_seg([
            ('Acme', 90.0, 90.5), ('sponsor', 90.5, 91.0),
            ('read.', 91.0, 92.0),
        ]),
        _timed_seg([
            ('Visit', 100.0, 100.4), ('the', 100.4, 100.6),
            ('city', 100.6, 100.9), ('museum', 100.9, 101.4),
            ('this', 101.4, 101.7), ('weekend', 101.7, 102.2),
            ('and', 102.2, 102.5), ('see', 102.5, 102.8),
            ('the', 102.8, 103.0), ('new', 103.0, 103.3),
            ('exhibit.', 103.3, 104.4),
        ]),
    ]
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], segments, extend_start=False)

    assert result[0]['end'] == 100.0


@pytest.mark.parametrize('speech', [
    'Acme is the name of this street in the old town.',
    'Beta posted the report at beta.com.',
    'Use code examples when teaching software design.',
    'Visit acme.com, and now let us get back to the story.',
])
def test_ordinary_show_mention_does_not_complete_ad(speech):
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}
    segments = [_seg(80.0, 99.0, 'Acme sponsor read.'),
                _seg(100.0, 104.4, speech)]

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_next_mixed_segment_without_words_does_not_extend():
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}
    segments = [_seg(80.0, 99.0, 'Acme sponsor read.'),
                {'start': 100.0, 'end': 120.0,
                 'text': 'Visit acme.com. Now the story continues.'}]

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_partial_word_list_does_not_authorize_segment_end():
    segment = _seg(100.0, 110.0,
                   'Visit acme.com. Now the show resumes.')
    segment['words'] = segment['words'][:2]
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment],
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_word_crossing_reviewed_end_can_finish_complete_url():
    segment = _timed_seg([
        ('Visit', 99.6, 99.9), ('acme.com.', 99.9, 100.1),
    ])
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], [segment],
                                             extend_start=False)

    assert result[0]['end'] == 100.1


def test_start_walk_stops_at_intervening_show_utterance():
    segments = [
        _seg(85.0, 90.0, 'Visit acme.com for savings.'),
        _seg(90.0, 95.0, 'The local weather changed.'),
        _seg(95.0, 100.0, 'Visit acme.com for savings.'),
        _seg(100.0, 110.0, 'Acme sponsor read.'),
    ]
    ad = {'start': 100.0, 'end': 110.0, 'reason': 'Acme sponsor'}

    result = extend_ad_boundaries_by_content([ad], segments)

    assert result[0]['start'] == 95.0


def test_multiword_path_with_inline_return_stays_for_review():
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}
    segments = [_seg(80.0, 99.0, 'Acme sponsor read.'),
                _seg(100.0, 105.0,
                     'Visit acme dot com slash spring savings now back to the show')]

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_complete_multiword_path_without_return_is_kept():
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}
    segments = [_seg(80.0, 99.0, 'Acme sponsor read.'),
                _seg(100.0, 105.0,
                     'Visit acme dot com slash spring savings.')]

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 105.0


def test_ambiguous_unpunctuated_offer_stays_for_review():
    ad = {'start': 80.0, 'end': 100.0, 'reason': 'Acme sponsor'}
    segments = [_seg(80.0, 99.0, 'Acme sponsor read.'),
                _seg(100.0, 105.0,
                     'Get twenty percent off your first order today')]

    result = extend_ad_boundaries_by_content([ad], segments,
                                             extend_start=False)

    assert result[0]['end'] == 100.0


def test_end_walk_survives_non_ad_connector_segment():
    # GuardSquare-shaped tail: CTA, connector line, then another sponsor mention.
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 126.0, 'go to guardsquare dot com slash securitynow'),
        _seg(126.0, 129.0, 'thank you for the job you do'),
        _seg(129.0, 135.0, 'and we thank them, guardsquare dot com'),
        _seg(135.0, 140.0, 'all right back to the news of the week'),
    ]
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 135.0
    assert extended[0]['end_extended_by_content'] is True


def test_end_walk_stops_after_consecutive_non_ad_segments():
    # Three plain-content segments in a row end the walk (connector skip is
    # capped at 2), even if a qualifying segment follows inside the window.
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 123.0, 'go to example dot com'),
        _seg(123.0, 125.0, 'so as i was saying'),
        _seg(125.0, 127.0, 'the weather was great'),
        _seg(127.0, 129.0, 'and we went hiking'),
        _seg(129.0, 134.0, 'visit example dot com'),
    ]
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 123.0


def test_end_walk_ignores_segments_beyond_max_window():
    # Qualifying segment starts past BOUNDARY_EXTENSION_MAX (30s) -- not swept.
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 126.0, 'go to example dot com'),
        _seg(151.0, 160.0, 'visit example dot com'),
    ]
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 126.0


def test_phone_pattern_extends_end():
    segments = [
        _seg(95.0, 200.0, 'sponsor copy ends here'),
        _seg(200.5, 207.0, 'call 1-800-grainger for the ones who get it done.'),
        _seg(207.0, 212.0, 'welcome back everybody'),
    ]
    ads = [{'start': 150.0, 'end': 200.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 207.0


def test_extend_start_false_skips_start_side():
    segments = [
        _seg(42.0, 49.5, 'visit example dot com'),
        _seg(50.0, 70.0, 'sponsor copy'),
    ]
    ads = [{'start': 50.0, 'end': 70.0}]

    end_only = extend_ad_boundaries_by_content(ads, segments, extend_start=False)
    assert end_only[0]['start'] == 50.0
    assert 'start_extended_by_content' not in end_only[0]

    both = extend_ad_boundaries_by_content(ads, segments)
    assert both[0]['start'] == 42.0
    assert both[0]['start_extended_by_content'] is True


def test_end_walk_includes_straddling_segment():
    # Ad end falls mid-segment; the straddling CTA segment still qualifies
    # and the walk extends to its end.
    segments = [
        _seg(95.0, 118.0, 'sponsor copy ends here'),
        _timed_seg([
            ('go', 118.0, 118.5), ('to', 118.5, 119.0),
            ('example', 119.0, 120.0), ('dot', 120.0, 121.0),
            ('com', 121.0, 122.0), ('slash', 122.0, 123.0),
            ('podcast', 123.0, 125.0),
        ]),
        _seg(125.0, 130.0, 'back to the show'),
    ]
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 125.0


def test_end_walk_time_cap_stops_long_non_ad_run():
    # Two long story segments (9s past the ad end) exceed
    # BOUNDARY_EXTENSION_SKIP_MAX (8s) even though the skip count (2) is
    # within the connector allowance.
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 123.0, 'go to example dot com'),
        _seg(123.0, 128.0, 'so we drove out to the lake that weekend'),
        _seg(128.0, 132.0, 'and the fishing was actually great'),
        _seg(132.0, 137.0, 'visit example dot com'),
    ]
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 123.0


def test_text_has_ad_content_phone_patterns():
    assert _text_has_ad_content('call 1-800-grainger today')
    assert _text_has_ad_content('call one eight hundred grainger')
    assert not _text_has_ad_content('thank you for the job you do')


def test_cue_snapped_end_is_not_extended():
    # A template-cue-anchored end must not be moved by the content walk.
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 126.0, 'go to example dot com slash offer'),
    ]
    ads = [{'start': 100.0, 'end': 120.0,
            'cue_snap': {'end': {'cue_start': 120.1, 'template_id': 1}}}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 120.0
    assert 'end_extended_by_content' not in extended[0]


def test_cue_snapped_start_is_not_extended():
    segments = [
        _seg(90.0, 99.0, 'visit example dot com for a discount'),
        _seg(100.0, 120.0, 'sponsor copy for example dot com'),
    ]
    ads = [{'start': 100.0, 'end': 120.0,
            'cue_snap': {'start': {'cue_end': 99.9, 'template_id': 1}}}]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['start'] == 100.0
    assert 'start_extended_by_content' not in extended[0]


def test_start_walk_stops_at_neighbouring_detection():
    # The walk must not swallow an adjacent detection's span; the neighbour
    # is judged on its own and may resolve to keep (self_promo).
    segments = [
        _seg(70.0, 99.0, 'support us at patreon dot com slash show'),
        _seg(100.0, 120.0, 'sponsor read mentioning patreon dot com'),
    ]
    ads = [
        {'start': 70.0, 'end': 100.0, 'category': 'self_promo'},
        {'start': 100.0, 'end': 120.0},
    ]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[1]['start'] == 100.0
    assert 'start_extended_by_content' not in extended[1]


def test_end_walk_stops_at_neighbouring_detection():
    segments = [
        _seg(95.0, 120.0, 'sponsor copy ends here'),
        _seg(120.0, 126.0, 'go to example dot com slash offer'),
        _seg(126.0, 140.0, 'more example dot com copy'),
    ]
    ads = [
        {'start': 100.0, 'end': 120.0},
        {'start': 126.0, 'end': 140.0},
    ]

    extended = extend_ad_boundaries_by_content(ads, segments)

    assert extended[0]['end'] == 126.0


def test_refine_skips_cue_snapped_end():
    # Phrase refinement must not move an edge the cue snap anchored.
    from ad_detector.boundaries import refine_ad_boundaries

    def _words(start, text):
        words = []
        t = start
        for w in text.split():
            words.append({'word': w, 'start': t, 'end': t + 0.4})
            t += 0.5
        return words

    segments = [
        {'start': 100.0, 'end': 119.5, 'text': 'sponsor copy here',
         'words': _words(100.0, 'sponsor copy here')},
        {'start': 120.5, 'end': 125.0, 'text': 'all right back to the show',
         'words': _words(120.5, 'all right back to the show')},
    ]
    pinned = [{'start': 100.0, 'end': 120.0,
               'cue_snap': {'end': {'cue_start': 120.1, 'template_id': 1}}}]
    free = [{'start': 100.0, 'end': 120.0}]

    assert refine_ad_boundaries(pinned, segments)[0]['end'] == 120.0
    assert refine_ad_boundaries(free, segments)[0]['end'] > 120.0


def test_barriers_outside_the_ads_list_still_block_extension():
    # Keep-partitioned markers leave the ads list before refinement but must
    # still stop the walk: a cut may not grow into a span the feed keeps.
    segments = [
        _seg(70.0, 99.0, 'support us at patreon dot com slash show'),
        _seg(100.0, 120.0, 'sponsor read mentioning patreon dot com'),
    ]
    kept = {'start': 70.0, 'end': 100.0, 'category': 'self_promo',
            'action_applied': 'keep'}
    ads = [{'start': 100.0, 'end': 120.0}]

    extended = extend_ad_boundaries_by_content(ads, segments,
                                               barriers=ads + [kept])

    assert extended[0]['start'] == 100.0
    assert 'start_extended_by_content' not in extended[0]


class TestTightenPatternRegions:
    """An oversized pattern span adopts the LLM bounds inside it (the
    ZipRecruiter case: a 100s pattern marker over a 38s read got held for
    no_splice_evidence while the 0.98-confidence LLM detection was dropped)."""

    def _fixture(self, claude, region_end=601.1):
        region = {'start': 501.1, 'end': region_end, 'pattern_id': 614}
        marker = {'start': 501.1, 'end': region_end, 'pattern_id': 614,
                  'detection_stage': 'text_pattern'}
        return [dict(c) for c in claude], [region], [marker]

    def test_single_tight_llm_ad_wins(self):
        from ad_detector.boundaries import tighten_pattern_regions
        claude, regions, ads = self._fixture(
            [{'start': 501.1, 'end': 539.2, 'confidence': 0.98,
              'category': 'sponsor'}])
        tighten_pattern_regions(claude, regions, ads, None)
        assert (ads[0]['start'], ads[0]['end']) == (501.1, 539.2)
        assert (regions[0]['start'], regions[0]['end']) == (501.1, 539.2)

    def test_matching_bounds_are_untouched(self):
        from ad_detector.boundaries import tighten_pattern_regions
        claude, regions, ads = self._fixture(
            [{'start': 501.1, 'end': 539.2, 'confidence': 0.98,
              'category': 'sponsor'}], region_end=545.0)
        tighten_pattern_regions(claude, regions, ads, None)
        assert ads[0]['end'] == 545.0

    def test_two_llm_ads_inside_leave_the_region_alone(self):
        from ad_detector.boundaries import tighten_pattern_regions
        claude, regions, ads = self._fixture([
            {'start': 501.1, 'end': 530.0, 'confidence': 0.98,
             'category': 'sponsor'},
            {'start': 560.0, 'end': 595.0, 'confidence': 0.97,
             'category': 'sponsor'},
        ])
        tighten_pattern_regions(claude, regions, ads, None)
        assert ads[0]['end'] == 601.1

    def test_low_confidence_does_not_tighten(self):
        from ad_detector.boundaries import tighten_pattern_regions
        claude, regions, ads = self._fixture(
            [{'start': 501.1, 'end': 539.2, 'confidence': 0.7,
              'category': 'sponsor'}])
        tighten_pattern_regions(claude, regions, ads, None)
        assert ads[0]['end'] == 601.1

    def test_keep_category_detection_does_not_tighten(self):
        from ad_detector.boundaries import tighten_pattern_regions
        claude, regions, ads = self._fixture(
            [{'start': 501.1, 'end': 539.2, 'confidence': 0.98,
              'category': 'self_promo'}])
        tighten_pattern_regions(claude, regions, ads,
                                {'self_promo': 'keep'})
        assert ads[0]['end'] == 601.1

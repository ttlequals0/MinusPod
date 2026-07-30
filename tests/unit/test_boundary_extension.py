"""Content-based ad boundary extension (extend_ad_boundaries_by_content).

Covers the tail-completion behaviors: the sandwich rule (connector segments
between sponsor mentions don't stop the end walk), phone-number CTA patterns,
and the end-only mode used by the post-reviewer pass.
"""

from ad_detector.boundaries import (
    extend_ad_boundaries_by_content,
    _text_has_ad_content,
)


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


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
        _seg(200.5, 207.0, 'call 1-800-grainger for the ones who get it done'),
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
        _seg(118.0, 125.0, 'go to example dot com slash podcast'),
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

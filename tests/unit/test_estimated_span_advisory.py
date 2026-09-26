"""tests/unit/test_estimated_span_advisory.py"""
from tests.app_bootstrap import bootstrap
bootstrap('estimated_span_advisory_test_')

from unittest.mock import MagicMock

import pytest

from ad_detector import AdDetector, _label_reach
from ad_detector.boundaries import get_uncovered_portions, tighten_pattern_regions
from text_pattern_matcher import TextPatternMatcher, AdPattern, TextMatch
from tests.unit.marker_test_utils import member_bases
from utils.markers import note_fold

SEGMENTS = [{'start': s, 'end': s + 5.0, 'text': 'words ' * 12}
            for s in range(1970, 2100, 5)]


def test_estimated_pattern_span_does_not_clear_hold():
    # start=2040 (< last['end']=2043) forces a true overlap so the merge
    # reaches the stage-set corroboration check instead of the separate
    # (#541) adjacency-is-not-corroboration bypass for touching spans.
    ads = [
        {'start': 1976.0, 'end': 2043.0, 'confidence': 0.94,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'span_estimated': True, 'sponsor': 'ShowOutro',
         'reason': 'ShowOutro (pattern #600)', 'category': 'sponsor'},
        {'start': 2040.0, 'end': 2084.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'differential_uncorroborated': True, 'held_for_review': True,
         'reason': 'Audio differs across fetches', 'category': 'sponsor'},
    ]
    d = AdDetector.__new__(AdDetector)
    merged = d._merge_detection_results(ads, SEGMENTS, action_map=None)
    held = [m for m in merged if m.get('held_for_review')]
    assert held, "estimated span must not corroborate the differential hold"


def test_estimated_pattern_stage_promotion_does_not_corroborate_hold():
    # Three-member fold promotes last's stage to text_pattern before the held
    # differential arrives; that promoted stage must still count as advisory.
    ads = [
        {'start': 2000.0, 'end': 2044.0, 'confidence': 0.9,
         'detection_stage': 'claude', 'reason': 'ad read', 'category': 'sponsor'},
        {'start': 2005.0, 'end': 2043.0, 'confidence': 0.85,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'span_estimated': True, 'sponsor': 'Acme',
         'reason': 'Acme (pattern #600)', 'category': 'sponsor'},
        {'start': 2040.0, 'end': 2084.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'differential_uncorroborated': True, 'held_for_review': True,
         'reason': 'Audio differs across fetches', 'category': 'sponsor'},
    ]
    d = AdDetector.__new__(AdDetector)
    merged = d._merge_detection_results(ads, None, action_map=None)
    assert len(merged) == 1
    assert merged[0].get('held_for_review')
    assert merged[0].get('differential_uncorroborated')


CLAUDE_MEMBER = {'start': 649.4, 'end': 921.1, 'confidence': 0.9,
                 'detection_stage': 'claude', 'reason': 'ad read'}


def _estimated_member(start=831.75):
    return {'start': start, 'end': 999.65, 'confidence': 0.85,
            'detection_stage': 'text_pattern', 'pattern_id': 600,
            'span_estimated': True, 'text_start': 831.75, 'text_end': 860.0,
            'sponsor': 'Acme', 'reason': 'Acme (pattern #600)'}


@pytest.mark.parametrize('ads,members', [
    pytest.param([CLAUDE_MEMBER, _estimated_member()],
                 [{'start': 649.4, 'end': 921.1, 'stage': 'claude'},
                  {'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'}],
                 id='claude_first'),
    pytest.param([_estimated_member(start=649.4), CLAUDE_MEMBER],
                 [{'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'},
                  {'start': 649.4, 'end': 921.1, 'stage': 'claude'}],
                 id='estimate_first'),
])
def test_folded_marker_records_only_the_matched_text(ads, members):
    # The accumulator may start as either member; the estimated tail never
    # becomes protected audio the reviewer has to keep.
    d = AdDetector.__new__(AdDetector)

    merged = d._merge_detection_results([dict(a) for a in ads], None,
                                        action_map=None)

    assert len(merged) == 1
    assert member_bases(merged[0]['merged_member_spans']) == members


def test_estimate_moved_by_a_snap_still_narrows_to_its_text():
    # A snap moved the end 0.4s; the estimated tail is still not evidence.
    ad = dict(_estimated_member(), end=1000.05)

    note_fold(ad, {'start': 1100.0, 'end': 1200.0, 'confidence': 0.9,
                   'detection_stage': 'claude'})

    assert member_bases(ad['merged_member_spans'][0]) == {
        'start': 831.75, 'end': 860.0, 'stage': 'text_pattern'}


def test_tightening_moves_the_span_but_not_the_label_reach():
    # Snapping to LLM bounds only moves the bounds: the flag still gates
    # corroboration, and the label still reaches only the matched text.
    region = {'start': 501.1, 'end': 601.1, 'pattern_id': 614}
    marker = {'start': 501.1, 'end': 601.1, 'pattern_id': 614,
              'detection_stage': 'text_pattern', 'span_estimated': True,
              'text_start': 501.1, 'text_end': 510.0}
    claude = [{'start': 501.1, 'end': 539.2, 'confidence': 0.98,
               'category': 'sponsor'}]

    tighten_pattern_regions(claude, [region], [marker], None)

    assert (marker['start'], marker['end']) == (501.1, 539.2)
    assert marker['span_estimated'] is True
    assert _label_reach(marker) == pytest.approx(8.9)


@pytest.mark.parametrize('pattern_first', [False, True])
def test_bundled_llm_bound_stops_estimated_pattern_tail(pattern_first):
    claude = {'start': 100.0, 'end': 222.0, 'confidence': 0.98,
              'category': 'sponsor', 'detection_stage': 'claude',
              'reason': 'Two ad reads'}
    marker = {'start': 185.0, 'end': 295.0, 'confidence': 0.85,
              'category': 'sponsor', 'detection_stage': 'text_pattern',
              'pattern_id': 600, 'span_estimated': True,
              'text_start': 185.0, 'text_end': 222.0,
              'reason': 'Matched second ad'}
    region = {'start': 185.0, 'end': 295.0, 'pattern_id': 600,
              'category': 'sponsor'}

    tighten_pattern_regions([claude], [region], [marker], None)
    uncovered = get_uncovered_portions(claude, [region])
    candidates = [marker, *uncovered] if pattern_first else [*uncovered, marker]
    merged = AdDetector.__new__(AdDetector)._merge_detection_results(
        candidates, action_map=None)

    assert (marker['start'], marker['end']) == (185.0, 222.0)
    assert (region['start'], region['end']) == (185.0, 222.0)
    assert [(m['start'], m['end']) for m in merged] == [(100.0, 222.0)]
    assert member_bases(merged[0]['merged_member_spans']) == [
        {'start': 100.0, 'end': 222.0, 'stage': 'claude'},
        {'start': 185.0, 'end': 222.0, 'stage': 'text_pattern'},
    ]


def test_bundled_llm_bound_stops_estimated_pattern_head():
    marker = {'start': 100.0, 'end': 222.0, 'pattern_id': 600,
              'span_estimated': True, 'text_start': 185.0,
              'text_end': 222.0}
    region = {'start': 100.0, 'end': 222.0, 'pattern_id': 600}
    claude = [{'start': 150.0, 'end': 250.0, 'confidence': 0.98,
               'category': 'sponsor'}]

    tighten_pattern_regions(claude, [region], [marker], None)

    assert (marker['start'], marker['end']) == (150.0, 222.0)
    assert (region['start'], region['end']) == (150.0, 222.0)


@pytest.mark.parametrize('claude,text_end', [
    ([], 222.0),
    ([{'start': 225.0, 'end': 260.0, 'confidence': 0.98,
       'category': 'sponsor'}], 222.0),
    ([{'start': 100.0, 'end': 222.0, 'confidence': 0.98,
       'category': 'sponsor'},
      {'start': 184.0, 'end': 223.0, 'confidence': 0.98,
       'category': 'sponsor'}], 222.0),
    ([{'start': 100.0, 'end': 191.0, 'confidence': 0.98,
       'category': 'sponsor'}], 191.0),
])
def test_estimated_pattern_stays_advisory_without_full_anchor(claude, text_end):
    marker = {'start': 185.0, 'end': 295.0, 'pattern_id': 600,
              'span_estimated': True, 'text_start': 185.0,
              'text_end': text_end}
    region = {'start': 185.0, 'end': 295.0, 'pattern_id': 600}

    tighten_pattern_regions(claude, [region], [marker], None)

    assert marker['end'] == 295.0
    assert region['end'] == 295.0


def test_estimated_pattern_without_in_span_text_stays_advisory():
    marker = {'start': 185.0, 'end': 295.0, 'pattern_id': 600,
              'span_estimated': True, 'text_start': 100.0,
              'text_end': 120.0}
    region = {'start': 185.0, 'end': 295.0, 'pattern_id': 600}
    claude = [{'start': 100.0, 'end': 220.0, 'confidence': 0.98,
               'category': 'sponsor'}]

    tighten_pattern_regions(claude, [region], [marker], None)

    assert marker['end'] == 295.0
    assert region['end'] == 295.0


def test_label_reach_is_clipped_to_the_entry_span():
    # Text bounds recorded outside the span claim no audio at all.
    entry = {'start': 831.75, 'end': 999.65, 'span_estimated': True,
             'text_start': 700.0, 'text_end': 800.0}

    assert _label_reach(entry) == 0.0


def test_merge_matches_propagates_estimated_span_conservatively():
    # Grounded 100-140 match (wins as "best") folds with an estimated 60-145
    # one; span_estimated must survive from the losing member too.
    matcher = TextPatternMatcher.__new__(TextPatternMatcher)
    grounded = TextMatch(pattern_id=1, start=100.0, end=140.0, confidence=0.95,
                          sponsor='Acme', match_type='outro',
                          span_estimated=False, text_start=100.0, text_end=140.0)
    estimated = TextMatch(pattern_id=2, start=60.0, end=145.0, confidence=0.9,
                           sponsor='Acme', match_type='outro',
                           span_estimated=True)

    merged = matcher._merge_matches([grounded, estimated])

    assert len(merged) == 1
    result = merged[0]
    assert (result.start, result.end) == (60.0, 145.0)
    assert result.span_estimated is True
    assert (result.text_start, result.text_end) == (100.0, 140.0)


class TestOutroAnchoredSpanEstimation:
    """_find_phrase_matches (the outro-anchored branch touched by this task)
    must stamp span_estimated at construction time, not only when a test
    hand-sets the flag on a directly constructed TextMatch."""

    def _matcher(self):
        matcher = TextPatternMatcher.__new__(TextPatternMatcher)
        matcher._patterns = []
        matcher._pattern_vectors = None
        matcher._vectorizer = None
        matcher._pattern_buckets = {}
        return matcher

    def _pattern(self):
        return AdPattern(
            id=600, text_template='outro', intro_variants=[],
            outro_variants=['thanks for listening to the show'],
            sponsor='ShowOutro', scope='global',
        )

    def test_outro_without_paired_intro_is_estimated(self):
        matcher = self._matcher()
        matcher._fuzzy_find = MagicMock(
            return_value=(500, 95, 'thanks for listening to the show'))
        matcher._char_pos_to_time = MagicMock(return_value=(140.0, 145.0))
        matcher._scan_for_intro = MagicMock(return_value=None)

        matches = matcher._find_phrase_matches('a' * 600, [], [], [self._pattern()])

        assert len(matches) == 1
        assert matches[0].span_estimated is True

    def test_outro_with_paired_intro_is_not_estimated(self):
        matcher = self._matcher()
        matcher._fuzzy_find = MagicMock(
            return_value=(500, 95, 'thanks for listening to the show'))
        matcher._char_pos_to_time = MagicMock(return_value=(140.0, 145.0))
        matcher._scan_for_intro = MagicMock(return_value=100.0)

        matches = matcher._find_phrase_matches('a' * 600, [], [], [self._pattern()])

        assert len(matches) == 1
        assert matches[0].span_estimated is False


def test_content_match_span_estimated_stays_false():
    # _score_windows (match_type='content') is a separate construction site
    # from the intro/outro phrase path; it never estimates a boundary, so
    # its matches must keep the dataclass default of span_estimated=False.
    from sklearn.feature_extraction.text import TfidfVectorizer

    ad_copy = (
        "this episode is brought to you by acme mattress the best "
        "mattress for deep sleep visit acme dot com slash podcast "
        "for twenty percent off your first order"
    )
    filler = (
        "the hosts talk about the news of the week and answer listener "
        "questions about many different unrelated topics and stories "
    )
    full_text = filler + ad_copy + " " + filler
    segments = [{'start': 0.0, 'end': 600.0}]
    segment_map = [(0, len(full_text), 0)]

    matcher = TextPatternMatcher.__new__(TextPatternMatcher)
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 3), min_df=1, stop_words='english', lowercase=True)
    vectorizer.fit([ad_copy])
    matcher._vectorizer = vectorizer

    pattern = AdPattern(id=1, text_template=ad_copy, intro_variants=[],
                        outro_variants=[], sponsor='Acme', scope='global')
    target_vectors = vectorizer.transform([pattern.text_template])

    matches = []
    matcher._score_windows(full_text, segment_map, segments, matches,
                           [pattern], target_vectors, 180, 60)

    assert matches, "expected the ad-copy window to match its own pattern"
    assert all(m.span_estimated is False for m in matches)

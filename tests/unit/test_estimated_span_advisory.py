"""tests/unit/test_estimated_span_advisory.py"""
from tests.app_bootstrap import bootstrap
bootstrap('estimated_span_advisory_test_')

from unittest.mock import MagicMock

from ad_detector import AdDetector
from text_pattern_matcher import TextPatternMatcher, AdPattern, TextMatch

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
    # Three-member fold: claude folds with an estimated text_pattern member
    # first (promoting last's stage to text_pattern via stage priority),
    # then a held differential folds in with segments=None. The promoted
    # stage must still be recognized as advisory so it cannot corroborate
    # the hold.
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


def test_merge_matches_propagates_estimated_span_conservatively():
    # A fully grounded match (100-140, higher confidence -> wins as "best")
    # folds with a lower-confidence match whose start is duration-estimated
    # (60-145). Taking span_estimated only from the winning member would
    # silently drop the advisory flag and let label reach expand to the
    # ungrounded 60-145 span.
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

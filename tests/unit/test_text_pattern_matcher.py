"""Unit tests for text_pattern_matcher helper functions and ad_detector region helpers."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock

from text_pattern_matcher import (
    _split_sentences, _extract_intro_phrase, _extract_outro_phrase,
    TextPatternMatcher, AdPattern, TextMatch, MAX_MATCH_DURATION,
    MIN_TEXT_LENGTH,
)
from ad_detector import _unpack_region, get_uncovered_portions, AdDetector
from config import DEFAULT_AD_DURATION_ESTIMATE, TFIDF_MATCH_THRESHOLD


class TestSplitSentences:
    """Tests for _split_sentences."""

    def test_basic_splitting(self):
        text = "Hello world. How are you? I am fine!"
        result = _split_sentences(text)
        assert result == ["Hello world.", "How are you?", "I am fine!"]

    def test_no_punctuation_returns_whole_text(self):
        text = "this is a sentence without punctuation"
        result = _split_sentences(text)
        assert result == [text]

    def test_empty_string(self):
        assert _split_sentences("") == []

    def test_single_sentence(self):
        result = _split_sentences("Just one sentence.")
        assert result == ["Just one sentence."]

    def test_extra_whitespace(self):
        text = "First sentence.   Second sentence."
        result = _split_sentences(text)
        assert result == ["First sentence.", "Second sentence."]


class TestExtractIntroPhrase:
    """Tests for _extract_intro_phrase."""

    def test_stops_at_min_words(self):
        # Build text with 3 sentences, each ~10 words
        s1 = "This is the first sentence of the ad read."
        s2 = "And here comes the second sentence of the ad."
        s3 = "Finally the third sentence wraps up the whole thing."
        text = f"{s1} {s2} {s3}"
        result = _extract_intro_phrase(text, min_words=15, max_words=60)
        # Should include s1 + s2 (20 words >= 15) and stop before s3
        assert result.startswith("This is the first")
        assert "second sentence" in result
        assert "third sentence" not in result

    def test_text_shorter_than_min_words(self):
        text = "Short text here."
        result = _extract_intro_phrase(text, min_words=20, max_words=60)
        assert result == text

    def test_max_words_cap(self):
        words = " ".join([f"word{i}" for i in range(100)])
        text = f"{words}."
        result = _extract_intro_phrase(text, min_words=20, max_words=30)
        result_word_count = len(result.split())
        # Single sentence exceeds max_words but is the first sentence so it gets included
        assert result_word_count <= 101  # whole sentence is included

    def test_empty_text(self):
        assert _extract_intro_phrase("") == ""


class TestExtractOutroPhrase:
    """Tests for _extract_outro_phrase."""

    def test_extracts_from_end(self):
        s1 = "This is the first sentence of the ad."
        s2 = "And the second sentence continues here."
        s3 = "Visit our site at example dot com slash promo."
        text = f"{s1} {s2} {s3}"
        result = _extract_outro_phrase(text, min_words=8, max_words=40)
        assert "example dot com" in result
        assert "first sentence" not in result

    def test_text_shorter_than_min_words(self):
        text = "Short outro."
        result = _extract_outro_phrase(text, min_words=15, max_words=40)
        assert result == text

    def test_reversed_sentence_order_preserved(self):
        s1 = "First sentence here."
        s2 = "Second sentence here."
        s3 = "Third sentence here."
        text = f"{s1} {s2} {s3}"
        result = _extract_outro_phrase(text, min_words=4, max_words=40)
        # Even though we iterate in reverse, result should be in original order
        if "Second" in result and "Third" in result:
            assert result.index("Second") < result.index("Third")

    def test_empty_text(self):
        assert _extract_outro_phrase("") == ""


class TestComputeOverlap:
    """Tests for AdDetector._compute_overlap."""

    def test_full_overlap(self):
        assert AdDetector._compute_overlap(10, 50, 10, 50) == 1.0

    def test_partial_overlap(self):
        result = AdDetector._compute_overlap(10, 30, 20, 40)
        # overlap = 30-20 = 10, b_duration = 40-20 = 20, fraction = 0.5
        assert abs(result - 0.5) < 0.001

    def test_no_overlap(self):
        assert AdDetector._compute_overlap(10, 20, 30, 40) == 0.0

    def test_zero_duration_region(self):
        assert AdDetector._compute_overlap(10, 20, 30, 30) == 0.0


class TestUnpackRegion:
    """Tests for _unpack_region."""

    def test_dict_input(self):
        region = {'start': 10.0, 'end': 20.0, 'pattern_id': 42}
        assert _unpack_region(region) == (10.0, 20.0)

    def test_tuple_input(self):
        region = (10.0, 20.0)
        assert _unpack_region(region) == (10.0, 20.0)

    def test_list_input(self):
        region = [5.0, 15.0]
        assert _unpack_region(region) == (5.0, 15.0)


class TestGetUncoveredPortionsWithDicts:
    """Tests for get_uncovered_portions using dict-format regions."""

    def test_fully_covered_by_dict_regions(self):
        ad = {'start': 100.0, 'end': 200.0, 'confidence': 0.9, 'reason': 'test'}
        covered = [{'start': 90.0, 'end': 210.0, 'pattern_id': 1}]
        result = get_uncovered_portions(ad, covered)
        assert result == []

    def test_partial_coverage_returns_tail(self):
        ad = {'start': 100.0, 'end': 200.0, 'confidence': 0.9, 'reason': 'test'}
        # Cover first 70% (70s out of 100s)
        covered = [{'start': 95.0, 'end': 170.0, 'pattern_id': 1}]
        result = get_uncovered_portions(ad, covered, min_duration=5.0)
        assert len(result) == 1
        assert abs(result[0]['start'] - 170.0) < 0.1
        assert abs(result[0]['end'] - 200.0) < 0.1

    def test_no_coverage_returns_original(self):
        ad = {'start': 100.0, 'end': 200.0, 'confidence': 0.9, 'reason': 'test'}
        covered = [{'start': 300.0, 'end': 400.0, 'pattern_id': 1}]
        result = get_uncovered_portions(ad, covered)
        assert len(result) == 1
        assert result[0]['start'] == 100.0
        assert result[0]['end'] == 200.0

    def test_mixed_dict_and_tuple_regions(self):
        ad = {'start': 100.0, 'end': 200.0, 'confidence': 0.9, 'reason': 'test'}
        covered = [
            {'start': 95.0, 'end': 150.0, 'pattern_id': 1},
            (150.0, 210.0)
        ]
        result = get_uncovered_portions(ad, covered)
        # Fully covered by combination
        assert result == []

    def test_zero_duration_ad(self):
        ad = {'start': 100.0, 'end': 100.0, 'confidence': 0.9, 'reason': 'test'}
        covered = [{'start': 90.0, 'end': 110.0}]
        result = get_uncovered_portions(ad, covered)
        assert result == []


class TestScanForBoundary:
    """Tests for _scan_for_boundary via _scan_for_outro and _scan_for_intro."""

    def _make_matcher(self):
        matcher = TextPatternMatcher.__new__(TextPatternMatcher)
        matcher._patterns = []
        matcher._pattern_vectors = None
        matcher._vectorizer = None
        matcher._pattern_buckets = {}
        return matcher

    def test_scan_for_outro_returns_end_time(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[],
            outro_variants=["visit our website today"],
            sponsor="test", scope="podcast",
        )
        # Mock _fuzzy_find to return a match at position 10
        matcher._fuzzy_find = MagicMock(return_value=(10, 85))
        # Mock _char_pos_to_time to return known times
        matcher._char_pos_to_time = MagicMock(return_value=(50.0, 55.0))

        full_text = "a" * 200
        result = matcher._scan_for_outro(full_text, {}, [], pattern, 0)

        assert result == 55.0
        matcher._fuzzy_find.assert_called_once()
        matcher._char_pos_to_time.assert_called_once()

    def test_scan_for_intro_returns_start_time(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test",
            intro_variants=["brought to you by testco"],
            outro_variants=[], sponsor="test", scope="podcast",
        )
        matcher._fuzzy_find = MagicMock(return_value=(5, 90))
        matcher._char_pos_to_time = MagicMock(return_value=(30.0, 35.0))

        full_text = "a" * 200
        result = matcher._scan_for_intro(full_text, {}, [], pattern, 200)

        assert result == 30.0

    def test_scan_for_boundary_no_variants_returns_none(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[],
            outro_variants=[], sponsor="test", scope="podcast",
        )

        result = matcher._scan_for_outro("some text", {}, [], pattern, 0)
        assert result is None

    def test_scan_for_boundary_short_phrase_skipped(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[],
            outro_variants=["short"],  # < 10 chars, should be skipped
            sponsor="test", scope="podcast",
        )
        matcher._fuzzy_find = MagicMock()

        result = matcher._scan_for_outro("a" * 200, {}, [], pattern, 0)
        assert result is None
        matcher._fuzzy_find.assert_not_called()

    def test_scan_for_boundary_low_score_rejected(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[],
            outro_variants=["a long enough outro phrase here"],
            sponsor="test", scope="podcast",
        )
        # Score below FUZZY_THRESHOLD * 100 (75)
        matcher._fuzzy_find = MagicMock(return_value=(10, 50))

        result = matcher._scan_for_outro("a" * 200, {}, [], pattern, 0)
        assert result is None


class TestEstimateDuration:
    """Tests for _estimate_end_from_duration and _estimate_start_from_duration."""

    def _make_matcher(self):
        matcher = TextPatternMatcher.__new__(TextPatternMatcher)
        return matcher

    def test_end_from_duration_uses_avg(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=45.0,
        )
        assert matcher._estimate_end_from_duration(pattern, 100.0) == 145.0

    def test_end_from_duration_none_uses_default(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=None,
        )
        assert matcher._estimate_end_from_duration(pattern, 100.0) == 100.0 + DEFAULT_AD_DURATION_ESTIMATE

    def test_end_from_duration_zero_uses_zero(self):
        """avg_duration=0.0 should use 0 (not fall back to default)."""
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=0.0,
        )
        assert matcher._estimate_end_from_duration(pattern, 100.0) == 100.0

    def test_start_from_duration_uses_avg(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=30.0,
        )
        assert matcher._estimate_start_from_duration(pattern, 100.0) == 70.0

    def test_start_from_duration_none_uses_default(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=None,
        )
        assert matcher._estimate_start_from_duration(pattern, 100.0) == max(0, 100.0 - DEFAULT_AD_DURATION_ESTIMATE)

    def test_start_from_duration_clamps_to_zero(self):
        matcher = self._make_matcher()
        pattern = AdPattern(
            id=1, text_template="test", intro_variants=[], outro_variants=[],
            sponsor="test", scope="podcast", avg_duration=200.0,
        )
        assert matcher._estimate_start_from_duration(pattern, 50.0) == 0


def _make_bare_matcher():
    """A matcher built without __init__, with the attributes the methods under
    test read set to inert defaults (no DB, no loaded patterns)."""
    matcher = TextPatternMatcher.__new__(TextPatternMatcher)
    matcher.db = None
    matcher._patterns = []
    matcher._pattern_vectors = None
    matcher._vectorizer = None
    matcher._pattern_buckets = {}
    return matcher


class TestConstrainOverlongSpans:
    """Regression tests for the text_pattern over-cut fix.

    Reproduces the Brilliant Idiots case: a merged span whose leading minutes
    are show content with zero sponsor mentions and the real ad only at the
    tail. The matcher must trim the span to the brand-bearing region instead of
    cutting the content.
    """

    def _overcut_segments(self):
        # 0-360s show content (no brand), then the real Hims ad at 360-420s.
        segs = [
            {'start': i * 30.0, 'end': (i + 1) * 30.0,
             'text': 'the knicks parade basketball debate ring talk'}
            for i in range(12)
        ]
        segs.append({'start': 360.0, 'end': 390.0,
                     'text': 'salute to Hims dot com when thinning hair starts'})
        segs.append({'start': 390.0, 'end': 420.0,
                     'text': 'go to Hims dot com slash idiots for your free trial'})
        return segs

    def test_trims_overcut_span_to_brand_region(self):
        matcher = _make_bare_matcher()
        match = TextMatch(pattern_id=1, start=0.0, end=420.0, confidence=0.9,
                          sponsor='Hims', match_type='both')
        result = matcher._constrain_overlong_spans([match], self._overcut_segments())
        assert len(result) == 1
        # Start pulled forward to the first Hims-bearing segment; content kept.
        assert result[0].start == 360.0
        assert result[0].end == 420.0

    def test_brand_substring_in_content_does_not_block_trim(self):
        # Leading content contains 'whims' -- a substring of 'Hims' but not a
        # whole word. Word-boundary matching must ignore it so the span still
        # trims to the real ad, not the content.
        matcher = _make_bare_matcher()
        segs = [{'start': i * 30.0, 'end': (i + 1) * 30.0,
                 'text': 'the whims of the knicks parade and minor things'}
                for i in range(12)]  # 360s, 'whims' substring-matches 'Hims'
        segs.append({'start': 360.0, 'end': 390.0,
                     'text': 'salute to Hims dot com when thinning hair starts'})
        segs.append({'start': 390.0, 'end': 420.0,
                     'text': 'go to Hims dot com slash idiots for your free trial'})
        match = TextMatch(pattern_id=1, start=0.0, end=420.0, confidence=0.9,
                          sponsor='Hims', match_type='both')
        result = matcher._constrain_overlong_spans([match], segs)
        assert len(result) == 1
        assert result[0].start == 360.0
        assert result[0].end == 420.0

    def test_drops_long_span_with_no_brand(self):
        matcher = _make_bare_matcher()
        segs = [{'start': i * 30.0, 'end': (i + 1) * 30.0,
                 'text': 'pure show content basketball'} for i in range(12)]  # 360s
        match = TextMatch(pattern_id=1, start=0.0, end=360.0, confidence=0.9,
                          sponsor='Hims', match_type='content')
        assert matcher._constrain_overlong_spans([match], segs) == []

    def test_short_span_untouched_even_without_brand(self):
        matcher = _make_bare_matcher()
        segs = [{'start': 0.0, 'end': 60.0, 'text': 'some content no brand'}]
        match = TextMatch(pattern_id=1, start=0.0, end=60.0, confidence=0.9,
                          sponsor='Hims', match_type='content')
        result = matcher._constrain_overlong_spans([match], segs)
        assert len(result) == 1
        assert (result[0].start, result[0].end) == (0.0, 60.0)

    def test_sponsorless_long_span_dropped(self):
        # No sponsor to anchor the trim, and the span is over the cap: drop it
        # rather than clamp to a guessed window that could cut show content.
        matcher = _make_bare_matcher()
        segs = [{'start': i * 30.0, 'end': (i + 1) * 30.0, 'text': 'content'}
                for i in range(20)]  # 600s
        match = TextMatch(pattern_id=1, start=0.0, end=600.0, confidence=0.9,
                          sponsor=None, match_type='content')
        assert matcher._constrain_overlong_spans([match], segs) == []

    def test_sponsorless_span_at_cap_untouched(self):
        # A sponsorless span exactly at the cap is kept unchanged (the cap is
        # inclusive); only spans strictly over it are dropped.
        matcher = _make_bare_matcher()
        segs = [{'start': 0.0, 'end': MAX_MATCH_DURATION, 'text': 'content'}]
        match = TextMatch(pattern_id=1, start=0.0, end=MAX_MATCH_DURATION,
                          confidence=0.9, sponsor=None, match_type='content')
        result = matcher._constrain_overlong_spans([match], segs)
        assert len(result) == 1
        assert (result[0].start, result[0].end) == (0.0, MAX_MATCH_DURATION)


class TestMergeSponsorGating:
    """Merge must not union different sponsors (would let one sponsor's bad
    anchor drag another's span and hide a co-located ad behind one label)."""

    def test_different_sponsors_not_merged(self):
        matcher = _make_bare_matcher()
        m1 = TextMatch(pattern_id=1, start=100.0, end=160.0, confidence=0.9,
                       sponsor='Hims', match_type='content')
        m2 = TextMatch(pattern_id=2, start=162.0, end=220.0, confidence=0.9,
                       sponsor='Quince', match_type='content')
        result = matcher._merge_matches([m1, m2])
        assert len(result) == 2
        assert {r.sponsor for r in result} == {'Hims', 'Quince'}

    def test_same_sponsor_merged(self):
        matcher = _make_bare_matcher()
        m1 = TextMatch(pattern_id=1, start=100.0, end=160.0, confidence=0.8,
                       sponsor='Hims', match_type='content')
        m2 = TextMatch(pattern_id=1, start=162.0, end=220.0, confidence=0.9,
                       sponsor='Hims', match_type='intro')
        result = matcher._merge_matches([m1, m2])
        assert len(result) == 1
        assert (result[0].start, result[0].end) == (100.0, 220.0)

    def test_unattributed_match_not_merged_into_named_ad(self):
        # A brand-free (sponsor=None) content match must not absorb an adjacent
        # named ad and inherit its sponsor -- that lets content ride along as ad
        # in a span too short for the over-long trim to catch.
        matcher = _make_bare_matcher()
        m1 = TextMatch(pattern_id=1, start=100.0, end=160.0, confidence=0.8,
                       sponsor=None, match_type='content')
        m2 = TextMatch(pattern_id=2, start=162.0, end=220.0, confidence=0.9,
                       sponsor='Hims', match_type='intro')
        result = matcher._merge_matches([m1, m2])
        assert len(result) == 2

    def test_case_variant_sponsors_merged(self):
        matcher = _make_bare_matcher()
        m1 = TextMatch(pattern_id=1, start=100.0, end=160.0, confidence=0.8,
                       sponsor='Hims', match_type='content')
        m2 = TextMatch(pattern_id=2, start=162.0, end=220.0, confidence=0.9,
                       sponsor='hims', match_type='intro')
        result = matcher._merge_matches([m1, m2])
        assert len(result) == 1


class TestMergeDetectionResults:
    """Cross-stage merge must keep a marker's sponsor and reason consistent --
    both from the SAME member -- so it never shows one ad's sponsor with another
    ad's description (the Flagrant mislabels: a Nordstrom pattern that matched a
    host tour-promo, a David Protein read folded into a ZipRecruiter marker)."""

    def _det(self):
        return AdDetector.__new__(AdDetector)

    def test_false_attribution_label_follows_content_reason(self):
        # text_pattern wrongly tagged a host tour-promo as Nordstrom (short,
        # sponsor-derived reason); Claude read the content (longer reason, no
        # external sponsor). The marker must take sponsor AND reason from the
        # content-aware member, not Nordstrom + tour-promo.
        det = self._det()
        ads = [
            {'start': 2074.0, 'end': 2130.0, 'detection_stage': 'text_pattern',
             'sponsor': 'Nordstrom', 'reason': 'Nordstrom (pattern #380)', 'confidence': 1.0},
            {'start': 2075.0, 'end': 2192.0, 'detection_stage': 'claude',
             'sponsor': None,
             'reason': 'Hosts promote their upcoming stand-up shows in Pasadena, Brea, and Halifax.',
             'confidence': 0.9},
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 1
        assert out[0]['sponsor'] is None
        assert 'stand-up shows' in out[0]['reason']
        assert 'Nordstrom' not in (out[0]['reason'] or '')

    def test_distinct_sponsor_fold_keeps_one_consistent_label(self):
        # Two different-sponsor reads merged into one span must not show one
        # sponsor with the other's reason; the surviving label is internally
        # consistent (sponsor matches its own reason).
        det = self._det()
        ads = [
            {'start': 4850.0, 'end': 4908.0, 'detection_stage': 'claude',
             'sponsor': 'David Protein', 'reason': 'David Protein', 'confidence': 0.9},
            {'start': 4908.0, 'end': 5092.0, 'detection_stage': 'claude',
             'sponsor': 'ZipRecruiter',
             'reason': 'ZipRecruiter: according to CNBC nearly half of hiring managers value enthusiasm.',
             'confidence': 0.9},
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 1
        # Longer reason (ZipRecruiter) wins both fields; no cross-member mix.
        assert out[0]['sponsor'] == 'ZipRecruiter'
        assert 'ZipRecruiter' in out[0]['reason']

    def test_same_ad_two_stages_takes_richer_description(self):
        det = self._det()
        ads = [
            {'start': 100.0, 'end': 160.0, 'detection_stage': 'text_pattern',
             'sponsor': 'Hims', 'reason': 'Hims (pattern #1)', 'confidence': 0.8, 'pattern_id': 1},
            {'start': 160.0, 'end': 220.0, 'detection_stage': 'claude',
             'sponsor': 'Hims', 'reason': 'Hims hair-loss read with promo code and detail.', 'confidence': 0.95},
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 1
        assert out[0]['sponsor'] == 'Hims'
        assert out[0]['end'] == 220.0

    def test_trust_stage_decoupled_from_label(self):
        # detection_stage/pattern_id follow stage priority (text_pattern over
        # claude) for cutting trust, while the LABEL follows the richer reason.
        det = self._det()
        ads = [
            {'start': 100.0, 'end': 160.0, 'detection_stage': 'text_pattern',
             'sponsor': 'Hims', 'reason': 'Hims (pattern #1)', 'confidence': 0.8, 'pattern_id': 7},
            {'start': 150.0, 'end': 210.0, 'detection_stage': 'claude',
             'sponsor': 'Hims', 'reason': 'Hims: a longer content-derived description here.', 'confidence': 0.9},
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 1
        assert out[0]['detection_stage'] == 'text_pattern'
        assert out[0]['pattern_id'] == 7
        assert 'content-derived' in out[0]['reason']
        assert out[0]['sponsor'] == 'Hims'


class TestScoreWindowsBatched:
    """Batched _score_windows must produce scores identical to the old
    per-window transform loop (one transform per window)."""

    def _make_matcher_and_patterns(self):
        from sklearn.feature_extraction.text import TfidfVectorizer

        ad_copy = (
            "this episode is brought to you by acme mattress the best "
            "mattress for deep sleep visit acme dot com slash podcast "
            "for twenty percent off your first order"
        )
        other_copy = (
            "try globex meal kits fresh ingredients delivered weekly "
            "use code podcast at checkout for a free box"
        )
        matcher = TextPatternMatcher()
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 3), min_df=1, stop_words='english', lowercase=True
        )
        vectorizer.fit([ad_copy, other_copy])
        matcher._vectorizer = vectorizer
        matcher._initialized = True

        patterns = [
            AdPattern(id=1, text_template=ad_copy, intro_variants=[],
                      outro_variants=[], sponsor='Acme', scope='global'),
            AdPattern(id=2, text_template=other_copy, intro_variants=[],
                      outro_variants=[], sponsor='Globex', scope='global'),
        ]
        target_vectors = vectorizer.transform(
            [p.text_template for p in patterns]
        )

        filler = (
            "the hosts talk about the news of the week and answer listener "
            "questions about many different unrelated topics and stories "
        )
        full_text = filler + ad_copy + " " + filler
        segments = [{'start': 0.0, 'end': 600.0}]
        segment_map = [(0, len(full_text), 0)]
        return matcher, patterns, target_vectors, full_text, segments, segment_map

    def test_batched_scores_equal_per_window(self):
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        (matcher, patterns, target_vectors, full_text,
         segments, segment_map) = self._make_matcher_and_patterns()
        window_size, step_size = 180, 60

        matches = []
        matcher._score_windows(full_text, segment_map, segments, matches,
                               patterns, target_vectors,
                               window_size, step_size)

        # Reference: the old per-window loop, one transform per window.
        ref_matches = []
        per_window_rows = []
        batched_texts = []
        for start_pos in range(0, len(full_text) - MIN_TEXT_LENGTH, step_size):
            end_pos = min(start_pos + window_size, len(full_text))
            window_text = full_text[start_pos:end_pos]
            if len(window_text.strip()) < MIN_TEXT_LENGTH:
                continue
            batched_texts.append(window_text)
            sims = cosine_similarity(
                matcher._vectorizer.transform([window_text]), target_vectors
            )[0]
            per_window_rows.append(sims)
            best_idx = int(np.argmax(sims))
            best_score = float(sims[best_idx])
            if best_score >= TFIDF_MATCH_THRESHOLD:
                ref_matches.append((patterns[best_idx].id, best_score))

        # The vectorizer treats documents independently: the batched matrix
        # must be numerically identical to stacked per-window transforms.
        batched_sims = cosine_similarity(
            matcher._vectorizer.transform(batched_texts), target_vectors
        )
        assert np.array_equal(batched_sims, np.vstack(per_window_rows))

        # And the matches emitted by _score_windows must equal the old loop's.
        assert ref_matches, "test setup must produce at least one match"
        assert [(m.pattern_id, m.confidence) for m in matches] == ref_matches
        assert all(m.match_type == 'content' for m in matches)
        assert all(m.sponsor == 'Acme' for m in matches)

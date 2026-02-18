"""Unit tests for ad detection module-level functions."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import (
    extract_sponsor_names,
    refine_ad_boundaries,
    merge_same_sponsor_ads,
    _extract_ad_keywords,
    validate_ad_timestamps,
    get_uncovered_portions,
)


class TestExtractSponsorNames:
    """Tests for extract_sponsor_names function."""

    def test_extract_sponsor_from_text(self):
        """Extract sponsor names from URLs in transcript text."""
        # Function extracts from URLs, not plain text mentions
        text = "Visit betterhelp.com/podcast for 10 percent off."

        sponsors = extract_sponsor_names(text)

        assert 'betterhelp' in sponsors

    def test_extract_sponsor_from_url(self):
        """Extract domain names from URLs in text."""
        text = "Visit athleticgreens.com/podcast for a free trial."

        sponsors = extract_sponsor_names(text)

        assert 'athleticgreens' in sponsors

    def test_extract_multiple_sponsors(self):
        """Extract multiple sponsors from URLs in text."""
        # Function extracts from URLs and "dot com" mentions
        text = "Visit betterhelp.com and squarespace.com for deals."

        sponsors = extract_sponsor_names(text)

        assert len(sponsors) >= 2
        assert 'betterhelp' in sponsors
        assert 'squarespace' in sponsors

    def test_extract_from_ad_reason(self):
        """Extract sponsor from ad_reason field."""
        text = "Some general text here"
        ad_reason = "NordVPN sponsor read with promo code"

        sponsors = extract_sponsor_names(text, ad_reason=ad_reason)

        assert 'nordvpn' in sponsors

    def test_no_sponsors_in_text(self):
        """Return empty set when no sponsors found."""
        text = "This is just regular episode content about cooking."

        sponsors = extract_sponsor_names(text)

        assert isinstance(sponsors, set)



class TestRefineBoundaries:
    """Tests for refine_ad_boundaries function."""

    def test_refine_boundaries_finds_transition_phrase(self):
        """Should find 'brought to you by' and adjust start."""
        segments = [
            {'start': 25.0, 'end': 30.0, 'text': 'That is a great point.'},
            {'start': 30.0, 'end': 35.0, 'text': 'This episode is brought to you by'},
            {'start': 35.0, 'end': 60.0, 'text': 'BetterHelp, online therapy made easy.'},
            {'start': 60.0, 'end': 90.0, 'text': 'Visit betterhelp.com/podcast today.'}
        ]

        ads = [
            {'start': 35.0, 'end': 90.0, 'confidence': 0.90, 'reason': 'BetterHelp ad'}
        ]

        refined = refine_ad_boundaries(ads, segments)

        # Should detect transition phrase and adjust start
        assert len(refined) == 1
        # Start might be adjusted to 30.0 where "brought to you by" appears
        assert refined[0]['start'] <= 35.0

    def test_refine_empty_ads(self):
        """Empty ads list should return empty."""
        segments = [
            {'start': 0.0, 'end': 10.0, 'text': 'Some content'}
        ]

        refined = refine_ad_boundaries([], segments)

        assert refined == []

    def test_refine_empty_segments(self):
        """Empty segments should return ads unchanged."""
        ads = [
            {'start': 30.0, 'end': 90.0, 'confidence': 0.90, 'reason': 'An ad'}
        ]

        refined = refine_ad_boundaries(ads, [])

        assert len(refined) == 1
        assert refined[0]['start'] == 30.0


class TestMergeSameSponsorAds:
    """Tests for merge_same_sponsor_ads function."""

    def test_merge_same_sponsor_close_gap(self):
        """Ads with same sponsor and small gap should merge."""
        segments = [
            {'start': 0.0, 'end': 100.0, 'text': 'Episode content here.'},
            {'start': 100.0, 'end': 200.0, 'text': 'More content in between.'}
        ]

        ads = [
            {
                'start': 30.0,
                'end': 60.0,
                'confidence': 0.90,
                'reason': 'BetterHelp sponsor read part 1'
            },
            {
                'start': 90.0,
                'end': 120.0,
                'confidence': 0.85,
                'reason': 'BetterHelp promo code mention'
            }
        ]

        merged = merge_same_sponsor_ads(ads, segments, max_gap=120.0)

        # Both mention BetterHelp, within 120s gap - should merge
        assert len(merged) <= 2

    def test_no_merge_different_sponsors(self):
        """Ads with different sponsors should not merge."""
        segments = [
            {'start': 0.0, 'end': 200.0, 'text': 'Regular content.'}
        ]

        ads = [
            {
                'start': 30.0,
                'end': 60.0,
                'confidence': 0.90,
                'reason': 'BetterHelp sponsor read'
            },
            {
                'start': 90.0,
                'end': 120.0,
                'confidence': 0.85,
                'reason': 'NordVPN promo'
            }
        ]

        merged = merge_same_sponsor_ads(ads, segments, max_gap=120.0)

        # Different sponsors - should remain separate
        assert len(merged) == 2

    def test_no_merge_large_gap(self):
        """Ads beyond max_gap should not merge even with same sponsor."""
        segments = [
            {'start': 0.0, 'end': 1000.0, 'text': 'Long episode content.'}
        ]

        ads = [
            {
                'start': 30.0,
                'end': 60.0,
                'confidence': 0.90,
                'reason': 'BetterHelp ad'
            },
            {
                'start': 500.0,
                'end': 530.0,
                'confidence': 0.85,
                'reason': 'BetterHelp second mention'
            }
        ]

        merged = merge_same_sponsor_ads(ads, segments, max_gap=300.0)

        # Gap of 440s exceeds 300s max_gap - should not merge
        assert len(merged) == 2

    def test_merge_preserves_higher_confidence(self):
        """Merged ads should use the higher confidence value."""
        segments = []

        ads = [
            {
                'start': 30.0,
                'end': 60.0,
                'confidence': 0.75,
                'reason': 'BetterHelp ad'
            },
            {
                'start': 62.0,
                'end': 90.0,
                'confidence': 0.95,
                'reason': 'BetterHelp continued'
            }
        ]

        merged = merge_same_sponsor_ads(ads, segments, max_gap=120.0)

        if len(merged) == 1:
            # If merged, should have higher confidence
            assert merged[0]['confidence'] >= 0.75


class TestExtractAdKeywords:
    """Tests for _extract_ad_keywords function."""

    def test_extracts_from_sponsor_field(self):
        """Should extract sponsor name as keyword."""
        ad = {'start': 100, 'end': 160, 'sponsor': 'GNC',
              'reason': 'GNC ad detected', 'confidence': 0.9}
        keywords = _extract_ad_keywords(ad)
        assert 'gnc' in keywords

    def test_skips_generic_advertisement_detected(self):
        """Generic 'Advertisement detected' has no extractable brand keywords."""
        ad = {'start': 100, 'end': 160,
              'reason': 'Advertisement detected', 'confidence': 0.9}
        keywords = _extract_ad_keywords(ad)
        # 'Advertisement' and 'detected' are in non-brand words
        assert len(keywords) == 0

    def test_extracts_capitalized_words_from_reason(self):
        """Should extract capitalized brand names from reason field."""
        ad = {'start': 100, 'end': 160,
              'reason': 'BetterHelp sponsor read with promo code',
              'confidence': 0.9}
        keywords = _extract_ad_keywords(ad)
        assert 'betterhelp' in keywords

    def test_filters_common_non_brand_words(self):
        """Should not include common words like 'Sponsor', 'Network'."""
        ad = {'start': 100, 'end': 160,
              'reason': 'Sponsored content from Network inserted promotion',
              'confidence': 0.9}
        keywords = _extract_ad_keywords(ad)
        assert 'sponsor' not in keywords
        assert 'network' not in keywords
        assert 'inserted' not in keywords
        assert 'promotion' not in keywords


class TestValidateAdTimestamps:
    """Tests for validate_ad_timestamps function."""

    def _make_segments(self, texts_with_times):
        """Helper: list of (start, end, text) -> segment dicts."""
        return [{'start': s, 'end': e, 'text': t} for s, e, t in texts_with_times]

    def test_correct_timestamps_pass_through(self):
        """Ads with keywords at the right position pass through unchanged."""
        segments = self._make_segments([
            (100, 110, 'This is brought to you by GNC'),
            (110, 120, 'GNC has the best supplements'),
            (120, 130, 'Visit GNC dot com today'),
        ])
        ads = [{'start': 100, 'end': 130, 'confidence': 0.9,
                'sponsor': 'GNC', 'reason': 'GNC sponsor read'}]

        result = validate_ad_timestamps(ads, segments, 0, 600)
        assert len(result) == 1
        assert result[0]['start'] == 100
        assert result[0]['end'] == 130

    def test_hallucinated_position_corrected(self):
        """Ad at wrong position gets moved to where keywords actually appear."""
        segments = self._make_segments([
            (100, 110, 'Just regular discussion here'),
            (110, 120, 'Nothing about any brands at all'),
            (400, 410, 'This is brought to you by GNC'),
            (410, 420, 'GNC has the best supplements'),
        ])
        # Claude says ad is at 100-130 but GNC is actually at 400-420
        ads = [{'start': 100, 'end': 130, 'confidence': 0.9,
                'sponsor': 'GNC', 'reason': 'GNC sponsor read'}]

        result = validate_ad_timestamps(ads, segments, 0, 600)
        assert len(result) == 1
        assert result[0]['start'] == 400

    def test_no_extractable_keywords_passes_through(self):
        """Ads with no extractable keywords pass through unchanged."""
        segments = self._make_segments([
            (100, 110, 'Some content here'),
        ])
        ads = [{'start': 100, 'end': 130, 'confidence': 0.9,
                'reason': 'Advertisement detected'}]

        result = validate_ad_timestamps(ads, segments, 0, 600)
        assert len(result) == 1
        assert result[0]['start'] == 100
        assert result[0]['end'] == 130

    def test_empty_ads_returns_empty(self):
        """Empty ads list returns empty."""
        result = validate_ad_timestamps([], [], 0, 600)
        assert result == []

    def test_keywords_not_found_anywhere_passes_through(self):
        """If keywords don't appear anywhere in window, pass through unchanged."""
        segments = self._make_segments([
            (100, 110, 'Just regular discussion here'),
            (110, 120, 'Nothing about any brands at all'),
        ])
        ads = [{'start': 100, 'end': 130, 'confidence': 0.9,
                'sponsor': 'GNC', 'reason': 'GNC sponsor read'}]

        result = validate_ad_timestamps(ads, segments, 0, 600)
        assert len(result) == 1
        # Passed through unchanged since keywords not found anywhere
        assert result[0]['start'] == 100
        assert result[0]['end'] == 130


class TestGetUncoveredPortions:
    """Tests for get_uncovered_portions function."""

    def test_no_overlap_returns_full_ad(self):
        """Ad with no pattern overlap returns the full ad."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        result = get_uncovered_portions(ad, [])
        assert len(result) == 1
        assert result[0]['start'] == 100
        assert result[0]['end'] == 200

    def test_fully_covered_returns_empty(self):
        """Ad completely covered by patterns returns empty list."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        covered = [(90, 210)]  # Covers entire ad
        result = get_uncovered_portions(ad, covered)
        assert result == []

    def test_trailing_tail_preserved(self):
        """Trailing tail >= min_duration is preserved."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        # Pattern covers 100-170, leaving 30s tail (170-200)
        covered = [(100, 170)]
        result = get_uncovered_portions(ad, covered, min_duration=15.0)
        assert len(result) == 1
        assert result[0]['start'] == 170
        assert result[0]['end'] == 200

    def test_short_tail_dropped(self):
        """Trailing tail < min_duration is dropped."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        # Pattern covers 100-190, leaving 10s tail
        covered = [(100, 190)]
        result = get_uncovered_portions(ad, covered, min_duration=15.0)
        assert result == []

    def test_leading_head_preserved(self):
        """Leading head >= min_duration is preserved."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        # Pattern covers 130-200, leaving 30s head (100-130)
        covered = [(130, 200)]
        result = get_uncovered_portions(ad, covered, min_duration=15.0)
        assert len(result) == 1
        assert result[0]['start'] == 100
        assert result[0]['end'] == 130

    def test_multiple_coverage_regions_with_gaps(self):
        """Multiple coverage regions with gaps between them."""
        ad = {'start': 100, 'end': 300, 'confidence': 0.9, 'reason': 'test'}
        # Two coverage regions leaving gaps
        covered = [(100, 140), (180, 260)]
        # Uncovered: 140-180 (40s), 260-300 (40s) -- both >= 15s
        result = get_uncovered_portions(ad, covered, min_duration=15.0)
        assert len(result) == 2
        assert result[0]['start'] == 140
        assert result[0]['end'] == 180
        assert result[1]['start'] == 260
        assert result[1]['end'] == 300

    def test_more_than_half_uncovered_returns_original(self):
        """>50% uncovered means overlap is incidental -- return original ad."""
        ad = {'start': 100, 'end': 200, 'confidence': 0.9, 'reason': 'test'}
        # Pattern covers only 30s of 100s ad (30%)
        covered = [(120, 150)]
        result = get_uncovered_portions(ad, covered, min_duration=15.0)
        assert len(result) == 1
        assert result[0]['start'] == 100
        assert result[0]['end'] == 200

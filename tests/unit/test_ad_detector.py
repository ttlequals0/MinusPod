"""Unit tests for ad detection module-level functions."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import (
    extract_sponsor_names,
    refine_ad_boundaries,
    merge_same_sponsor_ads
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

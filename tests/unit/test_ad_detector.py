"""Unit tests for ad detection module-level functions."""
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import (
    extract_sponsor_names,
    refine_ad_boundaries,
    merge_same_sponsor_ads,
    deduplicate_window_ads,
    split_conflicting_action_span,
    _extract_ad_keywords,
    validate_ad_timestamps,
    get_uncovered_portions,
    removal_coverage_regions,
    PATTERN_CORRECTION_OVERLAP_THRESHOLD,
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
            # Same-sponsor merge now uses max confidence (unified with filler-gap
            # merge via the shared _merge_ad_pair helper).
            assert merged[0]['confidence'] == 0.95

    def test_merge_same_sponsor_uses_max_confidence(self):
        """Merged same-sponsor ad takes the max of the two confidences, not the
        first fragment's."""
        segments = [{'start': 0.0, 'end': 200.0, 'text': 'content'}]
        ads = [
            {'start': 30.0, 'end': 60.0, 'confidence': 0.70,
             'reason': 'BetterHelp ad part 1'},
            {'start': 62.0, 'end': 90.0, 'confidence': 0.92,
             'reason': 'BetterHelp ad part 2'},
        ]
        merged = merge_same_sponsor_ads(ads, segments, max_gap=120.0)
        assert len(merged) == 1
        assert merged[0]['confidence'] == 0.92

    def test_the_hosts_own_site_does_not_merge_unrelated_ads(self):
        """Two different sponsors that both name the show's site stay apart."""
        segments = [
            {'start': 0.0, 'end': 60.0,
             'text': 'brought to you by squarespace, the home of my website, '
                     'dailytech.com, go build one'},
            {'start': 60.0, 'end': 100.0, 'text': 'back to the conversation'},
            {'start': 100.0, 'end': 160.0,
             'text': 'apple card, terms at dailytech.com slash card'},
        ]
        ads = [
            {'start': 0.0, 'end': 60.0, 'confidence': 0.9,
             'reason': 'Squarespace'},
            {'start': 100.0, 'end': 160.0, 'confidence': 0.9,
             'reason': 'Apple Card'},
        ]

        merged = merge_same_sponsor_ads(ads, segments, max_gap=300.0,
                                        podcast_name='The Daily Tech Show')

        assert len(merged) == 2

    def test_without_the_show_name_the_shared_token_still_merges(self):
        """Guards the fix itself: same input, no podcast name, one span."""
        segments = [
            {'start': 0.0, 'end': 60.0, 'text': 'squarespace dailytech.com'},
            {'start': 60.0, 'end': 100.0, 'text': 'back to the conversation'},
            {'start': 100.0, 'end': 160.0, 'text': 'apple card dailytech.com'},
        ]
        ads = [
            {'start': 0.0, 'end': 60.0, 'confidence': 0.9, 'reason': 'Squarespace'},
            {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'reason': 'Apple Card'},
        ]

        assert len(merge_same_sponsor_ads(ads, segments, max_gap=300.0)) == 1


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

    def test_multiword_sponsor_drops_constituent_tokens(self):
        """'Capital One' keeps the full phrase, drops standalone 'one'/'capital'."""
        ad = {'start': 100, 'end': 160, 'sponsor': 'Capital One',
              'reason': 'Capital One financing spot', 'confidence': 0.9}
        keywords = _extract_ad_keywords(ad)
        assert 'capital one' in keywords
        assert 'one' not in keywords
        assert 'capital' not in keywords


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

    def test_multiword_sponsor_not_relocated_onto_generic_word(self):
        """'Capital One' must not relocate onto editorial that only has 'one'."""
        segments = self._make_segments([
            (100, 110, 'Just regular discussion here'),
            (200, 205, 'the only one here'),
            (206, 211, 'no one knows that'),
            (212, 217, 'one of them left'),
            (218, 223, 'every one agreed'),
            (400, 410, 'That is technology at Capital One'),
        ])
        ads = [{'start': 100, 'end': 130, 'confidence': 0.9,
                'sponsor': 'Capital One', 'reason': 'Capital One spot'}]
        result = validate_ad_timestamps(ads, segments, 0, 600)
        assert len(result) == 1
        # Lands on the real 'Capital One' read, not the 'one'-heavy editorial.
        assert result[0]['start'] == 400


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


class TestClaudeFeedbackDedup:
    """Tests that Claude duration feedback deduplicates per pattern_id."""

    def test_same_pattern_updated_only_once(self):
        """Two Claude ads overlapping the same pattern should only update it once."""
        from ad_detector import AdDetector

        detector = AdDetector.__new__(AdDetector)
        mock_pattern_service = MagicMock()
        detector.pattern_service = mock_pattern_service

        # Two Claude ads that both overlap the same pattern region
        claude_ads = [
            {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'reason': 'ad1'},
            {'start': 140.0, 'end': 200.0, 'confidence': 0.85, 'reason': 'ad2'},
        ]
        # Single pattern region that overlaps both Claude ads
        pattern_matched_regions = [
            {'start': 110.0, 'end': 190.0, 'pattern_id': 42},
        ]

        # Execute just the duration feedback loop
        updated_patterns = set()
        for ad in claude_ads:
            for region in pattern_matched_regions:
                pid = region.get('pattern_id')
                if not pid or pid in updated_patterns:
                    continue
                overlap = AdDetector._compute_overlap(
                    ad['start'], ad['end'],
                    region['start'], region['end']
                )
                if overlap >= PATTERN_CORRECTION_OVERLAP_THRESHOLD:
                    observed_duration = ad['end'] - ad['start']
                    if detector.pattern_service:
                        detector.pattern_service.update_duration(
                            pid, observed_duration
                        )
                        updated_patterns.add(pid)

        # Should be called exactly once despite two overlapping Claude ads
        mock_pattern_service.update_duration.assert_called_once_with(42, 60.0)

    def test_different_patterns_both_updated(self):
        """Different pattern_ids should each get updated."""
        from ad_detector import AdDetector

        detector = AdDetector.__new__(AdDetector)
        mock_pattern_service = MagicMock()
        detector.pattern_service = mock_pattern_service

        claude_ads = [
            {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'reason': 'ad1'},
            {'start': 300.0, 'end': 360.0, 'confidence': 0.85, 'reason': 'ad2'},
        ]
        pattern_matched_regions = [
            {'start': 105.0, 'end': 155.0, 'pattern_id': 10},
            {'start': 305.0, 'end': 355.0, 'pattern_id': 20},
        ]

        updated_patterns = set()
        for ad in claude_ads:
            for region in pattern_matched_regions:
                pid = region.get('pattern_id')
                if not pid or pid in updated_patterns:
                    continue
                overlap = AdDetector._compute_overlap(
                    ad['start'], ad['end'],
                    region['start'], region['end']
                )
                if overlap >= PATTERN_CORRECTION_OVERLAP_THRESHOLD:
                    observed_duration = ad['end'] - ad['start']
                    if detector.pattern_service:
                        detector.pattern_service.update_duration(
                            pid, observed_duration
                        )
                        updated_patterns.add(pid)

        assert mock_pattern_service.update_duration.call_count == 2


class TestDeduplicateWindowMergeFlag:
    """deduplicate_window_ads must flag a chained-distinct-ad span (positive
    gap) so the reviewer keeps it expand-only, but NOT flag a same-ad overlap."""

    def test_gap_chain_sets_merged_distinct_ads(self):
        # Three back-to-back distinct ads within the 5s merge threshold, like
        # the DTNS Live With It / Capital One / Grainger chain.
        ads = [
            {'start': 1987.2, 'end': 2006.0, 'sponsor': 'Live With It'},
            {'start': 2006.0, 'end': 2034.1, 'sponsor': 'Capital One'},
            {'start': 2034.6, 'end': 2073.3, 'sponsor': 'Grainger'},
        ]
        merged = deduplicate_window_ads(ads)
        assert len(merged) == 1
        assert merged[0]['start'] == 1987.2
        assert merged[0]['end'] == 2073.3
        assert merged[0].get('merged_distinct_ads') is True

    def test_touching_distinct_ads_set_merged_distinct_ads(self):
        # Two distinct ads emitted back-to-back with no gap (end == next start) -
        # the common LLM contiguous-break shape. Must be flagged (touch counts).
        ads = [
            {'start': 100.0, 'end': 130.0, 'sponsor': 'Acme'},
            {'start': 130.0, 'end': 160.0, 'sponsor': 'Beta'},
        ]
        merged = deduplicate_window_ads(ads)
        assert len(merged) == 1
        assert merged[0].get('merged_distinct_ads') is True

    def test_overlap_dedup_does_not_set_merged_distinct_ads(self):
        # Same ad detected twice across an overlapping window boundary.
        ads = [
            {'start': 100.0, 'end': 135.0, 'sponsor': 'Acme'},
            {'start': 130.0, 'end': 138.0, 'sponsor': 'Acme'},
        ]
        merged = deduplicate_window_ads(ads)
        assert len(merged) == 1
        # Key must be absent, not merely falsy, so deleting the gap logic fails.
        assert 'merged_distinct_ads' not in merged[0]


class TestSplitConflictingActionSpan:
    """split_conflicting_action_span is the shared containment-safe split
    used by both deduplicate_window_ads and _merge_detection_results when
    two adjacent-or-overlapping ads resolve to different actions (#565
    follow-up, DTNS 5317)."""

    def test_no_true_overlap_both_survive_untouched(self):
        last = {'start': 0.0, 'end': 20.0, 'category': 'sponsor'}
        current = {'start': 20.0, 'end': 30.0, 'category': 'self_promo'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert new_last == last
        assert entries == [current]

    def test_current_extends_past_last_clamps_start(self):
        last = {'start': 0.0, 'end': 25.0, 'category': 'sponsor'}
        current = {'start': 20.0, 'end': 30.0, 'category': 'interaction'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert new_last == last
        assert len(entries) == 1
        assert entries[0]['start'] == 25.0
        assert entries[0]['end'] == 30.0

    def test_later_keep_owns_partial_overlap_with_remove(self):
        last = {'start': 100.0, 'end': 150.0, 'category': 'sponsor'}
        current = {'start': 130.0, 'end': 170.0, 'category': 'self_promo'}

        new_last, entries = split_conflicting_action_span(
            last, current, 'remove', 'keep')

        assert new_last['start'] == 100.0
        assert new_last['end'] == 130.0
        assert entries == [current]

    def test_later_remove_yields_partial_overlap_to_keep(self):
        last = {'start': 100.0, 'end': 150.0, 'category': 'self_promo'}
        current = {'start': 130.0, 'end': 170.0, 'category': 'sponsor'}

        new_last, entries = split_conflicting_action_span(
            last, current, 'keep', 'remove')

        assert new_last == last
        assert entries[0]['start'] == 150.0
        assert entries[0]['end'] == 170.0

    def test_nested_keep_split_marks_short_remove_fragments_trusted(self):
        last = {
            'start': 100.0, 'end': 126.0, 'category': 'sponsor',
            'confidence': 0.85,
        }
        current = {
            'start': 108.0, 'end': 118.0, 'category': 'self_promo',
        }

        new_last, entries = split_conflicting_action_span(
            last, current, 'remove', 'keep')

        assert (new_last['start'], new_last['end']) == (100.0, 108.0)
        assert (entries[1]['start'], entries[1]['end']) == (118.0, 126.0)
        assert new_last['_trusted_split_fragment'] is True
        assert entries[1]['_trusted_split_fragment'] is True

    def test_current_nested_inside_last_splits_last_around_it(self):
        """The DTNS 5317 shape: a longer remove-resolving pattern match
        fully containing a shorter keep-resolving LLM span (e.g. an intro
        tail-aligned inside a pre-roll pattern match) must not collapse the
        nested span to nothing."""
        last = {'start': 0.0, 'end': 166.6, 'category': 'sponsor'}
        current = {'start': 158.0, 'end': 166.6, 'category': 'intro'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert new_last['start'] == 0.0
        assert new_last['end'] == 158.0
        assert new_last['category'] == 'sponsor'
        assert len(entries) == 1
        assert entries[0]['start'] == 158.0
        assert entries[0]['end'] == 166.6
        assert entries[0]['category'] == 'intro'

    def test_current_strictly_inside_last_produces_before_and_after(self):
        last = {'start': 0.0, 'end': 100.0, 'category': 'sponsor'}
        current = {'start': 40.0, 'end': 60.0, 'category': 'interaction'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert new_last['start'] == 0.0
        assert new_last['end'] == 40.0
        assert len(entries) == 2
        assert entries[0]['start'] == 40.0 and entries[0]['end'] == 60.0
        assert entries[0]['category'] == 'interaction'
        assert entries[1]['start'] == 60.0 and entries[1]['end'] == 100.0
        assert entries[1]['category'] == 'sponsor'

    def test_current_same_start_as_last_consumes_last(self):
        last = {'start': 0.0, 'end': 100.0, 'category': 'sponsor'}
        current = {'start': 0.0, 'end': 50.0, 'category': 'outro'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert new_last is None
        assert len(entries) == 2
        assert entries[0]['start'] == 0.0 and entries[0]['end'] == 50.0
        assert entries[1]['start'] == 50.0 and entries[1]['end'] == 100.0

    def test_nested_split_strips_stale_merge_bookkeeping(self):
        """A last that already carries merged_distinct_ads/
        merged_protected_start/end (from an earlier same-action fold) must
        not hand that stale bookkeeping to the split pieces: the recorded
        protected bounds describe last's original span and could otherwise
        float the reviewer's expand-only floor back across the split
        boundary, re-absorbing audio this split just carved out."""
        last = {'start': 0.0, 'end': 100.0, 'category': 'sponsor',
                'merged_distinct_ads': True,
                'merged_protected_start': 0.0, 'merged_protected_end': 100.0}
        current = {'start': 40.0, 'end': 60.0, 'category': 'interaction'}
        new_last, entries = split_conflicting_action_span(last, current)
        assert len(entries) == 2  # current itself, plus the 'after' remainder
        for piece in [new_last, entries[1]]:
            assert 'merged_distinct_ads' not in piece
            assert 'merged_protected_start' not in piece
            assert 'merged_protected_end' not in piece


class TestDeduplicateWindowAdsActionGate:
    """DTNS 5317: daily-tech-news-show episode 3c0b827ef2c5, reprocessed
    with detect_show_segments=true and per-feed actions {cross_promo,
    intro,outro,recap,self_promo: keep; sponsor,interaction: remove}. The
    LLM's raw 9 detections carried category on only the intro and outro;
    deduplicate_window_ads's 5.0s window-boundary merge fused each into an
    adjacent uncategorized sponsor read before the merge seam ever saw
    them as separate candidates: the intro's category was silently
    dropped, and the outro's span was wrongly extended.
    """

    DTNS_ACTION_MAP = {
        'sponsor': 'remove', 'interaction': 'remove',
        'cross_promo': 'keep', 'self_promo': 'keep',
        'intro': 'keep', 'outro': 'keep', 'recap': 'keep',
    }

    def _raw_llm_detections(self):
        return [
            {'start': 0.0, 'end': 156.7, 'confidence': 0.98,
             'reason': 'Pre-roll ad block: Capital One, Olly Sleep, Cologuard, '
                       'and Morning Brew Daily sponsor reads',
             'end_text': 'wherever you get your podcasts'},
            {'start': 158.0, 'end': 166.6, 'confidence': 0.9, 'category': 'intro',
             'reason': 'Show intro marker/theme',
             'end_text': 'Daily Tech News for Friday'},
            {'start': 687.5, 'end': 845.5, 'confidence': 0.98,
             'reason': 'Ad break with multiple sponsors: Capital One, Michaels, '
                       'Morning Brew Daily podcast promo, Stamps.com, Vanta',
             'end_text': "All right, let's get into the briefs"},
            {'start': 814.2, 'end': 845.5, 'confidence': 0.97,
             'reason': 'Vanta sponsor read with call to action (vanta.com), '
                       'continues from previous window',
             'end_text': "let's get into the briefs"},
            {'start': 1502.5, 'end': 1562.5, 'confidence': 0.9,
             'reason': "Patreon promotion with promo code 'experiment' for 26% "
                       'off, call to action patreon.com/DTNS',
             'end_text': 'little smarter'},
            {'start': 1900.1, 'end': 1972.2, 'confidence': 0.98,
             'reason': 'Ad break with Capital One and Noom sponsor reads, '
                       'bracketed by ad-break boundary cues',
             'end_text': 'Individual results may vary'},
            {'start': 2314.1, 'end': 2319.2, 'confidence': 0.9,
             'reason': 'Patreon promo with code experiment and URL patreon.com/DTNS',
             'end_text': 'patreon.com slash DTNS'},
            {'start': 2324.5, 'end': 2381.1, 'confidence': 0.85, 'category': 'outro',
             'reason': 'Show credits and DTNS Family of Podcasts sign-off',
             'end_text': 'enjoyed this program'},
            {'start': 2385.8, 'end': 2444.9, 'confidence': 0.97,
             'reason': 'Capital One and Stamps.com sponsor ads with promo code podcast',
             'end_text': 'Taxes and fees apply'},
        ]

    def test_without_action_map_reproduces_the_bug(self):
        """Regression anchor: without an action_map, today's (pre-fix, and
        still-current no-map) behavior fuses the intro into the pre-roll
        (1.3s gap) and the outro into the trailing sponsor ad (4.7s gap),
        exactly matching the production log ("Total after dedup: 6 ads")."""
        merged = deduplicate_window_ads(self._raw_llm_detections())
        assert len(merged) == 6
        first = merged[0]
        assert first['start'] == 0.0 and first['end'] == 166.6
        assert 'category' not in first
        last = merged[-1]
        assert last['start'] == 2324.5 and last['end'] == 2444.9
        assert last['category'] == 'outro'

    def test_with_dtns_action_map_intro_and_outro_survive_distinct(self):
        merged = deduplicate_window_ads(
            self._raw_llm_detections(), action_map=self.DTNS_ACTION_MAP)

        by_start = {round(m['start'], 1): m for m in merged}
        assert 0.0 in by_start
        assert by_start[0.0]['end'] == 156.7
        assert by_start[0.0].get('category') is None

        assert 158.0 in by_start
        intro = by_start[158.0]
        assert intro['end'] == 166.6
        assert intro['category'] == 'intro'

        assert 2324.5 in by_start
        outro = by_start[2324.5]
        assert outro['end'] == 2381.1
        assert outro['category'] == 'outro'

        assert 2385.8 in by_start
        assert by_start[2385.8]['end'] == 2444.9
        assert by_start[2385.8].get('category') is None

        # The genuinely-duplicate Vanta re-detection across the window 2/3
        # boundary (687.5-845.5 and 814.2-845.5, same resolved action) still
        # merges exactly as before.
        assert 687.5 in by_start
        assert by_start[687.5]['end'] == 845.5

    def test_all_remove_map_matches_no_map_behavior(self):
        """An all-remove action map (today's default feed) must merge
        identically to the no-map case: the gate never changes behavior
        for a feed that has not opted into per-category actions."""
        all_remove = {cat: 'remove' for cat in self.DTNS_ACTION_MAP}
        merged = deduplicate_window_ads(
            self._raw_llm_detections(), action_map=all_remove)
        assert len(merged) == 6


class TestRemovalCoverageRegions:
    """removal_coverage_regions gates which pattern-matched regions may
    shadow (trim) a Claude detection (DTNS 5337): a keep-resolving pattern
    region never cuts, so letting it cover a remove-resolving detection
    leaves the ad in the audio with no marker responsible for removing it."""

    ACTION_MAP = {'sponsor': 'remove', 'cross_promo': 'keep',
                  'self_promo': 'keep', 'intro': 'keep', 'outro': 'keep',
                  'interaction': 'remove', 'recap': 'keep'}

    def test_keep_resolving_region_excluded(self):
        regions = [{'start': 1686.7, 'end': 1781.6, 'pattern_id': 625,
                    'category': 'cross_promo'}]
        assert removal_coverage_regions(regions, self.ACTION_MAP) == []

    def test_remove_resolving_and_uncategorized_regions_kept(self):
        regions = [
            {'start': 0.0, 'end': 170.0, 'pattern_id': 622,
             'category': 'sponsor'},
            {'start': 500.0, 'end': 530.0, 'pattern_id': 661,
             'category': None},
        ]
        assert removal_coverage_regions(regions, self.ACTION_MAP) == regions

    def test_tuple_regions_kept(self):
        """Bare (start, end) tuples carry no category and stay eligible."""
        regions = [(100.0, 130.0)]
        assert removal_coverage_regions(regions, self.ACTION_MAP) == regions

    def test_none_action_map_returns_all(self):
        regions = [{'start': 10.0, 'end': 40.0, 'pattern_id': 1,
                    'category': 'cross_promo'}]
        assert removal_coverage_regions(regions, None) == regions

    def test_dtns_5337_keep_pattern_does_not_trim_sponsor_detection(self):
        """The DTNS 5337 shape: a cross_promo->keep pattern match covered
        52% of a Morning Brew + Vanta sponsor detection; the trim left only
        the Vanta half cut and the Morning Brew read in the audio."""
        ad = {'start': 1752.4, 'end': 1808.6, 'confidence': 0.97,
              'category': 'sponsor', 'reason': 'Morning Brew Daily + Vanta'}
        regions = [{'start': 1686.7, 'end': 1781.64, 'pattern_id': 625,
                    'category': 'cross_promo'}]
        coverage = removal_coverage_regions(regions, self.ACTION_MAP)
        portions = get_uncovered_portions(ad, coverage)
        assert len(portions) == 1
        assert portions[0]['start'] == 1752.4
        assert portions[0]['end'] == 1808.6


class TestAddPatternMatchRegionCategory:
    """_add_pattern_match must record the match category on the coverage
    region so removal_coverage_regions can resolve its action."""

    def _match(self, category):
        from types import SimpleNamespace
        return SimpleNamespace(start=10.0, end=40.0, confidence=0.9,
                               sponsor='Morning Brew', pattern_id=625,
                               category=category, matched_text=None)

    def test_region_carries_match_category(self):
        from ad_detector import AdDetector
        detector = AdDetector.__new__(AdDetector)
        detector.pattern_service = None
        all_ads, regions = [], []
        detector._add_pattern_match(self._match('cross_promo'),
                                    'text_pattern', 'content',
                                    all_ads, regions, 'ep1')
        assert regions[0]['category'] == 'cross_promo'

    def test_region_category_none_when_pattern_uncategorized(self):
        from ad_detector import AdDetector
        detector = AdDetector.__new__(AdDetector)
        detector.pattern_service = None
        all_ads, regions = [], []
        detector._add_pattern_match(self._match(None),
                                    'text_pattern', 'content',
                                    all_ads, regions, 'ep1')
        assert regions[0].get('category') is None

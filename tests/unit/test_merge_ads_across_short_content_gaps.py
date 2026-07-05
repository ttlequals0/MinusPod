"""Unit tests for merge_ads_across_short_content_gaps."""
import sys
import os
import tempfile
from unittest.mock import patch, MagicMock

# Must be set before importing main_app.processing (storage init reads it).
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='merge_gap_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import merge_ads_across_short_content_gaps
import main_app.processing as _proc  # noqa: import at top so env vars are set


# Helpers

def _ad(start, end, confidence=0.85, sponsor=None, reason='Ad detected'):
    ad = {'start': start, 'end': end, 'confidence': confidence, 'reason': reason}
    if sponsor is not None:
        ad['sponsor'] = sponsor
    return ad


def _seg(start, end, text='Show content speech here.'):
    return {'start': start, 'end': end, 'text': text}


# --- core merge cases ---

class TestMergeAcrossShortContentGaps:

    def test_filler_gap_merges_two_ads(self):
        """Two ads with no speech in the gap (music/silence) should merge."""
        ads = [_ad(10, 40), _ad(50, 80)]
        # gap 40-50: no segments -> 0s of content -> below threshold
        segments = [_seg(0, 10, 'Intro.'), _seg(80, 120, 'Outro.')]
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0]['start'] == 10
        assert result[0]['end'] == 80

    def test_filler_gap_sets_merged_distinct_ads_flag(self):
        """merged_distinct_ads must be True after a filler-gap merge."""
        ads = [_ad(10, 40), _ad(50, 80)]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0].get('merged_distinct_ads') is True

    def test_filler_gap_appends_reason(self):
        """Merged ad reason string must reference the merge."""
        ads = [_ad(10, 40, reason='BetterHelp ad'), _ad(50, 80, reason='NordVPN ad')]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert 'NordVPN' in result[0]['reason'] or 'merged' in result[0]['reason'].lower()

    def test_real_content_in_gap_prevents_merge(self):
        """Over-merge guard: >= threshold seconds of speech must block merge."""
        ads = [_ad(10, 40), _ad(100, 130)]
        # gap 40-100: 30s segment with speech text -> 30s content -> above 12s threshold
        segments = [_seg(40, 70, 'Today we discuss the state of the economy and what it means.')]
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 2, "Ads separated by real content must not merge"

    def test_partial_content_below_threshold_merges(self):
        """A short speech segment (< threshold) in a mostly-filler gap should still merge."""
        ads = [_ad(10, 40), _ad(55, 80)]
        # gap 40-55 = 15s total; segment covers 40-44 (4s text) -> 4s content < 12s threshold
        segments = [_seg(40, 44, 'Back soon.')]
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1

    def test_three_ads_one_break_merge_to_single_span(self):
        """apparle's case: 3 ads in one break with filler gaps -> single contiguous cut."""
        ads = [_ad(10, 40), _ad(50, 80), _ad(90, 120)]
        # gaps 40-50 and 80-90: no speech segments
        segments = [_seg(0, 10, 'Intro.'), _seg(120, 200, 'Show content.')]
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0]['start'] == 10
        assert result[0]['end'] == 120

    def test_different_sponsors_filler_gap_merges(self):
        """Cross-sponsor merge is the intended use case; must succeed."""
        ads = [_ad(10, 40, sponsor='BetterHelp'), _ad(50, 80, sponsor='NordVPN')]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1

    def test_max_merged_duration_blocks_merge(self):
        """When merged span would exceed max_merged_seconds, skip merge, keep both."""
        ads = [_ad(0, 200), _ad(205, 400)]
        # merged span = 400s > max 300s
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 2, "Oversized merge must be skipped"

    def test_confidence_is_max_of_two(self):
        """Merged ad confidence must be the max of the two source ads."""
        ads = [_ad(10, 40, confidence=0.70), _ad(50, 80, confidence=0.92)]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0]['confidence'] == 0.92

    def test_sponsor_none_does_not_overwrite_real_sponsor(self):
        """Regression: merging a None-sponsor ad must not null-overwrite a real sponsor."""
        ads = [_ad(10, 40, sponsor='BetterHelp'), _ad(50, 80, sponsor=None)]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0].get('sponsor') == 'BetterHelp'

    def test_sponsor_none_first_then_real_sponsor(self):
        """None sponsor in first ad must not overwrite real sponsor from second."""
        ads = [_ad(10, 40, sponsor=None), _ad(50, 80, sponsor='NordVPN')]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0].get('sponsor') == 'NordVPN'

    def test_single_ad_unchanged(self):
        """Single ad returns unchanged."""
        ads = [_ad(10, 40)]
        result = merge_ads_across_short_content_gaps(ads, [], min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0]['start'] == 10

    def test_empty_ads_unchanged(self):
        """Empty list returns empty."""
        result = merge_ads_across_short_content_gaps([], [], min_content_seconds=12.0, max_merged_seconds=300.0)
        assert result == []

    def test_output_sorted_by_start(self):
        """Output must be sorted by start time even when input is unsorted."""
        ads = [_ad(50, 80), _ad(10, 40)]
        segments = [_seg(40, 60, 'Lots of show content here, keep them separate please.')]
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        starts = [r['start'] for r in result]
        assert starts == sorted(starts)

    def test_end_text_taken_from_later_ad(self):
        """After merge, end_text should come from the later (merged) ad."""
        ads = [
            {**_ad(10, 40), 'end_text': 'Use code FIRST'},
            {**_ad(50, 80), 'end_text': 'Use code SECOND'},
        ]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 1
        assert result[0].get('end_text') == 'Use code SECOND'

    def test_zero_min_content_disables_pass(self):
        """min_content_seconds <= 0 disables the pass: nothing merges."""
        ads = [_ad(10, 40), _ad(50, 80)]
        segments = [_seg(0, 5, 'Intro.')]  # filler gap that WOULD merge if enabled
        result = merge_ads_across_short_content_gaps(ads, segments, min_content_seconds=0.0, max_merged_seconds=300.0)
        assert len(result) == 2, "0 must disable merging, not merge unconditionally"

    def test_no_segments_never_merges(self):
        """Without a transcript there is no content evidence: never merge.

        Guards the catastrophic path where empty segments make every gap
        measure 0 content and everything within the cap over-merges."""
        ads = [_ad(10, 40), _ad(50, 80)]
        result = merge_ads_across_short_content_gaps(ads, [], min_content_seconds=12.0, max_merged_seconds=300.0)
        assert len(result) == 2


# --- plumbing: setting reaches the pass from _refine_boundaries ---

class TestMergeGapSettingPlumbing:

    def test_setting_passed_through_refine_boundaries(self):
        """_refine_boundaries must call merge_ads_across_short_content_gaps
        with the value read from the DB setting."""
        ads_in = [_ad(10, 40), _ad(50, 80)]
        segments = [_seg(0, 5, 'Intro.')]  # out-of-gap; gap itself untranscribed

        mock_db = MagicMock()
        # _setting_float calls db.get_setting(key) -> str or None
        mock_db.get_setting = MagicMock(return_value='15.0')

        captured = {}
        original = _proc.merge_ads_across_short_content_gaps

        def spy(ads, segs, min_content_seconds, max_merged_seconds=300.0):
            captured['min_content_seconds'] = min_content_seconds
            return original(ads, segs, min_content_seconds, max_merged_seconds)

        with patch.object(_proc, 'merge_ads_across_short_content_gaps', spy):
            _proc._refine_boundaries(ads_in, segments, db=mock_db)

        assert 'min_content_seconds' in captured, "_refine_boundaries did not call merge_ads_across_short_content_gaps"
        assert captured['min_content_seconds'] == 15.0

    def test_zero_setting_disables_end_to_end(self):
        """Stored '0.0' must reach the pass as 0.0 (not the 12.0 default) and
        disable merging. Guards the _setting_float parsed>0 fallback trap."""
        ads_in = [_ad(10, 40), _ad(50, 80)]
        segments = [_seg(0, 5, 'Intro.')]  # filler gap that WOULD merge if enabled

        mock_db = MagicMock()
        mock_db.get_setting = MagicMock(return_value='0.0')

        captured = {}
        original = _proc.merge_ads_across_short_content_gaps

        def spy(ads, segs, min_content_seconds, max_merged_seconds=300.0):
            captured['min_content_seconds'] = min_content_seconds
            return original(ads, segs, min_content_seconds, max_merged_seconds)

        with patch.object(_proc, 'merge_ads_across_short_content_gaps', spy):
            result = _proc._refine_boundaries(ads_in, segments, db=mock_db)

        assert captured['min_content_seconds'] == 0.0
        assert len(result) == 2, "Setting 0 must disable merging end-to-end"

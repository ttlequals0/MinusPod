"""Sponsor label hygiene: sanitize_sponsor_label and duplicate-overlap merge.

Real-world case (Windows Weekly baf427c1693c): Claude labeled an ad's
sponsor 'Xbox segment' (the segment name) and emitted overlapping markers
for ONE ad read; other holds carried reasoning prose as sponsors.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector
from utils.constants import sanitize_sponsor_label


def _ad(start, end, confidence=0.8, sponsor=None, reason='', held=False):
    ad = {'start': start, 'end': end, 'confidence': confidence,
          'sponsor': sponsor, 'reason': reason}
    if held:
        ad['held_for_review'] = True
        ad['was_cut'] = False
    return ad


class TestSanitizeSponsorLabel:

    def test_segment_name_is_rejected(self):
        assert sanitize_sponsor_label('Xbox segment') is None

    def test_reasoning_prose_is_rejected(self):
        assert sanitize_sponsor_label(
            'Regular discussion about Whiteboard app resumes') is None

    def test_real_sponsor_unchanged(self):
        assert sanitize_sponsor_label('Capital One') == 'Capital One'

    def test_falsy_input_is_none(self):
        assert sanitize_sponsor_label(None) is None
        assert sanitize_sponsor_label('') is None

    def test_segment_match_is_case_insensitive_and_trims(self):
        assert sanitize_sponsor_label('  Weather Segment  ') is None
        assert sanitize_sponsor_label('News SEGMENT') is None

    def test_word_containing_segment_is_not_rejected(self):
        # \bsegment$ must not match a sponsor name that merely contains
        # "segment" as part of a longer word.
        assert sanitize_sponsor_label('Segmentify') == 'Segmentify'


class TestMergeDetectionResultsSanitizesSponsor:

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_windows_weekly_shape_merges_with_clean_sponsor(self):
        det = self._det()
        ads = [
            _ad(3963.3, 4131.6, confidence=0.8, sponsor='Xbox segment'),
            _ad(3963.6, 4093.3, confidence=0.9, sponsor='CiraSync'),
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 1
        assert out[0]['start'] == 3963.3
        assert out[0]['end'] == 4131.6
        assert out[0]['sponsor'] == 'CiraSync'

    def test_standalone_segment_sponsor_sanitized_to_none(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, sponsor='Xbox segment')])
        assert len(out) == 1
        assert out[0]['sponsor'] is None

    def test_standalone_real_sponsor_passes_through(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, sponsor='Capital One')])
        assert len(out) == 1
        assert out[0]['sponsor'] == 'Capital One'


class TestMergeOverlappingAcceptedDuplicates:

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_forty_percent_overlap_does_not_merge(self):
        det = self._det()
        # A=[0,100] dur 100, B=[80,130] dur 50: overlap 20 / shorter 50 = 0.4
        ads = [
            _ad(0.0, 100.0, confidence=0.8, sponsor='FooCo'),
            _ad(80.0, 130.0, confidence=0.9, sponsor='BarCo'),
        ]
        out = det._merge_overlapping_accepted_duplicates(ads)
        assert len(out) == 2

    def test_eighty_percent_overlap_merges_to_higher_confidence_sponsor(self):
        det = self._det()
        # A=[0,100] dur 100, B=[10,100] dur 90: overlap 90 / shorter 90 = 1.0
        ads = [
            _ad(0.0, 100.0, confidence=0.7, sponsor='FooCo'),
            _ad(10.0, 100.0, confidence=0.9, sponsor='BarCo'),
        ]
        out = det._merge_overlapping_accepted_duplicates(ads)
        assert len(out) == 1
        assert out[0]['start'] == 0.0
        assert out[0]['end'] == 100.0
        assert out[0]['confidence'] == 0.9
        assert out[0]['sponsor'] == 'BarCo'

    def test_junk_primary_sponsor_falls_back_to_other(self):
        det = self._det()
        ads = [
            _ad(0.0, 100.0, confidence=0.9, sponsor='Xbox segment'),
            _ad(5.0, 95.0, confidence=0.6, sponsor='CiraSync'),
        ]
        out = det._merge_overlapping_accepted_duplicates(ads)
        assert len(out) == 1
        assert out[0]['sponsor'] == 'CiraSync'

    def test_held_marker_never_merges_with_accepted(self):
        det = self._det()
        ads = [
            _ad(0.0, 100.0, confidence=0.9, sponsor='FooCo', held=True),
            _ad(5.0, 95.0, confidence=0.9, sponsor='FooCo'),
        ]
        out = det._merge_overlapping_accepted_duplicates(ads)
        assert len(out) == 2

    def test_held_markers_never_merge_with_each_other(self):
        det = self._det()
        ads = [
            _ad(0.0, 100.0, confidence=0.9, sponsor='FooCo', held=True),
            _ad(5.0, 95.0, confidence=0.9, sponsor='FooCo', held=True),
        ]
        out = det._merge_overlapping_accepted_duplicates(ads)
        assert len(out) == 2

    def test_single_marker_unchanged(self):
        det = self._det()
        out = det._merge_overlapping_accepted_duplicates([_ad(0.0, 100.0)])
        assert len(out) == 1

    def test_empty_list_unchanged(self):
        det = self._det()
        assert det._merge_overlapping_accepted_duplicates([]) == []

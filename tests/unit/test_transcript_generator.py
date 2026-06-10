"""Unit tests for TranscriptGenerator segment filtering."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from transcript_generator import TranscriptGenerator


def _seg(start, end, text='words'):
    return {'start': start, 'end': end, 'text': text}


class TestIsSegmentInAd:
    def setup_method(self):
        self.gen = TranscriptGenerator()

    def test_no_ads_keeps_segment(self):
        assert self.gen.is_segment_in_ad(_seg(10.0, 20.0), []) is False

    def test_segment_entirely_in_ad_is_dropped(self):
        ads = [{'start': 5.0, 'end': 25.0}]
        assert self.gen.is_segment_in_ad(_seg(10.0, 20.0), ads) is True

    def test_segment_split_across_two_adjacent_cuts_is_dropped(self):
        # The Grainger case: one segment straddles a pass-1 cut end and a
        # pass-2 re-cut start. Neither cut alone covers >80%, but together
        # they cover ~95%, so the segment (and its ad text) must be dropped.
        seg = _seg(2034.6, 2061.4)            # 26.8s
        ads = [
            {'start': 1969.0, 'end': 2046.0},  # pass-1 cut, overlaps 11.4s (43%)
            {'start': 2047.5, 'end': 2075.0},  # pass-2 recut, overlaps 13.9s (52%)
        ]
        assert self.gen.is_segment_in_ad(seg, ads) is True

    def test_low_total_overlap_keeps_segment(self):
        # Only ~30% of the segment overlaps a cut -> kept.
        seg = _seg(100.0, 120.0)              # 20s
        ads = [{'start': 114.0, 'end': 130.0}]  # overlaps 6s (30%)
        assert self.gen.is_segment_in_ad(seg, ads) is False

    def test_overlapping_ads_not_double_counted(self):
        # Two cuts over the same first half: naive summation would be 50 + 40 =
        # 90% (> threshold, wrongly dropped); the union is 50% so it is kept.
        seg = _seg(0.0, 100.0)
        ads = [{'start': 0.0, 'end': 50.0}, {'start': 10.0, 'end': 50.0}]
        assert self.gen.is_segment_in_ad(seg, ads) is False


class TestComputeFinalSegments:
    def setup_method(self):
        self.gen = TranscriptGenerator()

    def test_straddling_ad_segment_removed_from_output(self):
        segments = [
            _seg(0.0, 30.0, 'show open'),
            _seg(2034.6, 2061.4, 'maintenance engineer Grainger'),
            _seg(2073.3, 2090.0, 'back to headlines'),
        ]
        ads = [
            {'start': 1969.0, 'end': 2046.0},
            {'start': 2047.5, 'end': 2075.0},
        ]
        out = self.gen.compute_final_segments(segments, ads)
        texts = [s['text'] for s in out]
        assert 'maintenance engineer Grainger' not in texts
        assert 'show open' in texts
        assert 'back to headlines' in texts

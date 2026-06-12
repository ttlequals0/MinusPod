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


def _words(spec):
    # spec: list of (start, end, word). Words carry leading spaces, matching
    # the transcriber's faster-whisper format.
    return [{'start': s, 'end': e, 'word': w} for s, e, w in spec]


class TestWordLevelTrim:
    def setup_method(self):
        self.gen = TranscriptGenerator()

    def test_tail_bleed_words_trimmed(self):
        # Cut starts mid-segment; trailing ad words are dropped, text rebuilt,
        # end tightened to the last kept word. Cut is after the kept range so
        # adjust_timestamp leaves the kept bounds unchanged.
        seg = {'start': 100.0, 'end': 120.0,
               'text': 'real content then sponsor pitch',
               'words': _words([
                   (100.0, 103.0, 'real'), (103.0, 106.0, ' content'),
                   (106.0, 109.0, ' then'), (114.5, 117.0, ' sponsor'),
                   (117.0, 120.0, ' pitch'),
               ])}
        ads = [{'start': 114.0, 'end': 130.0}]
        out = self.gen.compute_final_segments([seg], ads)
        assert len(out) == 1
        assert out[0]['text'] == 'real content then'
        assert out[0]['start'] == 100.0
        assert out[0]['end'] == 109.0

    def test_head_bleed_words_trimmed_and_shifted(self):
        # Cut covers the segment head; leading ad words drop, start tightens
        # to the first kept word, then the whole cut duration shifts it left.
        seg = {'start': 100.0, 'end': 120.0,
               'text': 'sponsor pitch then real content',
               'words': _words([
                   (100.0, 103.0, 'sponsor'), (103.0, 106.0, ' pitch'),
                   (106.5, 109.0, ' then'), (109.0, 114.0, ' real'),
                   (114.0, 120.0, ' content'),
               ])}
        ads = [{'start': 90.0, 'end': 106.0}]
        out = self.gen.compute_final_segments([seg], ads)
        assert len(out) == 1
        assert out[0]['text'] == 'then real content'
        # start = 106.5 tightened, minus the 16s cut before it
        assert abs(out[0]['start'] - 90.5) < 0.01
        assert abs(out[0]['end'] - 104.0) < 0.01

    def test_no_overlap_keeps_text_byte_for_byte(self):
        seg = {'start': 100.0, 'end': 120.0,
               'text': '  spacing preserved exactly ',
               'words': _words([(100.0, 110.0, 'spacing'),
                                (110.0, 120.0, ' preserved')])}
        ads = [{'start': 300.0, 'end': 330.0}]
        out = self.gen.compute_final_segments([seg], ads)
        # No overlap -> no trim path -> only the pre-existing strip applies.
        assert out[0]['text'] == 'spacing preserved exactly'

    def test_partial_overlap_without_words_falls_back(self):
        # No word timing available: current behavior (keep whole text).
        seg = {'start': 100.0, 'end': 120.0, 'text': 'mixed line'}
        ads = [{'start': 114.0, 'end': 130.0}]
        out = self.gen.compute_final_segments([seg], ads)
        assert out[0]['text'] == 'mixed line'

    def test_words_missing_timestamps_fall_back(self):
        seg = {'start': 100.0, 'end': 120.0, 'text': 'mixed line',
               'words': [{'word': 'mixed'}, {'word': ' line'}]}
        ads = [{'start': 114.0, 'end': 130.0}]
        out = self.gen.compute_final_segments([seg], ads)
        assert out[0]['text'] == 'mixed line'

    def test_words_with_none_timestamps_fall_back(self):
        # The API transcription path can carry JSON nulls through as None.
        seg = {'start': 100.0, 'end': 120.0, 'text': 'mixed line',
               'words': [{'word': 'mixed', 'start': None, 'end': None},
                         {'word': ' line', 'start': 110.0, 'end': 120.0}]}
        ads = [{'start': 114.0, 'end': 130.0}]
        out = self.gen.compute_final_segments([seg], ads)
        assert out[0]['text'] == 'mixed line'

    def test_all_words_in_cuts_drops_segment(self):
        # Union coverage is under the 80% drop threshold, but every word
        # midpoint falls inside a cut -> nothing survives the trim.
        seg = {'start': 100.0, 'end': 120.0, 'text': 'sponsor words only',
               'words': _words([(104.0, 106.0, 'sponsor'),
                                (106.0, 108.0, ' words'),
                                (108.0, 110.0, ' only')])}
        ads = [{'start': 103.0, 'end': 111.0}]  # 40% of the segment
        out = self.gen.compute_final_segments([seg], ads)
        assert out == []


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

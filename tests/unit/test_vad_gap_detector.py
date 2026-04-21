"""Tests for src/vad_gap_detector.py."""
import pytest

from vad_gap_detector import detect_vad_gaps


def _seg(start, end, text=''):
    return {'start': start, 'end': end, 'text': text}


class TestHeadGap:
    def test_head_gap_above_threshold_emits_marker(self):
        segments = [_seg(10.95, 38.75, 'This is the Daily Tech News...')]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=2522.0)
        head = [g for g in gaps if g['start'] == 0.0]
        assert len(head) == 1
        assert head[0]['end'] == pytest.approx(10.95)
        assert head[0]['detection_stage'] == 'vad_gap'
        assert head[0]['confidence'] > 0

    def test_head_gap_below_threshold_skipped(self):
        segments = [_seg(1.5, 30.0, 'Opening text')]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=60.0,
                               start_min_seconds=3.0)
        assert not any(g['start'] == 0.0 for g in gaps)

    def test_head_gap_already_covered_skipped(self):
        segments = [_seg(10.95, 38.75)]
        existing = [{'start': 0.0, 'end': 110.0, 'reason': 'Grainger'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=200.0)
        assert not any(g['start'] == 0.0 and g['end'] == 10.95 for g in gaps)

    def test_configurable_head_threshold(self):
        segments = [_seg(4.0, 30.0)]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=60.0,
                               start_min_seconds=5.0)
        assert not any(g['start'] == 0.0 for g in gaps)
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=60.0,
                               start_min_seconds=3.0)
        assert any(g['start'] == 0.0 for g in gaps)


class TestMidGap:
    def test_adjacent_gap_extends_existing_ad_no_new_marker(self):
        segments = [_seg(0.0, 50.0, 'show'), _seg(70.0, 100.0, 'show')]
        existing = [{'start': 45.0, 'end': 50.0, 'reason': 'Sponsor'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=100.0,
                               mid_min_seconds=10.0)
        mid_markers = [g for g in gaps if g['start'] == 50.0 and g['end'] == 70.0]
        assert mid_markers == []
        assert existing[0]['end'] == pytest.approx(70.0)
        assert existing[0].get('vad_gap_extended') is True

    def test_mid_gap_with_signoff_and_resume_emits(self):
        segments = [
            _seg(0.0, 60.0, 'Visit example.com slash code for a free trial today.'),
            _seg(75.0, 120.0, "Welcome back everyone, let's continue."),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=200.0,
                               mid_min_seconds=10.0)
        mid = [g for g in gaps if g['start'] == 60.0 and g['end'] == 75.0]
        assert len(mid) == 1

    def test_mid_gap_neutral_context_skipped(self):
        segments = [
            _seg(0.0, 60.0, 'Then our guest shared their thoughts on the topic.'),
            _seg(75.0, 120.0, 'That was a great point about the industry trend.'),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=200.0,
                               mid_min_seconds=10.0)
        assert not any(g['start'] == 60.0 for g in gaps)

    def test_mid_gap_below_threshold_skipped(self):
        segments = [
            _seg(0.0, 60.0, 'Visit example.com slash code.'),
            _seg(62.0, 120.0, 'Welcome back.'),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=200.0,
                               mid_min_seconds=10.0)
        assert not any(g['start'] == 60.0 for g in gaps)


class TestTailGap:
    def test_tail_gap_above_threshold_emits_marker(self):
        segments = [_seg(0.0, 100.0, 'show')]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=115.0,
                               tail_min_seconds=3.0)
        tail = [g for g in gaps if g['end'] == 115.0]
        assert len(tail) == 1
        assert tail[0]['start'] == pytest.approx(100.0)

    def test_tail_gap_below_threshold_skipped(self):
        segments = [_seg(0.0, 100.0)]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=101.5,
                               tail_min_seconds=3.0)
        assert not any(g['end'] == 101.5 for g in gaps)

    def test_tail_gap_covered_by_postroll_skipped(self):
        segments = [_seg(0.0, 100.0)]
        existing = [{'start': 95.0, 'end': 115.0, 'reason': 'Postroll'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=115.0)
        assert not any(g['end'] == 115.0 and g['start'] == 100.0 for g in gaps)


class TestEmpty:
    def test_empty_segments_returns_empty(self):
        assert detect_vad_gaps([], existing_ads=[], episode_duration=100.0) == []


class TestDTNSRegression:
    """Reproduce the DTNS episode that motivated this feature.

    Original episode: daily-tech-news-show/18fff54d3363. Whisper's transcript
    starts at 10.95s (sped-up DIA legal tail is VAD-dropped). Even if no
    detected ad anchors the head, we should emit a head-gap marker.
    """
    def test_head_gap_emitted_without_existing_ads_anchor(self):
        segments = [_seg(10.95, 38.75, 'This is the Daily Tech News for Tuesday')]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=2522.0)
        assert any(g['start'] == 0.0 and g['end'] == pytest.approx(10.95) for g in gaps)

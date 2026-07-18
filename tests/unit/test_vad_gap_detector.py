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

    def test_mid_gap_signoff_only_does_not_emit(self):
        # Signoff phrase before the gap, but neutral chatter after. Pre-fix
        # this emitted; post-fix both sides must show context.
        segments = [
            _seg(0.0, 60.0, 'Thanks for tuning in to the show today.'),
            _seg(75.0, 120.0, 'So Apple has been making moves recently.'),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=200.0,
                               mid_min_seconds=10.0)
        assert not any(g['start'] == 60.0 and g['end'] == 75.0 for g in gaps)

    def test_mid_gap_resume_only_does_not_emit(self):
        # Resume phrase after the gap, but neutral chatter before.
        segments = [
            _seg(0.0, 60.0, 'And then the team continued building out the project.'),
            _seg(75.0, 120.0, "Welcome back everyone, let's continue."),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=200.0,
                               mid_min_seconds=10.0)
        assert not any(g['start'] == 60.0 and g['end'] == 75.0 for g in gaps)


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


class TestDAISeamRegression:
    """the-brilliant-idiots/79eedd7bf2a7: DAI insertion duplicated a few
    seconds of show audio around the splice. The line appeared once inside
    the ad span near the seam and verbatim again at 87.8s where the show
    resumed. The mid-gap merge extended the ad end 76.0s -> 87.8s, swallowing
    ~12s of show. A verbatim duplicate beyond the proposed boundary must now
    block the extension.
    """
    _DUP_LINE = "I'm experiencing a weird, it's not even weird, I don't feel the FOMO."

    def _incident_segments(self, resume_text):
        return [
            _seg(0.0, 66.0, 'Show opening with the hosts catching up on the week.'),
            _seg(66.0, 71.0, 'This episode is brought to you by our sponsor, use code IDIOTS.'),
            _seg(71.0, 76.0, self._DUP_LINE),
            _seg(87.8, 95.0, resume_text),
        ]

    def test_duplicate_beyond_end_boundary_blocks_extension(self):
        segments = self._incident_segments(self._DUP_LINE)
        existing = [{'start': 66.0, 'end': 76.0, 'reason': 'Preroll sponsor'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=95.0,
                               mid_min_seconds=8.0)
        assert existing[0]['end'] == pytest.approx(76.0)
        assert 'vad_gap_extended' not in existing[0]
        assert not any(g['start'] == 76.0 for g in gaps)

    def test_no_duplicate_control_extends_as_before(self):
        segments = self._incident_segments(
            'Alright so back to the story we were telling before the break.')
        existing = [{'start': 66.0, 'end': 76.0, 'reason': 'Preroll sponsor'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=95.0,
                               mid_min_seconds=8.0)
        assert existing[0]['end'] == pytest.approx(87.8)
        assert existing[0].get('vad_gap_extended') is True
        assert not any(g['start'] == 76.0 for g in gaps)

    def test_short_duplicate_below_threshold_still_extends(self):
        # "welcome back" repeats on both sides but normalizes to 12 chars,
        # under the 50-char threshold, so the extension proceeds.
        segments = [
            _seg(0.0, 66.0, 'Show opening with the hosts catching up on the week.'),
            _seg(66.0, 76.0, 'Use code IDIOTS at checkout and welcome back savings await.'),
            _seg(87.8, 95.0, 'Welcome back. Today we are covering something entirely new.'),
        ]
        existing = [{'start': 66.0, 'end': 76.0, 'reason': 'Preroll sponsor'}]
        detect_vad_gaps(segments, existing_ads=existing, episode_duration=95.0,
                        mid_min_seconds=8.0)
        assert existing[0]['end'] == pytest.approx(87.8)
        assert existing[0].get('vad_gap_extended') is True

    def test_stock_cta_shared_across_gap_still_extends(self):
        # Two back-to-back sponsor reads share the stock CTA "this episode is
        # brought to you by" (~34 normalized chars) across the gap. That is
        # common ad boilerplate, not duplicated show audio, so the longest
        # verbatim run stays under the 50-char seam threshold and the
        # extension proceeds -- otherwise the gap of ad residue stays uncut.
        segments = [
            _seg(0.0, 66.0, 'Show opening with the hosts catching up on the week.'),
            _seg(66.0, 76.0, 'This episode is brought to you by Acme, use code ONE.'),
            _seg(87.8, 95.0, 'This episode is brought to you by Globex, use code TWO.'),
        ]
        existing = [{'start': 66.0, 'end': 76.0, 'reason': 'Preroll sponsor'}]
        detect_vad_gaps(segments, existing_ads=existing, episode_duration=95.0,
                        mid_min_seconds=8.0)
        assert existing[0]['end'] == pytest.approx(87.8)
        assert existing[0].get('vad_gap_extended') is True

    def test_duplicate_beyond_start_boundary_blocks_extension(self):
        dup = 'and that is why the entire premise of the argument falls apart'
        segments = [
            _seg(0.0, 100.0, f'The hosts keep debating, {dup}.'),
            _seg(112.0, 120.0, f'{dup}, but our sponsor disagrees, use code SHOW.'),
            _seg(120.0, 160.0, 'More midroll sponsor copy continues here.'),
        ]
        existing = [{'start': 112.0, 'end': 140.0, 'reason': 'Midroll sponsor'}]
        gaps = detect_vad_gaps(segments, existing_ads=existing, episode_duration=160.0,
                               mid_min_seconds=8.0)
        assert existing[0]['start'] == pytest.approx(112.0)
        assert 'vad_gap_extended' not in existing[0]
        assert not any(g['start'] == 100.0 for g in gaps)

    def test_start_side_control_extends_as_before(self):
        segments = [
            _seg(0.0, 100.0, 'The hosts keep debating the finer points of the story.'),
            _seg(112.0, 120.0, 'This midroll is brought to you by our sponsor, use code SHOW.'),
            _seg(120.0, 160.0, 'More midroll sponsor copy continues here.'),
        ]
        existing = [{'start': 112.0, 'end': 140.0, 'reason': 'Midroll sponsor'}]
        detect_vad_gaps(segments, existing_ads=existing, episode_duration=160.0,
                        mid_min_seconds=8.0)
        assert existing[0]['start'] == pytest.approx(100.0)
        assert existing[0].get('vad_gap_extended') is True


class TestMBW1021Regression:
    """MacBreak Weekly 1021 (5ef2df166c8e) emitted 8 mid-gap markers where
    one side had a signoff/resume-like phrase but the other was neutral
    podcast chatter. Each cut 9-44s of legitimate content. Both sides must
    now show context to emit a mid-gap marker.
    """
    def test_one_sided_signoff_no_resume_skipped(self):
        segments = [
            _seg(2070.0, 2081.8, 'See you next week, take care everyone.'),
            _seg(2097.6, 2150.0, 'So Apple has been making moves recently.'),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=8359.0,
                               mid_min_seconds=8.0)
        assert not any(
            g.get('detection_stage') == 'vad_gap' and g['start'] == 2081.8
            for g in gaps
        )

    def test_one_sided_resume_no_signoff_skipped(self):
        segments = [
            _seg(2070.0, 2081.8, 'And the new chip improves performance significantly.'),
            _seg(2097.6, 2150.0, "Welcome back to MacBreak Weekly, let's continue."),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=8359.0,
                               mid_min_seconds=8.0)
        assert not any(
            g.get('detection_stage') == 'vad_gap' and g['start'] == 2081.8
            for g in gaps
        )

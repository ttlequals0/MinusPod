"""Tests for src/vad_gap_detector.py."""
import pytest

from config import HOLD_REASON_LARGE_VAD_GAP
from vad_gap_detector import detect_vad_gaps, show_cue_spans


def _seg(start, end, text=''):
    return {'start': start, 'end': end, 'text': text}


class TestHeadGap:
    def test_head_gap_above_threshold_emits_marker(self):
        segments = [_seg(10.95, 38.75, 'This is the Daily Tech Show...')]
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

    def test_large_adjacent_gap_is_held_separately(self):
        segments = [
            _seg(0.0, 50.0, 'The hosts continue their discussion.'),
            _seg(170.0, 200.0, 'They move on to the next topic.'),
        ]
        existing = [{'start': 40.0, 'end': 50.0, 'reason': 'Sponsor'}]

        gaps = detect_vad_gaps(
            segments, existing_ads=existing, episode_duration=200.0,
            mid_min_seconds=10.0,
        )

        assert existing[0]['end'] == pytest.approx(50.0)
        assert 'vad_gap_extended' not in existing[0]
        assert len(gaps) == 1
        assert gaps[0]['start'] == pytest.approx(50.0)
        assert gaps[0]['end'] == pytest.approx(170.0)
        assert gaps[0]['vad_gap_requires_review'] is True
        assert gaps[0]['held_for_review'] is True
        assert gaps[0]['hold_reason'] == HOLD_REASON_LARGE_VAD_GAP

    def test_fragmented_gaps_cannot_bypass_cumulative_extension_limit(self):
        segments = [
            _seg(0.0, 50.0, 'The hosts continue their discussion.'),
            _seg(105.0, 106.0, 'A brief show interjection.'),
            _seg(161.0, 200.0, 'They move on to the next topic.'),
        ]
        existing = [{'start': 40.0, 'end': 50.0, 'reason': 'Sponsor'}]

        gaps = detect_vad_gaps(
            segments, existing_ads=existing, episode_duration=200.0,
            mid_min_seconds=10.0,
        )

        assert existing[0]['end'] == pytest.approx(105.0)
        assert existing[0]['vad_gap_adjacency_extension_seconds'] == pytest.approx(55.0)
        assert len(gaps) == 1
        assert gaps[0]['start'] == pytest.approx(106.0)
        assert gaps[0]['end'] == pytest.approx(161.0)
        assert gaps[0]['held_for_review'] is True
        assert gaps[0]['hold_reason'] == HOLD_REASON_LARGE_VAD_GAP

    def test_large_adjacent_gap_with_break_context_can_extend(self):
        segments = [
            _seg(0.0, 50.0, 'Visit example.com slash show for a free trial.'),
            _seg(170.0, 200.0, 'Welcome back everyone, let us continue.'),
        ]
        existing = [{'start': 40.0, 'end': 50.0, 'reason': 'Sponsor'}]

        gaps = detect_vad_gaps(
            segments, existing_ads=existing, episode_duration=200.0,
            mid_min_seconds=10.0,
        )

        assert gaps == []
        assert existing[0]['end'] == pytest.approx(170.0)
        assert existing[0]['vad_gap_extended'] is True


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


class TestHeadGapRegression:
    """A head-gap marker is emitted for an episode whose Whisper transcript starts at 10.95s.

    The sped-up legal tail is VAD-dropped; the marker must appear even when no detected ad anchors the head.
    """
    def test_head_gap_emitted_without_existing_ads_anchor(self):
        segments = [_seg(10.95, 38.75, 'This is the Daily Tech Show for Tuesday')]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=2522.0)
        assert any(g['start'] == 0.0 and g['end'] == pytest.approx(10.95) for g in gaps)


class TestDAISeamRegression:
    """DAI insertion duplicated a few seconds of show audio around the splice.

    The line appears once inside the ad span near the seam and verbatim again at 87.8s where the show
    resumed. The mid-gap merge extended the ad end 76.0s -> 87.8s, swallowing ~12s of show, so a verbatim
    duplicate beyond the proposed boundary must block the extension.
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
        # Two back-to-back sponsor reads share the stock CTA "this episode is brought to you by" (~34 normalized
        # chars). That is ad boilerplate, not duplicated show audio, so the longest verbatim run stays under the
        # 50-char seam threshold and the extension proceeds; otherwise the gap of ad residue stays uncut.
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


class TestOneSidedMidGapRegression:
    """Both sides of a mid-gap must show context to emit a marker.

    One side with a signoff/resume-like phrase and the other neutral chatter used to emit markers
    that cut 9-44s of content.
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
            _seg(2097.6, 2150.0, "Welcome back to The Daily Tech Show, let's continue."),
        ]
        gaps = detect_vad_gaps(segments, existing_ads=[], episode_duration=8359.0,
                               mid_min_seconds=8.0)
        assert not any(
            g.get('detection_stage') == 'vad_gap' and g['start'] == 2081.8
            for g in gaps
        )


def _cue(start, end, cue_type, source='template', confidence=0.95):
    return {'start': start, 'end': end, 'signal_type': 'audio_cue',
            'confidence': confidence,
            'details': {'source': source, 'cue_type': cue_type}}


class TestShowCueSpans:
    def test_collects_template_show_intro_and_outro_cues(self):
        analysis = {'signals': [
            _cue(0.4, 4.6, 'show_intro'),
            _cue(247.6, 255.9, 'show_outro'),
            _cue(120.0, 122.0, 'ad_break_boundary'),
            _cue(130.0, 132.0, 'content_transition'),
            _cue(250.0, 252.0, 'show_outro', source='spectral'),
            {'start': 249.0, 'end': 250.0, 'signal_type': 'volume_change',
             'confidence': 0.9, 'details': {}},
        ]}
        assert show_cue_spans(analysis) == {
            'show_intro': [(0.4, 4.6)],
            'show_outro': [(247.6, 255.9)],
        }

    def test_missing_analysis_yields_empty_spans(self):
        assert show_cue_spans(None) == {'show_intro': [], 'show_outro': []}
        assert show_cue_spans({}) == {'show_intro': [], 'show_outro': []}


class TestShowCueCategory:
    """A head/tail gap the feed's own show intro/outro cue covers is show audio, so the
    marker carries that category and the feed's segment action decides it, not Rule 4."""

    SEGMENTS = [_seg(5.1, 120.0, 'Good morning, here is the news.'),
                _seg(120.0, 246.6, 'That was all for today.')]

    def _tail(self, gaps):
        return [g for g in gaps if g['end'] == pytest.approx(256.1)]

    def _head(self, gaps):
        return [g for g in gaps if g['start'] == 0.0]

    def test_tail_gap_covered_by_outro_cue_is_outro(self):
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1,
                               show_cues={'show_outro': [(247.6, 255.9)]})
        tail = self._tail(gaps)
        assert len(tail) == 1
        assert tail[0]['start'] == pytest.approx(246.6)
        assert tail[0]['category'] == 'outro'
        assert tail[0]['detection_stage'] == 'vad_gap'

    def test_head_gap_covered_by_intro_cue_is_intro(self):
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1,
                               show_cues={'show_intro': [(0.3, 4.8)]})
        head = self._head(gaps)
        assert len(head) == 1
        assert head[0]['end'] == pytest.approx(5.1)
        assert head[0]['category'] == 'intro'

    def test_no_cue_leaves_gaps_uncategorised(self):
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1)
        assert len(gaps) == 2
        assert all('category' not in g for g in gaps)

    def test_outro_cue_outside_the_tail_gap_is_ignored(self):
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1,
                               show_cues={'show_outro': [(200.0, 205.0)],
                                          'show_intro': [(60.0, 64.0)]})
        assert all('category' not in g for g in gaps)

    def test_intro_cue_does_not_categorise_the_tail(self):
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1,
                               show_cues={'show_intro': [(247.6, 255.9)]})
        assert 'category' not in self._tail(gaps)[0]

    def test_audio_after_the_outro_stays_its_own_marker(self):
        # 20 s of untranscribed audio after the outro could be an inserted post-roll:
        # it keeps the plain tail marker (and Rule 4's hold); only the outro is show audio.
        segments = [_seg(5.1, 246.6, 'That was all for today.')]
        gaps = detect_vad_gaps(segments, [], 276.0,
                               show_cues={'show_outro': [(247.6, 255.9)]})
        tail = sorted((g for g in gaps if g['start'] >= 246.0), key=lambda g: g['start'])
        assert [(round(g['start'], 1), round(g['end'], 1)) for g in tail] == [
            (246.6, 255.9), (255.9, 276.0)]
        assert tail[0]['category'] == 'outro'
        assert 'category' not in tail[1]

    def test_audio_before_the_intro_stays_its_own_marker(self):
        segments = [_seg(30.0, 200.0, 'Welcome back to the show.')]
        gaps = detect_vad_gaps(segments, [], 260.0,
                               show_cues={'show_intro': [(20.0, 29.0)]})
        head = sorted((g for g in gaps if g['end'] <= 30.0), key=lambda g: g['start'])
        assert [(round(g['start'], 1), round(g['end'], 1)) for g in head] == [
            (0.0, 20.0), (20.0, 30.0)]
        assert 'category' not in head[0]
        assert head[1]['category'] == 'intro'

    def test_short_remainder_after_the_outro_is_absorbed(self):
        # A fade under tail_min_seconds after the cue is part of the outro.
        gaps = detect_vad_gaps(self.SEGMENTS, [], 256.1,
                               show_cues={'show_outro': [(247.6, 253.5)]})
        tail = [g for g in gaps if g['start'] >= 246.0]
        assert len(tail) == 1
        assert tail[0]['end'] == pytest.approx(256.1)
        assert tail[0]['category'] == 'outro'

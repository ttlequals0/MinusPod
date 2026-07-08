"""Splice-evidence consumers in AdValidator (spec 2.3a).

Depends on Task 2's _audio_corroboration_source and the
validate(..., audio_analysis=) wiring; adds the splice_evidence source.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision


def _event(t, end=None, etype='digital_silence'):
    end = end if end is not None else t + 1.0
    return {'time': t, 'end_time': end, 'type': etype, 'depth_dbfs': -90.0,
            'duration_s': end - t, 'loudness_step_lu': None,
            'centroid_step_hz': None, 'flatness_step': None}


def _analysis(events, status='calibrated'):
    return {'splice_evidence': {'version': 1, 'events': events,
                                'calibration': {'status': status}}}


class TestSpliceCorroboration:
    def test_event_within_3s_of_start_returns_splice_evidence(self):
        validator = AdValidator(episode_duration=3600.0)
        validator.validate(
            [{'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'reason': 'x'}],
            audio_analysis=_analysis([_event(97.5)]))
        assert validator._audio_corroboration_source(
            {'start': 100.0, 'end': 130.0}) == 'splice_evidence'

    def test_event_beyond_3s_of_both_edges_returns_none(self):
        validator = AdValidator(episode_duration=3600.0)
        validator.validate(
            [{'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'reason': 'x'}],
            audio_analysis=_analysis([_event(50.0)]))
        assert validator._audio_corroboration_source(
            {'start': 100.0, 'end': 130.0}) is None

    def test_vad_gap_clamp_bypassed_by_splice_event(self):
        # Untranscribed tail marker: without corroboration the vad_gap clamp
        # forces it below min_cut_confidence; a splice event at its start
        # bypasses the clamp (TWiT catch-22, spec 1.1 + 2.3a).
        ad = {'start': 3557.6, 'end': 3600.0, 'confidence': 0.85,
              'reason': 'untranscribed tail gap', 'detection_stage': 'vad_gap'}
        corroborated = AdValidator(episode_duration=3600.0,
                                   min_cut_confidence=0.80)
        result = corroborated.validate(
            [dict(ad)], audio_analysis=_analysis([_event(3556.9)]))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value
        assert result.ads[0]['corroborated_by'] == 'splice_evidence'

        bare = AdValidator(episode_duration=3600.0, min_cut_confidence=0.80)
        result = bare.validate([dict(ad)], audio_analysis=_analysis([]))
        assert result.ads[0]['validation']['decision'] == Decision.REVIEW.value
        assert 'corroborated_by' not in result.ads[0]


class TestNoSegmentsVadGapClamp:
    """Pins the no-segments branch of _verify_in_transcript. A fully
    untranscribed episode (segments=[]) with an uncorroborated vad_gap
    marker clamps confidence to min_cut_confidence - 0.01 so it routes to
    REVIEW; a splice event in range corroborates it and skips the clamp.
    This is a deliberate behavior change from Task 14 (previously the empty
    segments branch returned confidence unchanged), backstopped by Task 3's
    tail hold rule.
    """

    def _marker(self):
        return {'start': 100.0, 'end': 130.0, 'confidence': 0.90,
                'reason': 'untranscribed gap', 'detection_stage': 'vad_gap'}

    def test_no_segments_uncorroborated_vad_gap_is_clamped(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                min_cut_confidence=0.80)
        validator._audio_analysis = None
        ad = self._marker()
        result = validator._verify_in_transcript(ad, 0.90, [])
        assert result == pytest.approx(0.79)
        assert 'corroborated_by' not in ad

    def test_no_segments_splice_corroborated_vad_gap_not_clamped(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                min_cut_confidence=0.80)
        validator._audio_analysis = _analysis([_event(97.5)])
        ad = self._marker()
        result = validator._verify_in_transcript(ad, 0.90, [])
        assert result == pytest.approx(0.90)
        assert ad['corroborated_by'] == 'splice_evidence'


class TestSpliceVeto:
    """Zero-evidence veto (spec 2.3c): Vrbo-shaped 90s cut with no splice
    evidence anywhere near it is demoted to REVIEW + held_for_review."""

    _AD = {'start': 1800.0, 'end': 1890.0, 'confidence': 0.92,
           'reason': 'Vrbo vacation rental read with booking details',
           'detection_stage': 'claude'}

    def _validate(self, analysis, **kwargs):
        validator = AdValidator(episode_duration=3600.0, **kwargs)
        return validator.validate([dict(self._AD)], audio_analysis=analysis)

    def test_evidence_less_long_cut_demoted_to_review(self):
        result = self._validate(_analysis([]))
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['held_for_review'] is True
        assert ad['hold_reason'] == 'no_splice_evidence'

    def test_corroborated_cut_untouched(self):
        result = self._validate(_analysis([_event(1801.0)]))
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_event_near_edge_counts(self):
        result = self._validate(_analysis([_event(1892.0)]))  # 2s past end
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_cold_start_never_vetoes(self):
        result = self._validate(_analysis([], status='cold_start'))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_disabled_never_vetoes(self):
        result = self._validate(_analysis([]), splice_veto_enabled=False)
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_short_cut_not_vetoed(self):
        validator = AdValidator(episode_duration=3600.0)
        short = dict(self._AD, end=1855.0)  # 55s < 60s floor
        result = validator.validate([short], audio_analysis=_analysis([]))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_other_stages_not_vetoed(self):
        validator = AdValidator(episode_duration=3600.0)
        manual = dict(self._AD, detection_stage='manual')
        result = validator.validate([manual], audio_analysis=_analysis([]))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_no_audio_analysis_never_vetoes(self):
        validator = AdValidator(episode_duration=3600.0)
        result = validator.validate([dict(self._AD)])
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

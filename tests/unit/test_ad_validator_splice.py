"""Splice-evidence consumers in AdValidator (spec 2.3a).

Depends on Task 2's _audio_corroboration_source and the
validate(..., audio_analysis=) wiring; adds the splice_evidence source.
"""
import os
import sys

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

"""Splice-evidence consumers in AdValidator (spec 2.3a).

Builds on _audio_corroboration_source and the validate(..., audio_analysis=)
wiring, adding the splice_evidence source.
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
    The empty-segments branch used to return confidence unchanged; the clamp
    is deliberate, backstopped by the tail hold rule.
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

    @pytest.mark.parametrize('stage', ['manual', 'vad_gap', 'fingerprint',
                                       'dai_differential', 'cue'])
    def test_other_stages_not_vetoed(self, stage):
        """Only claude/text_pattern are subject to the splice veto; every
        other stage carries its own evidence and is exempt. vad_gap can still
        route to REVIEW via its own confidence clamp, but never with the
        no_splice_evidence hold, so assert the veto did not fire."""
        validator = AdValidator(episode_duration=3600.0)
        ad = dict(self._AD, detection_stage=stage)
        result = validator.validate([ad], audio_analysis=_analysis([]))
        result_ad = result.ads[0]
        assert result_ad.get('hold_reason') != 'no_splice_evidence'
        assert not result_ad.get('held_for_review')

    def test_no_audio_analysis_never_vetoes(self):
        validator = AdValidator(episode_duration=3600.0)
        result = validator.validate([dict(self._AD)])
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_dai_transition_pair_near_edge_exempts_from_veto(self):
        # Finding 1: Rule 3 must gate on full corroboration (not just splice events).
        # A long claude cut with a DAI transition pair near an edge is corroborated
        # and must not be vetoed, even when splice_evidence.events is empty.
        analysis = {
            'signals': [{
                'start': 1798.0, 'end': 1892.0,
                'signal_type': 'dai_transition_pair',
                'confidence': 0.95, 'duration': 94.0,
                'details': {'avg_delta_db': 14.0, 'start_direction': 'down',
                            'start_delta_db': 14.2, 'end_delta_db': 13.8,
                            'start_from_lufs': -16.0, 'start_to_lufs': -30.2,
                            'end_from_lufs': -30.0, 'end_to_lufs': -16.2},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_dai_differential_region_overlap_exempts_from_veto(self):
        # Finding 1: a dai_differential region overlapping the ad is corroboration
        # and must also exempt it from the splice veto.
        analysis = {
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
            'dai_differential': {'status': 'ok', 'regions': [
                {'start_s': 1790.0, 'end_s': 1900.0,
                 'kind': 'differential', 'corr': 0.0}
            ]},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_template_cue_near_edge_exempts_from_veto(self):
        # A learned ad-break template firing at an edge is splice evidence;
        # a long text_pattern cut bracketed by one must not be vetoed.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.99,
                'details': {'source': 'template', 'template_id': 1,
                            'cue_type': 'ad_break_boundary'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_spectral_fallback_cue_does_not_exempt(self):
        # Spectral cues (no template source) stay advisory: too coarse to
        # count as splice evidence.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.9,
                'details': {'cue_type': 'ad_break_boundary'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'

    def test_non_ad_role_template_cue_does_not_exempt(self):
        # A show intro/outro stinger (role non_ad, #350) at an edge is not
        # ad-break evidence and must not lift the veto.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.99,
                'details': {'source': 'template', 'template_id': 2,
                            'role': 'non_ad', 'cue_type': 'show_outro'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'


class TestProtectedBoundsClamp:
    def test_merged_protected_end_clamped_to_duration(self):
        # A protected member recorded past EOF must not survive validation,
        # or the reviewer floor re-expands the marker beyond the file.
        validator = AdValidator(episode_duration=2315.9)
        ad = {'start': 2159.6, 'end': 2346.6, 'confidence': 0.9,
              'reason': 'tail pattern', 'detection_stage': 'text_pattern',
              'merged_distinct_ads': True,
              'merged_protected_start': 2159.6,
              'merged_protected_end': 2346.6}
        result = validator.validate([ad])
        out = result.ads[0]
        assert out['end'] == 2315.9
        assert out['merged_protected_end'] == 2315.9

"""Unit tests for AdValidator class."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision
from config import (
    HOLD_REASON_MAX_DURATION, HOLD_REASON_NO_CUE,
    HOLD_REASON_UNCORROBORATED_TAIL,
)


class TestAdValidatorDuration:
    """Tests for ad duration validation."""

    def test_reject_too_short_ads(self, sample_transcript):
        """Ads shorter than MIN_AD_DURATION (7s) should be rejected."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        short_ad = {
            'start': 50.0,
            'end': 55.0,  # 5 seconds - below 7s minimum
            'confidence': 0.90,
            'reason': 'Quick sponsor mention'
        }

        result = validator.validate([short_ad])

        assert result.rejected == 1
        assert result.accepted == 0
        assert any('ERROR' in f for f in result.ads[0]['validation']['flags'])

    def test_reject_too_long_ads(self, sample_transcript):
        """Ads longer than MAX_AD_DURATION (300s) should be rejected."""
        validator = AdValidator(episode_duration=600.0, segments=sample_transcript)

        long_ad = {
            'start': 100.0,
            'end': 450.0,  # 350 seconds - above 300s max
            'confidence': 0.70,
            'reason': 'Extended segment'
        }

        result = validator.validate([long_ad])

        assert result.rejected == 1
        assert any('Very long' in f for f in result.ads[0]['validation']['flags'])

    def test_accept_long_ad_with_sponsor_confirmed(self, sample_transcript):
        """Long ads with sponsor confirmed in description should use higher limit."""
        # Episode description mentions BetterHelp
        description = '<strong>Sponsors:</strong> <a href="https://betterhelp.com/podcast">BetterHelp</a>'

        validator = AdValidator(
            episode_duration=1200.0,
            segments=sample_transcript,
            episode_description=description
        )

        long_ad = {
            'start': 100.0,
            'end': 500.0,  # 400 seconds - above 300s but below 900s confirmed limit
            'confidence': 0.90,
            'reason': 'BetterHelp sponsor read with extended testimonial'
        }

        result = validator.validate([long_ad])

        # Should not be rejected due to confirmed sponsor
        assert result.rejected == 0


class TestAdValidatorConfidence:
    """Tests for confidence-based validation."""

    def test_accept_high_confidence(self, sample_transcript):
        """Ads with confidence >= 0.85 should be accepted."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        high_conf_ad = {
            'start': 30.0,
            'end': 90.0,
            'confidence': 0.95,
            'reason': 'BetterHelp sponsor read'
        }

        result = validator.validate([high_conf_ad])

        assert result.accepted == 1
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_review_medium_confidence(self, sample_transcript):
        """Ads with confidence between 0.3 and 0.85 may need review."""
        validator = AdValidator(episode_duration=300.0, segments=[])  # No transcript for verification

        medium_conf_ad = {
            'start': 100.0,
            'end': 150.0,
            'confidence': 0.50,
            'reason': 'Possible promotional content'
        }

        result = validator.validate([medium_conf_ad])

        # Could be review or accept depending on other factors
        assert result.ads[0]['validation']['adjusted_confidence'] <= 0.85

    def test_reject_low_confidence(self, sample_transcript):
        """Ads with confidence < 0.3 should be rejected."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        low_conf_ad = {
            'start': 100.0,
            'end': 120.0,
            'confidence': 0.20,
            'reason': 'Maybe an ad'
        }

        result = validator.validate([low_conf_ad])

        assert result.rejected == 1
        assert result.ads[0]['validation']['decision'] == Decision.REJECT.value


class TestAdValidatorMerging:
    """Tests for ad merging behavior."""

    def test_merge_close_ads(self, sample_transcript):
        """Ads within MERGE_GAP_THRESHOLD (5s) should be merged."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        close_ads = [
            {'start': 30.0, 'end': 60.0, 'confidence': 0.90, 'reason': 'First ad segment'},
            {'start': 63.0, 'end': 90.0, 'confidence': 0.85, 'reason': 'Second ad segment'}  # 3s gap
        ]

        result = validator.validate(close_ads)

        # Should be merged into one ad
        assert len(result.ads) == 1
        assert result.ads[0]['start'] == 30.0
        assert result.ads[0]['end'] == 90.0
        assert 'Merged' in ' '.join(result.corrections)

    def test_no_merge_for_large_gap(self, sample_transcript):
        """Ads with gaps > MERGE_GAP_THRESHOLD should not be merged."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        separate_ads = [
            {'start': 30.0, 'end': 60.0, 'confidence': 0.90, 'reason': 'First ad'},
            {'start': 120.0, 'end': 150.0, 'confidence': 0.85, 'reason': 'Second ad'}  # 60s gap
        ]

        result = validator.validate(separate_ads)

        # Should remain as two separate ads
        assert len(result.ads) == 2


class TestAdValidatorPosition:
    """Tests for position-based confidence adjustments."""

    def test_position_boost_preroll(self):
        """Pre-roll position (first 5%) should get confidence boost."""
        validator = AdValidator(episode_duration=1000.0)

        # Ad at 2% position (pre-roll) with known sponsor name for boost
        preroll_ad = {
            'start': 10.0,
            'end': 70.0,
            'confidence': 0.75,
            'reason': 'BetterHelp promo with discount code'
        }

        result = validator.validate([preroll_ad])

        # Original confidence 0.75 + 0.10 (pre-roll) + 0.10 (sponsor name) = 0.95
        # Should be at least 0.85 after all adjustments
        assert result.ads[0]['validation']['adjusted_confidence'] >= 0.85

    def test_position_boost_postroll(self):
        """Post-roll position (last 5%) should get confidence boost."""
        validator = AdValidator(episode_duration=1000.0)

        # Ad at 96% position (post-roll) with known sponsor name
        postroll_ad = {
            'start': 960.0,
            'end': 1000.0,
            'confidence': 0.75,
            'reason': 'NordVPN end-of-show promo'
        }

        result = validator.validate([postroll_ad])

        # Original confidence 0.75 + 0.05 (post-roll) + 0.10 (sponsor name) = 0.90
        # Should be at least 0.80 after all adjustments
        assert result.ads[0]['validation']['adjusted_confidence'] >= 0.80


class TestAdValidatorBoundaries:
    """Tests for boundary clamping."""

    def test_clamp_negative_start(self, sample_transcript):
        """Negative start times should be clamped to 0."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        negative_start_ad = {
            'start': -10.0,
            'end': 60.0,
            'confidence': 0.90,
            'reason': 'Ad with negative start'
        }

        result = validator.validate([negative_start_ad])

        assert result.ads[0]['start'] == 0
        assert 'Clamped negative start' in ' '.join(result.corrections)

    def test_clamp_end_to_duration(self, sample_transcript):
        """End times beyond episode duration should be clamped."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        past_end_ad = {
            'start': 250.0,
            'end': 400.0,  # Beyond 300s episode duration
            'confidence': 0.90,
            'reason': 'Ad extending past end'
        }

        result = validator.validate([past_end_ad])

        assert result.ads[0]['end'] == 300.0
        assert 'Clamped end' in ' '.join(result.corrections)


class TestAdValidatorResults:
    """Tests for validation result counts and structure."""

    def test_validation_result_counts(self, sample_transcript):
        """Verify accepted/reviewed/rejected counts are accurate."""
        validator = AdValidator(episode_duration=600.0, segments=sample_transcript)

        mixed_ads = [
            {'start': 30.0, 'end': 90.0, 'confidence': 0.95, 'reason': 'High conf ad'},
            {'start': 200.0, 'end': 205.0, 'confidence': 0.90, 'reason': 'Too short'},  # Rejected
            {'start': 300.0, 'end': 360.0, 'confidence': 0.15, 'reason': 'Low conf'}  # Rejected
        ]

        result = validator.validate(mixed_ads)

        total = result.accepted + result.reviewed + result.rejected
        assert total == len(result.ads)
        assert result.rejected >= 2  # At least the short and low-conf ads

    def test_empty_ads_list(self):
        """Empty ads list should return empty result."""
        validator = AdValidator(episode_duration=300.0)

        result = validator.validate([])

        assert result.ads == []
        assert result.accepted == 0
        assert result.reviewed == 0
        assert result.rejected == 0


class TestAdValidatorFalsePositives:
    """Tests for user-marked false positive handling."""

    def test_reject_overlapping_false_positive(self, sample_transcript):
        """Ads overlapping with user-marked false positives should be rejected."""
        false_positives = [
            {'start': 100.0, 'end': 150.0}  # User marked this as NOT an ad
        ]

        validator = AdValidator(
            episode_duration=300.0,
            segments=sample_transcript,
            false_positive_corrections=false_positives
        )

        overlapping_ad = {
            'start': 110.0,
            'end': 140.0,  # Within the false positive range
            'confidence': 0.95,
            'reason': 'High confidence but user says no'
        }

        result = validator.validate([overlapping_ad])

        assert result.rejected == 1
        assert 'false positive' in result.ads[0]['validation']['flags'][0].lower()


class TestAdValidatorReasonQuality:
    """Tests for reason quality checks."""

    def test_reject_not_an_ad_reason(self, sample_transcript):
        """Ads with reason indicating 'not an ad' should be rejected."""
        validator = AdValidator(episode_duration=300.0, segments=sample_transcript)

        not_ad = {
            'start': 100.0,
            'end': 150.0,
            'confidence': 0.80,
            'reason': 'This is not an advertisement, just regular show content'
        }

        result = validator.validate([not_ad])

        assert result.rejected == 1

    def test_penalize_vague_reason(self, sample_transcript):
        """Ads with vague reasons should have confidence penalized."""
        validator = AdValidator(episode_duration=300.0, segments=[])

        vague_ad = {
            'start': 100.0,
            'end': 150.0,
            'confidence': 0.70,
            'reason': 'advertisement'  # Very vague
        }

        result = validator.validate([vague_ad])

        # Confidence should be reduced from original
        assert result.ads[0]['validation']['adjusted_confidence'] < 0.70


class TestNotAdPatternsRegex:
    """Tests for NOT_AD_PATTERNS regex accuracy."""

    def test_transition_from_show_content_not_matched(self):
        """Regression: 'transition from show content' must NOT trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.99,
            'reason': 'ZipRecruiter sponsor read - transition from show content to ad'
        }

        result = validator.validate([ad])

        assert result.rejected == 0, (
            "Reason containing 'transition from show content' should not be rejected"
        )

    def test_return_to_show_content_not_matched(self):
        """'return to show content' must NOT trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.95,
            'reason': 'Ad segment before return to show content'
        }

        result = validator.validate([ad])

        assert result.rejected == 0, (
            "Reason containing 'return to show content' should not be rejected"
        )

    def test_is_show_content_still_matched(self):
        """'is show content' MUST still trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.80,
            'reason': 'This segment is show content, not a sponsor'
        }

        result = validator.validate([ad])

        assert result.rejected == 1

    def test_appears_to_be_regular_content_still_matched(self):
        """'appears to be regular content' MUST still trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.80,
            'reason': 'This appears to be regular content'
        }

        result = validator.validate([ad])

        assert result.rejected == 1

    def test_not_an_ad_still_matched(self):
        """'not an ad' MUST still trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.80,
            'reason': 'This is not an advertisement'
        }

        result = validator.validate([ad])

        assert result.rejected == 1

    def test_false_positive_still_matched(self):
        """'false positive' MUST still trigger rejection."""
        validator = AdValidator(episode_duration=600.0, segments=[])

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.80,
            'reason': 'Likely a false positive detection'
        }

        result = validator.validate([ad])

        assert result.rejected == 1


class TestConfirmedCorrections:
    """Tests for user-confirmed correction handling."""

    def test_confirmed_correction_force_accept(self):
        """Low-confidence ad overlapping confirmed correction gets ACCEPT at 1.0."""
        confirmed = [
            {'start': 100.0, 'end': 200.0}
        ]

        validator = AdValidator(
            episode_duration=600.0,
            segments=[],
            confirmed_corrections=confirmed
        )

        ad = {
            'start': 110.0,
            'end': 190.0,
            'confidence': 0.40,
            'reason': 'Low confidence sponsor mention'
        }

        result = validator.validate([ad])

        assert result.accepted == 1
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value
        assert result.ads[0]['validation']['adjusted_confidence'] == 1.0

    def test_false_positive_wins_over_confirmed(self):
        """Segment with both corrections gets REJECT (false_positive priority)."""
        false_positives = [
            {'start': 100.0, 'end': 200.0}
        ]
        confirmed = [
            {'start': 100.0, 'end': 200.0}
        ]

        validator = AdValidator(
            episode_duration=600.0,
            segments=[],
            false_positive_corrections=false_positives,
            confirmed_corrections=confirmed
        )

        ad = {
            'start': 110.0,
            'end': 190.0,
            'confidence': 0.95,
            'reason': 'High confidence ad'
        }

        result = validator.validate([ad])

        assert result.rejected == 1
        assert result.ads[0]['validation']['decision'] == Decision.REJECT.value

    def test_no_confirmed_corrections_normal_flow(self):
        """Without confirmed corrections, normal validation applies."""
        validator = AdValidator(
            episode_duration=600.0,
            segments=[],
            confirmed_corrections=[]
        )

        ad = {
            'start': 100.0,
            'end': 200.0,
            'confidence': 0.40,
            'reason': 'Low confidence mention'
        }

        result = validator.validate([ad])

        # Low confidence with no boosts should not be accepted
        assert result.ads[0]['validation']['decision'] != Decision.ACCEPT.value


class TestAdValidatorVadGapVerification:
    """Tests for vad_gap-specific transcript verification.

    Regression: MacBreak Weekly 1021 (5ef2df166c8e) produced 8 vad_gap
    markers carrying 'WARN: No ad signals in transcript' that were ACCEPTed
    at adjusted confidence 0.80. Validator must not auto-cut a vad_gap
    marker that has no corroborating sponsor or ad-signal pattern in range.
    """

    def test_vad_gap_with_no_signals_drops_below_cut_threshold(self):
        segments = [
            {'start': 2080.0, 'end': 2098.0,
             'text': 'And so Apple has been making moves recently.'},
        ]
        validator = AdValidator(episode_duration=8000.0, segments=segments)
        marker = {
            'start': 2081.8,
            'end': 2097.6,
            'confidence': 0.75,
            'reason': 'VAD gap with signoff and resume context',
            'detection_stage': 'vad_gap',
        }
        result = validator.validate([marker])
        assert result.ads[0]['validation']['decision'] != Decision.ACCEPT.value
        assert result.ads[0]['validation']['adjusted_confidence'] < 0.80

    def test_vad_gap_with_sponsor_in_range_keeps_confidence(self):
        # Sponsor branch returns early, so the vad_gap clamp never runs.
        segments = [
            {'start': 2080.0, 'end': 2098.0,
             'text': 'BetterHelp helps you find a therapist online.'},
        ]
        validator = AdValidator(episode_duration=8000.0, segments=segments)
        marker = {
            'start': 2081.8,
            'end': 2097.6,
            'confidence': 0.75,
            'reason': 'VAD gap with signoff and resume context',
            'detection_stage': 'vad_gap',
        }
        result = validator.validate([marker])
        assert result.ads[0]['validation']['adjusted_confidence'] >= 0.80

    def test_non_vad_gap_no_signals_not_penalized(self):
        # Claude-stage marker: vad_gap clamp must not apply.
        segments = [
            {'start': 100.0, 'end': 150.0,
             'text': 'Just regular conversation about the topic at hand.'},
        ]
        validator = AdValidator(episode_duration=8000.0, segments=segments)
        marker = {
            'start': 100.0,
            'end': 150.0,
            'confidence': 0.85,
            'reason': 'Identified by Claude as a paid read',
            'detection_stage': 'claude',
        }
        result = validator.validate([marker])
        # If the clamp fired it would drop to 0.79; without it the marker
        # stays at or above 0.85 (modulo position adjustments).
        assert result.ads[0]['validation']['adjusted_confidence'] > 0.79


class TestPositionalPriorBoost:
    """Tests for learned positional prior boosts (issue #360)."""

    def _prior(self, zones):
        from positional_prior import LearnedZone, PositionalPrior
        return PositionalPrior(
            episodes_considered=10, median_duration=1000.0,
            zones=[LearnedZone(center=c, low=lo, high=hi, support=8, boost=b)
                   for c, lo, hi, b in zones])

    def test_in_zone_ad_gains_zone_boost(self):
        prior = self._prior([(0.30, 0.25, 0.35, 0.075)])
        validator = AdValidator(episode_duration=1000.0, segments=[],
                                positional_prior=prior)
        assert validator._apply_position_boost(0.80, 0.30) == pytest.approx(0.875)

    def test_boost_capped_at_one(self):
        prior = self._prior([(0.30, 0.25, 0.35, 0.075)])
        validator = AdValidator(episode_duration=1000.0, segments=[],
                                positional_prior=prior)
        assert validator._apply_position_boost(0.97, 0.30) == pytest.approx(1.0)

    def test_overlapping_zones_use_max_boost(self):
        prior = self._prior([(0.30, 0.25, 0.35, 0.05),
                             (0.40, 0.34, 0.46, 0.10)])
        validator = AdValidator(episode_duration=1000.0, segments=[],
                                positional_prior=prior)
        # 0.345 sits in both zones; the stronger boost wins.
        assert validator._apply_position_boost(0.80, 0.345) == pytest.approx(0.90)

    def test_no_boost_outside_learned_zones_even_in_global_preroll(self):
        prior = self._prior([(0.30, 0.25, 0.35, 0.075)])
        validator = AdValidator(episode_duration=1000.0, segments=[],
                                positional_prior=prior)
        # 0.01 sits in the global PRE_ROLL zone; learned prior replaces it.
        assert validator._apply_position_boost(0.80, 0.01) == pytest.approx(0.80)

    def test_none_prior_keeps_global_zone_behavior(self):
        validator = AdValidator(episode_duration=1000.0, segments=[])
        assert validator._apply_position_boost(0.80, 0.01) == pytest.approx(0.90)
        assert validator._apply_position_boost(0.80, 0.50) == pytest.approx(0.85)
        assert validator._apply_position_boost(0.80, 0.97) == pytest.approx(0.85)
        assert validator._apply_position_boost(0.80, 0.10) == pytest.approx(0.80)


class TestMaxAdDurationHold:
    """Per-feed max duration override hold rules (Phase C)."""

    def _ad(self, start, end, confidence=0.95, reason='BetterHelp sponsor read'):
        return {'start': start, 'end': end, 'confidence': confidence, 'reason': reason}

    def test_high_conf_over_cap_is_held(self):
        # conf 0.95, 300s ad, 240s override -> HELD (HIGH_CONFIDENCE_OVERRIDE ACCEPT suppressed)
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 400.0, confidence=0.95)])
        ad = result.ads[0]
        assert ad.get('held_for_review') is True
        assert ad.get('hold_reason') == HOLD_REASON_MAX_DURATION
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_very_long_high_conf_over_cap_is_held(self):
        # 700s ad at conf 0.95 (today: override-ACCEPT via HIGH_CONFIDENCE_OVERRIDE) + 240s cap -> HELD
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 800.0, confidence=0.95)])
        ad = result.ads[0]
        assert ad.get('held_for_review') is True
        assert ad.get('hold_reason') == HOLD_REASON_MAX_DURATION
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_duration_only_reject_over_cap_is_held(self):
        # 350s ad, conf 0.70 (>= REJECT_CONFIDENCE 0.30), no other errors -> HELD
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 450.0, confidence=0.70,
                                              reason='sponsor read')])
        ad = result.ads[0]
        assert ad.get('held_for_review') is True
        assert ad.get('hold_reason') == HOLD_REASON_MAX_DURATION
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_low_conf_over_cap_stays_reject(self):
        # conf 0.25 < REJECT_CONFIDENCE 0.30 -> REJECT, not held
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 400.0, confidence=0.25)])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.REJECT.value

    def test_override_none_behaves_like_existing_long_ad(self):
        # No override set -> same behavior as the baseline long-ad test
        validator = AdValidator(episode_duration=3600.0, segments=[])
        result = validator.validate([self._ad(100.0, 450.0, confidence=0.70,
                                              reason='sponsor read')])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        # 350s at conf 0.70 without override: flagged Very long -> REJECT
        assert ad['validation']['decision'] == Decision.REJECT.value

    def test_confirm_corrected_over_cap_is_accepted(self):
        # Confirmed correction early-returns ACCEPT before hold rules run
        confirmed = [{'start': 100.0, 'end': 400.0}]
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                confirmed_corrections=confirmed,
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 400.0, confidence=0.95)])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_fp_corrected_over_cap_is_rejected_not_held(self):
        # FP correction early-returns REJECT before hold rules run
        fp = [{'start': 100.0, 'end': 400.0}]
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                false_positive_corrections=fp,
                                max_ad_duration_override=240.0)
        result = validator.validate([self._ad(100.0, 400.0, confidence=0.95)])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.REJECT.value

    def test_stale_held_flags_popped_on_fp_path(self):
        # Stale held_for_review must be cleared even when FP short-circuits
        fp = [{'start': 100.0, 'end': 400.0}]
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                false_positive_corrections=fp,
                                max_ad_duration_override=240.0)
        stale_ad = self._ad(100.0, 400.0)
        stale_ad['held_for_review'] = True
        stale_ad['hold_reason'] = HOLD_REASON_MAX_DURATION
        result = validator.validate([stale_ad])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert 'hold_reason' not in ad

    def test_stale_held_flags_popped_on_confirm_path(self):
        confirmed = [{'start': 100.0, 'end': 400.0}]
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                confirmed_corrections=confirmed,
                                max_ad_duration_override=240.0)
        stale_ad = self._ad(100.0, 400.0)
        stale_ad['held_for_review'] = True
        stale_ad['hold_reason'] = HOLD_REASON_MAX_DURATION
        result = validator.validate([stale_ad])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert 'hold_reason' not in ad


class TestCueGatedApproval:
    """Per-feed cue-gated approval hold rules (Phase C)."""

    def _ad(self, start=100.0, end=160.0, confidence=0.95, reason='BetterHelp read', **kwargs):
        base = {'start': start, 'end': end, 'confidence': confidence, 'reason': reason}
        base.update(kwargs)
        return base

    def test_gate_on_no_evidence_is_held(self):
        # cue_gate_enabled + would-be ACCEPT + no cue evidence -> HELD
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True)
        result = validator.validate([self._ad()])
        ad = result.ads[0]
        assert ad.get('held_for_review') is True
        assert ad.get('hold_reason') == HOLD_REASON_NO_CUE
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_gate_on_cue_snap_present_is_accepted(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True)
        result = validator.validate([self._ad(cue_snap={'start': 98.0, 'end': 162.0})])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_gate_on_detection_stage_cue_pair_is_accepted(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True)
        result = validator.validate([self._ad(detection_stage='cue_pair')])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_gate_on_manual_stage_is_accepted(self):
        # 'manual' is exempt from cue gating -- human decision
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True)
        result = validator.validate([self._ad(detection_stage='manual')])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_gate_off_no_evidence_not_held(self):
        # Gate disabled -> no hold regardless of cue evidence
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=False)
        result = validator.validate([self._ad()])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_gate_on_below_threshold_is_plain_review_not_held(self):
        # Below min_cut_confidence -> REVIEW but NOT held (held == "would have been cut")
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True, min_cut_confidence=0.80)
        # confidence 0.50 -> no position boost without sponsor -> stays REVIEW
        ad_input = {'start': 500.0, 'end': 560.0, 'confidence': 0.50,
                    'reason': 'possible sponsor mention'}
        result = validator.validate([ad_input])
        ad = result.ads[0]
        assert not ad.get('held_for_review'), (
            "Below-threshold REVIEW should not be marked held"
        )
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_rounding_boundary_no_cue_is_held_not_accepted(self):
        # Unrounded 0.7996 rounds to 0.800 == slider 0.80. The rounded value must
        # not slip past the cue gate as a plain ACCEPT: decision rounds too, so
        # the cue-gate hold rule sees ACCEPT and holds the no-cue ad. Position
        # 0.10 and a neutral reason avoid position/reason boosts.
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True, min_cut_confidence=0.80)
        result = validator.validate([self._ad(start=360.0, end=420.0,
                                              confidence=0.7996, reason='promotional read')])
        ad = result.ads[0]
        assert ad.get('held_for_review') is True, (
            "Rounded-up no-cue ad must be held, not silently accepted"
        )
        assert ad.get('hold_reason') == HOLD_REASON_NO_CUE
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_rounding_boundary_cue_backed_is_accepted(self):
        # Same boundary but cue-backed -> allowed to ACCEPT via the fall-through.
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                cue_gate_enabled=True, min_cut_confidence=0.80)
        result = validator.validate([self._ad(start=360.0, end=420.0,
                                              confidence=0.7996, reason='promotional read',
                                              cue_snap={'start': 358.0, 'end': 422.0})])
        ad = result.ads[0]
        assert not ad.get('held_for_review')
        assert ad['validation']['decision'] == Decision.ACCEPT.value


class TestAudioCorroborationSource:
    """Layer 1 audio corroboration seam (ad-splice-detection spec 1.1).

    The stored audio_analysis dict is AudioAnalysisResult.to_dict():
    {'signals': [{'start', 'end', 'signal_type', 'confidence', 'duration',
    'details'}, ...], 'loudness_baseline', 'analysis_time_seconds', 'errors'}.
    """

    def _validator(self, analysis):
        validator = AdValidator(episode_duration=10600.0, segments=[])
        validator._audio_analysis = analysis
        return validator

    def _transition_pair(self, start, end, avg_delta_db=15.0):
        return {
            'start': start, 'end': end,
            'signal_type': 'dai_transition_pair',
            'confidence': 0.95, 'duration': end - start,
            'details': {'avg_delta_db': avg_delta_db, 'start_direction': 'down',
                        'start_delta_db': avg_delta_db, 'end_delta_db': avg_delta_db,
                        'start_from_lufs': -16.0, 'start_to_lufs': -31.0,
                        'end_from_lufs': -31.0, 'end_to_lufs': -16.0},
        }

    def _volume_anomaly(self, start, end, deviation_db=-15.0):
        return {
            'start': start, 'end': end,
            'signal_type': 'volume_decrease',
            'confidence': 0.9, 'duration': end - start,
            'details': {'deviation_db': deviation_db, 'baseline_lufs': -16.0,
                        'direction': 'decrease'},
        }

    def test_validate_stores_audio_analysis_kwarg(self):
        validator = AdValidator(episode_duration=10600.0, segments=[])
        analysis = {'signals': [], 'loudness_baseline': -16.0}
        validator.validate([], audio_analysis=analysis)
        assert validator._audio_analysis is analysis

    def test_transition_pair_near_marker_start(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        analysis = {'signals': [self._transition_pair(10557.4, 10599.6)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) == 'transition_pair'

    def test_transition_pair_near_marker_end_only(self):
        # Pair end within 5s of the ad end; pair start far from both edges.
        ad = {'start': 500.0, 'end': 560.0}
        analysis = {'signals': [self._transition_pair(400.0, 557.0)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) == 'transition_pair'

    def test_volume_anomaly_at_boundary(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        analysis = {'signals': [self._volume_anomaly(10558.0, 10599.0)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) == 'volume_anomaly'

    def test_volume_anomaly_below_12db_ignored(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        analysis = {'signals': [self._volume_anomaly(10558.0, 10599.0, deviation_db=-8.0)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) is None

    def test_signal_outside_window_ignored(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        analysis = {'signals': [self._transition_pair(10500.0, 10550.0)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) is None

    def test_no_analysis_returns_none(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        validator = AdValidator(episode_duration=10600.0, segments=[])
        assert validator._audio_corroboration_source(ad) is None

    def test_transition_pair_outranks_volume_anomaly(self):
        ad = {'start': 10557.6, 'end': 10600.0}
        analysis = {'signals': [self._volume_anomaly(10558.0, 10599.0),
                                self._transition_pair(10557.4, 10599.6)]}
        assert self._validator(analysis)._audio_corroboration_source(ad) == 'transition_pair'


class TestVadGapClampBypass:
    """TWiT 1091 regression (this-week-in-tech-audio/b37bf6df81a5): a DAI
    post-roll played ~15 dB quieter, Whisper VAD dropped it, and the vad_gap
    marker covering it (0.75 + 0.05 POST_ROLL boost = 0.80) was clamped to
    0.79 because untranscribed audio can never show ad signals in transcript.
    A stored 15 dB transition pair at the boundary must bypass the clamp.
    """

    SEGMENTS = [
        {'start': 10520.0, 'end': 10545.0,
         'text': 'So that is our show for this week everybody.'},
        {'start': 10545.0, 'end': 10557.6,
         'text': 'Thanks for being here and we will see you next time on the show.'},
    ]

    def _marker(self):
        # Untranscribed 42.4s tail; start at 99.6% position (POST_ROLL).
        return {
            'start': 10557.6,
            'end': 10600.0,
            'confidence': 0.75,
            'reason': 'VAD gap at episode tail (42.4s untranscribed)',
            'detection_stage': 'vad_gap',
            'sponsor': None,
        }

    def _tail_transition_analysis(self):
        # Stored 15 dB transition pair bounding the tail (down into the quiet
        # DAI fill at 10557.4s, back up at 10599.6s).
        return {
            'signals': [{
                'start': 10557.4, 'end': 10599.6,
                'signal_type': 'dai_transition_pair',
                'confidence': 0.95, 'duration': 42.2,
                'details': {'avg_delta_db': 15.0, 'start_direction': 'down',
                            'start_delta_db': 15.2, 'end_delta_db': 14.8,
                            'start_from_lufs': -16.0, 'start_to_lufs': -31.2,
                            'end_from_lufs': -31.0, 'end_to_lufs': -16.2},
            }],
            'loudness_baseline': -16.0,
            'analysis_time_seconds': 4.2,
            'errors': [],
        }

    def test_corroborated_tail_marker_is_accepted(self):
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker()],
                                    audio_analysis=self._tail_transition_analysis())
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['adjusted_confidence'] == pytest.approx(0.80)
        assert ad['corroborated_by'] == 'transition_pair'

    def test_uncorroborated_tail_marker_clamped_to_review(self):
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker()])
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['validation']['adjusted_confidence'] == pytest.approx(0.79)
        assert 'corroborated_by' not in ad

    def test_distant_analysis_signal_does_not_bypass_clamp(self):
        analysis = self._tail_transition_analysis()
        analysis['signals'][0]['start'] = 5000.0
        analysis['signals'][0]['end'] = 5060.0
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker()], audio_analysis=analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['validation']['adjusted_confidence'] == pytest.approx(0.79)
        assert 'corroborated_by' not in ad

    def test_empty_text_vad_gap_skips_clamp_and_corroboration(self):
        # Pre-existing early return: empty-text vad_gap markers skip the
        # clamp block entirely. Deliberate - Task 3 holds uncorroborated
        # tail markers via a direct _audio_corroboration_source check, not
        # via corroborated_by.
        segments = [
            {'start': 100.0, 'end': 130.0,
             'text': 'Welcome to the show everybody.'},
        ]
        validator = AdValidator(episode_duration=10600.0, segments=segments)
        result = validator.validate([self._marker()],
                                    audio_analysis=self._tail_transition_analysis())
        ad = result.ads[0]
        assert ad['validation']['adjusted_confidence'] == pytest.approx(0.80)
        assert 'corroborated_by' not in ad

    def test_stale_corroborated_by_popped_when_evidence_absent(self):
        marker = self._marker()
        marker['corroborated_by'] = 'transition_pair'
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([marker])
        assert 'corroborated_by' not in result.ads[0]


class TestUncorroboratedTailHold:
    """Spec 1.3: a vad_gap marker at the episode tail (end within 5s of EOF)
    that stays REVIEW and uncorroborated is held for review, so it lands in
    the pending-review UI instead of shipping silently (TWiT 1091 shipped
    with pendingReviewCount=0).
    """

    SEGMENTS = [
        {'start': 10520.0, 'end': 10545.0,
         'text': 'So that is our show for this week everybody.'},
        {'start': 10545.0, 'end': 10557.6,
         'text': 'Thanks for being here and we will see you next time on the show.'},
    ]

    def _marker(self, confidence=0.75):
        return {
            'start': 10557.6,
            'end': 10600.0,
            'confidence': confidence,
            'reason': 'VAD gap at episode tail (42.4s untranscribed)',
            'detection_stage': 'vad_gap',
            'sponsor': None,
        }

    def _tail_transition_analysis(self):
        return {
            'signals': [{
                'start': 10557.4, 'end': 10599.6,
                'signal_type': 'dai_transition_pair',
                'confidence': 0.95, 'duration': 42.2,
                'details': {'avg_delta_db': 15.0, 'start_direction': 'down',
                            'start_delta_db': 15.2, 'end_delta_db': 14.8,
                            'start_from_lufs': -16.0, 'start_to_lufs': -31.2,
                            'end_from_lufs': -31.0, 'end_to_lufs': -16.2},
            }],
            'loudness_baseline': -16.0,
            'analysis_time_seconds': 4.2,
            'errors': [],
        }

    def test_uncorroborated_tail_review_is_held(self):
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker()])
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad.get('held_for_review') is True
        assert ad.get('hold_reason') == HOLD_REASON_UNCORROBORATED_TAIL

    def test_corroborated_tail_marker_not_held(self):
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker()],
                                    audio_analysis=self._tail_transition_analysis())
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')

    def test_corroborated_below_threshold_review_not_held(self):
        # Corroboration bypasses the clamp but 0.60 + 0.05 = 0.65 < 0.80:
        # stays REVIEW yet is corroborated, so per spec 1.3 it is NOT held.
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        result = validator.validate([self._marker(confidence=0.60)],
                                    audio_analysis=self._tail_transition_analysis())
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['corroborated_by'] == 'transition_pair'
        assert not ad.get('held_for_review')

    def test_mid_episode_vad_gap_review_not_held(self):
        segments = [
            {'start': 2080.0, 'end': 2098.0,
             'text': 'And so Apple has been making moves recently.'},
        ]
        validator = AdValidator(episode_duration=8000.0, segments=segments)
        marker = {
            'start': 2081.8, 'end': 2097.6, 'confidence': 0.75,
            'reason': 'VAD gap with signoff and resume context',
            'detection_stage': 'vad_gap',
        }
        result = validator.validate([marker])
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert not ad.get('held_for_review')

    def test_tail_review_non_vad_gap_not_held(self):
        validator = AdValidator(episode_duration=10600.0, segments=self.SEGMENTS)
        marker = {
            'start': 10557.6, 'end': 10600.0, 'confidence': 0.70,
            'reason': 'promotional read near the end',
            'detection_stage': 'claude',
        }
        result = validator.validate([marker])
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert not ad.get('held_for_review')


class TestDaiDifferentialCorroboration:
    """Layer 3: differential regions corroborate markers (vad_gap clamp bypass)."""

    _REGIONS = {'dai_differential': {'status': 'ok', 'regions': [
        {'start_s': 3500.0, 'end_s': 3552.0, 'kind': 'differential', 'corr': 0.0}]}}

    _SEGMENTS = [
        {'start': 3490.0, 'end': 3506.0,
         'text': 'so anyway that was quite the discussion earlier today'},
    ]

    def _marker(self):
        return {'start': 3505.0, 'end': 3548.0, 'confidence': 0.80,
                'reason': 'Untranscribed audio gap at episode tail',
                'detection_stage': 'vad_gap'}

    def test_source_returned_for_overlap(self):
        validator = AdValidator(episode_duration=3600.0, segments=self._SEGMENTS)
        validator._audio_analysis = self._REGIONS
        assert validator._audio_corroboration_source(self._marker()) == 'dai_differential'

    def test_no_source_without_overlap(self):
        validator = AdValidator(episode_duration=3600.0, segments=self._SEGMENTS)
        validator._audio_analysis = {'dai_differential': {'status': 'ok', 'regions': [
            {'start_s': 100.0, 'end_s': 160.0, 'kind': 'differential', 'corr': 0.0}]}}
        assert validator._audio_corroboration_source(self._marker()) is None

    def test_identical_regions_do_not_corroborate(self):
        validator = AdValidator(episode_duration=3600.0, segments=self._SEGMENTS)
        validator._audio_analysis = {'dai_differential': {'status': 'ok', 'regions': [
            {'start_s': 3500.0, 'end_s': 3552.0, 'kind': 'identical', 'corr': 0.98}]}}
        assert validator._audio_corroboration_source(self._marker()) is None

    def test_vad_gap_clamp_bypassed_with_differential(self):
        validator = AdValidator(episode_duration=3600.0, segments=self._SEGMENTS,
                                min_cut_confidence=0.80)
        result = validator.validate([self._marker()],
                                    audio_analysis=self._REGIONS)
        ad = result.ads[0]
        assert ad['corroborated_by'] == 'dai_differential'
        assert ad['validation']['adjusted_confidence'] >= 0.80

    def test_vad_gap_clamped_without_audio_analysis(self):
        validator = AdValidator(episode_duration=3600.0, segments=self._SEGMENTS,
                                min_cut_confidence=0.80)
        result = validator.validate([self._marker()])
        ad = result.ads[0]
        assert 'corroborated_by' not in ad
        assert ad['validation']['adjusted_confidence'] < 0.80

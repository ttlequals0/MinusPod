"""Unit tests for AdValidator class."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, ValidationResult, Decision


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

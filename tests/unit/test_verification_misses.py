"""Tests for verification false negative recording in PatternService."""
import pytest
from unittest.mock import MagicMock, patch, call

from pattern_service import PatternService


def _make_service(patterns=None):
    """Create a PatternService with mocked DB and pattern lookup."""
    svc = PatternService(db=MagicMock())
    svc.get_patterns_for_podcast = MagicMock(return_value=patterns or [])
    svc.record_pattern_match = MagicMock()
    return svc


class TestRecordVerificationMisses:
    """Test PatternService.record_verification_misses."""

    def test_skips_when_no_db(self):
        svc = PatternService(db=None)
        # Should not raise
        svc.record_verification_misses("slug", "ep1", [{"sponsor": "Acme", "start": 0, "end": 60}])

    def test_skips_unknown_sponsors(self):
        svc = _make_service()
        svc.record_verification_misses("slug", "ep1", [
            {"sponsor": "unknown", "start": 0, "end": 60},
            {"sponsor": "N/A", "start": 0, "end": 60},
            {"sponsor": "", "start": 0, "end": 60},
            {"sponsor": None, "start": 0, "end": 60},
        ])
        svc.record_pattern_match.assert_not_called()

    def test_boosts_matching_pattern(self):
        patterns = [
            {"id": 42, "sponsor": "Acme"},
            {"id": 99, "sponsor": "OtherCo"},
        ]
        svc = _make_service(patterns)

        svc.record_verification_misses("slug", "ep1", [
            {"sponsor": "Acme", "start": 100, "end": 160}
        ])

        svc.record_pattern_match.assert_called_once_with(
            42, episode_id="ep1", observed_duration=60
        )

    def test_case_insensitive_sponsor_match(self):
        patterns = [{"id": 10, "sponsor": "BetterHelp"}]
        svc = _make_service(patterns)

        svc.record_verification_misses("slug", "ep1", [
            {"sponsor": "betterhelp", "start": 0, "end": 90}
        ])

        svc.record_pattern_match.assert_called_once()

    def test_logs_unmatched_sponsor(self):
        svc = _make_service(patterns=[])

        with patch("pattern_service.logger") as mock_logger:
            svc.record_verification_misses("slug", "ep1", [
                {"sponsor": "NewSponsor", "start": 0, "end": 60}
            ])
            # Should log that no pattern exists
            assert any(
                "No existing pattern" in str(c) and "NewSponsor" in str(c)
                for c in mock_logger.info.call_args_list
            )

    def test_loads_patterns_once_for_multiple_ads(self):
        patterns = [{"id": 1, "sponsor": "Acme"}]
        svc = _make_service(patterns)

        svc.record_verification_misses("slug", "ep1", [
            {"sponsor": "Acme", "start": 100, "end": 160},
            {"sponsor": "Acme", "start": 500, "end": 560},
            {"sponsor": "Unknown Co", "start": 200, "end": 260},
        ])

        # Patterns loaded once, not per-ad
        svc.get_patterns_for_podcast.assert_called_once_with("slug")
        # Acme matched twice
        assert svc.record_pattern_match.call_count == 2

    def test_exception_in_one_ad_does_not_block_others(self):
        patterns = [
            {"id": 1, "sponsor": "First"},
            {"id": 2, "sponsor": "Third"},
        ]
        svc = _make_service(patterns)
        # First call raises, second should still work
        svc.record_pattern_match.side_effect = [Exception("DB error"), None]

        svc.record_verification_misses("slug", "ep1", [
            {"sponsor": "First", "start": 0, "end": 60},
            {"sponsor": "Third", "start": 100, "end": 160},
        ])

        assert svc.record_pattern_match.call_count == 2


class TestRecordVerificationMissesAutoCreate:
    """Verify auto-creation of podcast-scoped patterns for unmatched sponsors."""

    def test_auto_creates_pattern_for_unknown_sponsor_when_segments_provided(self):
        svc = _make_service(patterns=[])
        fake_matcher = MagicMock()
        fake_matcher.create_pattern_from_ad.return_value = 555
        svc._text_pattern_matcher = fake_matcher

        segments = [{"start": 0, "end": 10, "text": "hello"}]
        svc.record_verification_misses(
            "slug", "ep1",
            [{"sponsor": "NewSponsor", "start": 100, "end": 160}],
            segments=segments,
        )

        fake_matcher.create_pattern_from_ad.assert_called_once_with(
            segments=segments,
            start=100,
            end=160,
            sponsor="NewSponsor",
            scope="podcast",
            podcast_id="slug",
            episode_id="ep1",
        )

    def test_no_auto_create_when_segments_missing(self):
        svc = _make_service(patterns=[])
        fake_matcher = MagicMock()
        svc._text_pattern_matcher = fake_matcher

        svc.record_verification_misses(
            "slug", "ep1",
            [{"sponsor": "NewSponsor", "start": 0, "end": 60}],
        )
        fake_matcher.create_pattern_from_ad.assert_not_called()

    def test_logs_declined_when_validator_rejects(self):
        svc = _make_service(patterns=[])
        fake_matcher = MagicMock()
        fake_matcher.create_pattern_from_ad.return_value = None
        svc._text_pattern_matcher = fake_matcher

        with patch("pattern_service.logger") as mock_logger:
            svc.record_verification_misses(
                "slug", "ep1",
                [{"sponsor": "ContaminatedSponsor", "start": 0, "end": 300}],
                segments=[{"start": 0, "end": 300, "text": "..."}],
            )
            assert any(
                "Declined to auto-create" in str(c)
                for c in mock_logger.info.call_args_list
            )

    def test_zero_alias_normalized_to_xero(self):
        patterns = [{"id": 77, "sponsor": "Xero"}]
        svc = _make_service(patterns)
        fake_matcher = MagicMock()
        svc._text_pattern_matcher = fake_matcher

        svc.record_verification_misses(
            "slug", "ep1",
            [{"sponsor": "Zero", "start": 100, "end": 160}],
            segments=[{"start": 0, "end": 200, "text": "..."}],
        )
        svc.record_pattern_match.assert_called_once_with(
            77, episode_id="ep1", observed_duration=60,
        )
        fake_matcher.create_pattern_from_ad.assert_not_called()

    def test_matched_sponsor_boosts_not_auto_creates(self):
        patterns = [{"id": 42, "sponsor": "Acme"}]
        svc = _make_service(patterns)
        fake_matcher = MagicMock()
        svc._text_pattern_matcher = fake_matcher

        svc.record_verification_misses(
            "slug", "ep1",
            [{"sponsor": "Acme", "start": 100, "end": 160}],
            segments=[{"start": 0, "end": 200, "text": "..."}],
        )
        svc.record_pattern_match.assert_called_once_with(
            42, episode_id="ep1", observed_duration=60,
        )
        fake_matcher.create_pattern_from_ad.assert_not_called()

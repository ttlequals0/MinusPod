"""Tests for utils.rate_limit helpers."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from utils.rate_limit import parse_retry_after


class TestParseRetryAfterDeltaSeconds:
    def test_integer_string(self):
        assert parse_retry_after("7") == 7.0

    def test_float_string(self):
        assert parse_retry_after("2.5") == 2.5

    def test_zero(self):
        assert parse_retry_after("0") == 0.0

    def test_whitespace_padded(self):
        assert parse_retry_after("  3  ") == 3.0


class TestParseRetryAfterHttpDate:
    def test_future_date(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=15)
        # parsedate_to_datetime drops sub-second precision, so allow a small window
        result = parse_retry_after(format_datetime(future))
        assert result is not None
        assert 13.0 <= result <= 16.0

    def test_past_date_clamps_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert parse_retry_after(format_datetime(past)) == 0.0


class TestParseRetryAfterEdgeCases:
    @pytest.mark.parametrize("value", [None, "", "   ", "tomorrow", "not-a-date"])
    def test_none_or_unparseable_returns_none(self, value):
        assert parse_retry_after(value) is None

    def test_clamps_to_max_seconds(self):
        assert parse_retry_after("9999", max_seconds=300.0) == 300.0

    def test_clamp_respects_custom_max(self):
        assert parse_retry_after("100", max_seconds=10.0) == 10.0

    def test_negative_string_clamped_to_zero(self):
        assert parse_retry_after("-5") == 0.0

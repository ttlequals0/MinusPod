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


class TestParseGroqRateLimitBody:
    def test_dict_with_full_tpm_message(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {
            "error": {
                "message": (
                    "Rate limit reached for model `mixtral-8x7b-32768` in organization "
                    "`org_xxx` on tokens per minute (TPM): Limit 6000, Used 0, "
                    "Requested ~7500. Please try again later."
                ),
                "type": "tokens",
                "code": "rate_limit_exceeded",
            }
        }
        assert parse_groq_rate_limit_body(body) == {
            "limit": 6000, "used": 0, "requested": 7500,
        }

    def test_string_json_body(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = (
            '{"error":{"message":"tokens per minute (TPM): Limit 5000, Used 100, '
            'Requested ~9000.","type":"tokens","code":"rate_limit_exceeded"}}'
        )
        assert parse_groq_rate_limit_body(body) == {
            "limit": 5000, "used": 100, "requested": 9000,
        }

    def test_plain_text_tpm(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        msg = "TPM exceeded - Limit 5000, Used 100, Requested ~9000"
        assert parse_groq_rate_limit_body(msg) == {
            "limit": 5000, "used": 100, "requested": 9000,
        }

    def test_comma_separated_numbers(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {"error": {"message": "tokens per minute: Limit 60,000, Used 1,000, Requested ~70,500", "type": "tokens"}}
        assert parse_groq_rate_limit_body(body) == {
            "limit": 60000, "used": 1000, "requested": 70500,
        }

    def test_requests_per_minute_not_tokens(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {"error": {"message": "requests per minute (RPM): Limit 30, Requested ~40"}}
        assert parse_groq_rate_limit_body(body) is None

    def test_missing_limit(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {"error": {"message": "tokens per minute: Requested ~7000", "type": "tokens"}}
        assert parse_groq_rate_limit_body(body) is None

    def test_missing_requested(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {"error": {"message": "tokens per minute: Limit 6000, Used 500", "type": "tokens"}}
        assert parse_groq_rate_limit_body(body) is None

    @pytest.mark.parametrize("body", [None, "", "garbage", {}, {"error": {}}, 12345])
    def test_unparseable_inputs_return_none(self, body):
        from utils.rate_limit import parse_groq_rate_limit_body
        assert parse_groq_rate_limit_body(body) is None

    def test_used_can_be_zero(self):
        from utils.rate_limit import parse_groq_rate_limit_body
        body = {"error": {"message": "TPM: Limit 6000, Used 0, Requested ~7500", "type": "tokens"}}
        result = parse_groq_rate_limit_body(body)
        assert result == {"limit": 6000, "used": 0, "requested": 7500}

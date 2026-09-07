"""Tests for utils.rate_limit helpers."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from utils.rate_limit import parse_retry_after, parse_upstream_reset

from tests.unit.rate_limit_fixtures import PRODUCTION_RESET_BODY


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


class TestParseUpstreamReset:
    def test_seconds_until_reset_is_exact(self):
        assert parse_upstream_reset(PRODUCTION_RESET_BODY, max_seconds=86400) == 8129.0

    def test_json_string_body(self):
        import json
        assert parse_upstream_reset(json.dumps(PRODUCTION_RESET_BODY), max_seconds=86400) == 8129.0

    def test_list_wrapped_body(self):
        assert parse_upstream_reset([PRODUCTION_RESET_BODY], max_seconds=86400) == 8129.0

    def test_resets_at_only(self):
        future_epoch = datetime.now(timezone.utc).timestamp() + 900
        body = {"error": {"resets_at": future_epoch}}
        result = parse_upstream_reset(body, max_seconds=86400)
        assert result is not None
        assert 890.0 <= result <= 905.0

    def test_resets_at_iso_only(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=900)
        body = {"error": {"resets_at_iso": future.isoformat()}}
        result = parse_upstream_reset(body, max_seconds=86400)
        assert result is not None
        assert 890.0 <= result <= 905.0

    def test_reset_in_the_past_clamps_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        body = {"error": {"resets_at_iso": past.isoformat()}}
        result = parse_upstream_reset(body, max_seconds=86400)
        assert result == 0.0

    def test_clamps_to_max_seconds(self):
        body = {"error": {"seconds_until_reset": 99999}}
        assert parse_upstream_reset(body, max_seconds=300.0) == 300.0

    def test_no_reset_fields_returns_none(self):
        assert parse_upstream_reset({"error": {"message": "slow down"}}) is None

    @pytest.mark.parametrize("body", [None, "", "garbage", {}, 12345])
    def test_unparseable_inputs_return_none(self, body):
        assert parse_upstream_reset(body) is None

    def test_finds_reset_nested_in_openrouter_metadata_raw(self):
        """OpenRouter proxies the upstream body as a JSON string under
        error.metadata.raw; the reset fields live inside that, not at the
        top level."""
        import json
        body = {
            "error": {
                "message": "Provider returned error",
                "metadata": {"raw": json.dumps(PRODUCTION_RESET_BODY)},
            }
        }
        assert parse_upstream_reset(body, max_seconds=86400) == 8129.0

    def test_outer_reset_field_wins_over_nested_metadata_raw(self):
        """An outer error carrying its own reset must not be shadowed by an
        inner body nested under metadata.raw."""
        import json
        body = {
            "error": {
                "message": "Provider returned error",
                "seconds_until_reset": 42,
                "metadata": {"raw": json.dumps(PRODUCTION_RESET_BODY)},
            }
        }
        assert parse_upstream_reset(body, max_seconds=86400) == 42.0

    @pytest.mark.parametrize("unparseable_outer", ["", "not-a-number", None])
    def test_unparseable_outer_reset_still_descends_to_nested_metadata_raw(self, unparseable_outer):
        """An outer resets_at that fails to parse must not block descent into
        a nested, genuinely parseable metadata.raw body (regression: an empty
        string, non-numeric string, or explicit null previously counted as
        'has its own reset' and suppressed the descent)."""
        import json
        body = {
            "error": {
                "message": "Provider returned error",
                "resets_at": unparseable_outer,
                "metadata": {"raw": json.dumps(PRODUCTION_RESET_BODY)},
            }
        }
        assert parse_upstream_reset(body, max_seconds=86400) == 8129.0

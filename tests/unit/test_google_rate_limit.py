"""Tests for Gemini/Google 429 handling (issue #435).

Two distinct cases:
- per-minute 429s carry a `retryDelay` in the body -> honor it for backoff;
- free-tier daily quota exhaustion (RESOURCE_EXHAUSTED, a PerDay quota) cannot
  recover within the run -> fail fast, and never leak the raw payload to the UI.
"""
import json

from utils.rate_limit import parse_google_retry_delay, parse_google_daily_quota
from llm_client import (
    classify_daily_quota_exhaustion, extract_retry_after, StructuralRateLimitError,
)
from ad_reviewer import _review_failure_reason


# The real free-tier daily-quota 429 body from issue #435 (parsed form).
GOOGLE_DAILY_BODY = {
    "error": {
        "code": 429,
        "message": (
            "You exceeded your current quota, please check your plan and billing "
            "details. Quota exceeded for metric: "
            "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
            "limit: 20, model: gemini-3.5-flash\nPlease retry in 4.229052722s."
        ),
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                 "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 "quotaDimensions": {"location": "global", "model": "gemini-3.5-flash"},
                 "quotaValue": "20"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "4s"},
        ],
    }
}

# A per-minute 429: retryable, carries a retryDelay, must NOT read as daily.
GOOGLE_PER_MINUTE_BODY = {
    "error": {
        "code": 429,
        "message": "Quota exceeded (per minute). Please retry in 12s.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                 "quotaValue": "10"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"},
        ],
    }
}


class _FakeResponse:
    def __init__(self, text=None, headers=None):
        self.text = text
        self.headers = headers or {}


class _FakeRateLimitError(Exception):
    """Mimics a provider 429: carries .body and/or .response; reads as rate limit."""
    def __init__(self, message="rate limit 429", body=None, response=None):
        super().__init__(message)
        self.body = body
        self.response = response


class TestParseGoogleRetryDelay:
    def test_structured_retry_info(self):
        assert parse_google_retry_delay(GOOGLE_DAILY_BODY) == 4.0

    def test_message_fallback(self):
        body = {"error": {"message": "Please retry in 4.2s.", "status": "RESOURCE_EXHAUSTED"}}
        assert abs(parse_google_retry_delay(body) - 4.2) < 0.001

    def test_list_wrapped_body(self):
        assert parse_google_retry_delay([GOOGLE_DAILY_BODY]) == 4.0

    def test_json_string_body(self):
        import json
        assert parse_google_retry_delay(json.dumps(GOOGLE_PER_MINUTE_BODY)) == 12.0

    def test_clamped(self):
        body = {"error": {"details": [
            {"@type": "x/google.rpc.RetryInfo", "retryDelay": "9999s"}]}}
        assert parse_google_retry_delay(body, max_seconds=300.0) == 300.0

    def test_none_and_junk(self):
        assert parse_google_retry_delay(None) is None
        assert parse_google_retry_delay("not json, no delay") is None
        assert parse_google_retry_delay({"error": {"message": "no delay here"}}) is None


class TestParseGoogleDailyQuota:
    def test_daily_quota_detected(self):
        out = parse_google_daily_quota(GOOGLE_DAILY_BODY)
        assert out is not None
        assert out["limit"] == 20
        assert "PerDay" in out["quota_id"]

    def test_per_minute_not_daily(self):
        assert parse_google_daily_quota(GOOGLE_PER_MINUTE_BODY) is None

    def test_non_quota_429(self):
        body = {"error": {"code": 429, "message": "slow down", "status": "UNAVAILABLE"}}
        assert parse_google_daily_quota(body) is None

    def test_none(self):
        assert parse_google_daily_quota(None) is None


class TestClassifyAndRetryAfter:
    def test_classify_daily_quota_from_error_body(self):
        err = _FakeRateLimitError(body=GOOGLE_DAILY_BODY)
        out = classify_daily_quota_exhaustion(err)
        assert out is not None and out["limit"] == 20

    def test_per_minute_not_classified_daily(self):
        err = _FakeRateLimitError(body=GOOGLE_PER_MINUTE_BODY)
        assert classify_daily_quota_exhaustion(err) is None

    def test_non_rate_limit_not_classified(self):
        err = Exception("connection reset")
        assert classify_daily_quota_exhaustion(err) is None

    def test_extract_retry_after_from_body_when_no_header(self):
        err = _FakeRateLimitError(body=GOOGLE_PER_MINUTE_BODY,
                                  response=_FakeResponse(headers={}))
        assert extract_retry_after(err) == 12.0

    def test_header_wins_over_body(self):
        err = _FakeRateLimitError(body=GOOGLE_PER_MINUTE_BODY,
                                  response=_FakeResponse(headers={"Retry-After": "3"}))
        assert extract_retry_after(err) == 3.0


class TestReviewFailureReason:
    def test_rate_limit_message_no_payload(self):
        err = _FakeRateLimitError(body=GOOGLE_DAILY_BODY)
        reason = _review_failure_reason(err)
        assert "rate limit" in reason.lower()
        assert "RESOURCE_EXHAUSTED" not in reason
        assert "{" not in reason  # no raw payload

    def test_generic_failure_message(self):
        reason = _review_failure_reason(Exception("connection reset"))
        assert reason == "Review unavailable: LLM call failed"


class TestRetryLoopDailyQuotaFastFail:
    def test_daily_quota_fails_fast(self, monkeypatch):
        from utils import llm_call
        calls = {"n": 0}

        class _Client:
            def messages_create(self, **kw):
                calls["n"] += 1
                raise _FakeRateLimitError(body=GOOGLE_DAILY_BODY)

        monkeypatch.setattr(llm_call.time, "sleep", lambda s: None)

        response, last_error = llm_call.call_llm_for_window(
            llm_client=_Client(), model="gemini-3.5-flash", system_prompt="s",
            prompt="u", llm_timeout=1.0, max_retries=5, max_tokens=4096,
            slug="t", episode_id="e", window_label="w",
        )
        assert response is None and last_error is not None
        assert calls["n"] == 1  # no long-backoff retries on a dead daily quota
        assert "RESOURCE_EXHAUSTED" not in str(last_error)


class TestCodeReviewRegressions:
    """Regressions for the #435 code-review findings."""

    def test_malformed_retry_delay_does_not_crash(self):
        # A non-float token in the hint must yield None, not raise ValueError.
        assert parse_google_retry_delay("Please retry in 1.2.3s") is None
        assert parse_google_retry_delay("retry in .s") is None
        assert parse_google_retry_delay("Please retry in 4.2s") == 4.2

    def test_openrouter_nested_metadata_raw_daily_quota(self):
        # OpenRouter nests the upstream Google error as a JSON string in
        # error.metadata.raw; daily quota must still be detected.
        raw = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
            "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                "quotaValue": "50"}]}]}})
        body = {"error": {"message": "Provider returned error", "code": 429,
                          "metadata": {"raw": raw}}}
        result = parse_google_daily_quota(body)
        assert result is not None and result["limit"] == 50

    def test_non_dict_quota_dimensions_does_not_crash(self):
        body = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenPerDay", "quotaValue": "50",
                 "quotaDimensions": ["model", "gemini"]}]}]}}
        result = parse_google_daily_quota(body)
        assert result is not None and result["model"] is None

    def test_per_minute_message_naming_per_day_is_not_daily(self):
        # A per-minute 429 whose message also enumerates a per-day cap must stay
        # retryable, not be misclassified as daily exhaustion.
        body = {"error": {"status": "RESOURCE_EXHAUSTED",
                          "message": "Quota exceeded: 10 per minute (also 50 per day)"}}
        assert parse_google_daily_quota(body) is None

    def test_structural_error_surfaces_actionable_text(self):
        err = StructuralRateLimitError(
            "openrouter free-tier daily quota (limit 50) exhausted; retry tomorrow, "
            "raise the tier, or switch provider.")
        reason = _review_failure_reason(err)
        assert "daily quota" in reason and "retry tomorrow" in reason
        assert reason.startswith("Review unavailable:")

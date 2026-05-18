"""Tests for the structural rate-limit classifier and retry-loop fast-fail.

A structural 429 is one where a single request's token count exceeds the
provider's per-minute cap. Retrying it cannot succeed; the user must shrink
the window or change provider/tier. These tests verify the classifier
identifies the structural case only, and that ambiguous inputs fall back
to the existing transient retry path.
"""
import pytest

from llm_client import is_structural_rate_limit_error


class _FakeResponse:
    def __init__(self, text=None, headers=None):
        self.text = text
        self.headers = headers or {}


class _FakeError(Exception):
    """Mimics provider error: carries .body and/or .response."""
    def __init__(self, message="", body=None, response=None):
        super().__init__(message)
        self.body = body
        self.response = response


class _FakeRateLimitError(_FakeError):
    """A RateLimitError so is_rate_limit_error returns True via string-match."""
    def __init__(self, message="rate limit 429", **kw):
        super().__init__(message, **kw)


class TestStructuralClassifier:
    def test_structural_groq_body_returns_true(self):
        body = {
            "error": {
                "message": "tokens per minute (TPM): Limit 6000, Used 0, Requested ~7500",
                "type": "tokens",
                "code": "rate_limit_exceeded",
            }
        }
        err = _FakeRateLimitError(body=body)
        assert is_structural_rate_limit_error(err) is True

    def test_requested_below_limit_returns_false(self):
        body = {
            "error": {
                "message": "tokens per minute: Limit 6000, Used 5000, Requested ~3000",
                "type": "tokens",
            }
        }
        err = _FakeRateLimitError(body=body)
        assert is_structural_rate_limit_error(err) is False

    def test_requested_equals_limit_returns_false(self):
        body = {
            "error": {
                "message": "tokens per minute: Limit 6000, Used 0, Requested ~6000",
                "type": "tokens",
            }
        }
        err = _FakeRateLimitError(body=body)
        assert is_structural_rate_limit_error(err) is False

    def test_requests_per_minute_returns_false(self):
        body = {
            "error": {"message": "requests per minute (RPM): Limit 30, Requested ~40"}
        }
        err = _FakeRateLimitError(body=body)
        assert is_structural_rate_limit_error(err) is False

    def test_unparseable_body_returns_false(self):
        err = _FakeRateLimitError(body="garbage that does not match")
        assert is_structural_rate_limit_error(err) is False

    def test_no_body_or_response_returns_false(self):
        err = _FakeRateLimitError(message="rate limit hit")
        assert is_structural_rate_limit_error(err) is False

    def test_non_rate_limit_error_returns_false(self):
        err = _FakeError(message="connection timeout")
        assert is_structural_rate_limit_error(err) is False

    def test_message_string_fallback(self):
        err = _FakeRateLimitError(
            message="429 rate limit reached on tokens per minute (TPM): "
                    "Limit 5000, Used 100, Requested ~9000",
        )
        assert is_structural_rate_limit_error(err) is True

    def test_response_text_fallback(self):
        body_text = (
            '{"error":{"message":"tokens per minute: Limit 5000, Used 0, '
            'Requested ~7000","type":"tokens","code":"rate_limit_exceeded"}}'
        )
        err = _FakeRateLimitError(response=_FakeResponse(text=body_text))
        assert is_structural_rate_limit_error(err) is True


class TestRetryLoopFastFail:
    """Integration test: the retry loop in utils.llm_call should fail fast
    on a structural 429 and never invoke the secondary per-window retry."""

    def test_structural_breaks_after_one_attempt(self, monkeypatch):
        from utils import llm_call

        call_count = {"n": 0}

        body = {
            "error": {
                "message": "tokens per minute (TPM): Limit 6000, Used 0, Requested ~7500",
                "type": "tokens",
                "code": "rate_limit_exceeded",
            }
        }

        class _Client:
            def messages_create(self, **kw):
                call_count["n"] += 1
                raise _FakeRateLimitError(body=body)

        # Suppress sleeps and webhook side-effects so the test is fast.
        monkeypatch.setattr(llm_call.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            "webhook_service.fire_structural_rate_limit_event",
            lambda *a, **kw: None,
        )

        response, last_error = llm_call.call_llm_for_window(
            llm_client=_Client(),
            model="test-model",
            system_prompt="sys",
            prompt="user",
            llm_timeout=1.0,
            max_retries=5,  # would normally allow many retries
            max_tokens=4096,
            slug="t",
            episode_id="e",
            window_label="w",
        )

        assert response is None
        assert last_error is not None
        assert "exceeds" in str(last_error)
        assert "window" in str(last_error).lower()
        # Should be one call, not max_retries+1 (=6) and not +2 fallback (=8).
        assert call_count["n"] == 1

    def test_transient_429_still_retries(self, monkeypatch):
        from utils import llm_call

        call_count = {"n": 0}

        # Transient: requested < limit, so NOT structural -- normal retry path.
        body = {
            "error": {
                "message": "tokens per minute: Limit 6000, Used 5500, Requested ~600",
                "type": "tokens",
            }
        }

        class _Client:
            def messages_create(self, **kw):
                call_count["n"] += 1
                raise _FakeRateLimitError(body=body)

        monkeypatch.setattr(llm_call.time, "sleep", lambda s: None)
        monkeypatch.setattr(llm_call.random, "uniform", lambda a, b: 0.0)

        response, last_error = llm_call.call_llm_for_window(
            llm_client=_Client(),
            model="test-model",
            system_prompt="sys",
            prompt="user",
            llm_timeout=1.0,
            max_retries=2,
            max_tokens=4096,
            slug="t",
            episode_id="e",
            window_label="w",
        )

        # Transient path: max_retries+1 (=3) main attempts + 2 fallback = 5.
        # Confirms structural fast-fail did NOT kick in.
        assert response is None
        assert call_count["n"] >= 3

"""Tests for the spend/quota limit classifier and its webhook routing.

A limit-exceeded error means the provider rejected the request because a
billing or usage limit is exhausted (OpenRouter monthly key limit, out of
credits, OpenAI insufficient_quota). These must fire the Limit Exceeded
webhook instead of Auth Failure, classify as non-retryable, and must not
shadow the transient-429 retry path or the Gemini daily/structural
classifiers (#491).
"""

import json
import threading
from unittest.mock import patch

import httpx

from llm_client import (
    is_limit_exceeded_error,
    is_auth_error,
    is_retryable_error,
    StructuralRateLimitError,
)
from tests.unit.provider_error_fakes import FakeResponse, FakeProviderError, call_window


OPENROUTER_KEY_LIMIT_403 = (
    "Error code: 403 - {'error': {'message': 'Key limit exceeded "
    "(monthly limit). Manage it using https://openrouter.ai/keys', "
    "'code': 403}}"
)

# Google's transient per-minute 429 message mentions both "quota" and
# "billing"; it must stay on the retry path.
GEMINI_PER_MINUTE_429 = (
    "429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check "
    "your plan and billing details."
)

INSUFFICIENT_QUOTA_BODY = {
    "error": {"message": "You exceeded your current quota",
              "type": "insufficient_quota",
              "code": "insufficient_quota"}
}


class TestLimitExceededClassifier:
    def test_openrouter_monthly_key_limit_403(self):
        err = FakeProviderError(OPENROUTER_KEY_LIMIT_403, status_code=403)
        assert is_limit_exceeded_error(err) is True

    def test_402_always_limit_exceeded(self):
        err = FakeProviderError("Insufficient credits", status_code=402)
        assert is_limit_exceeded_error(err) is True

    def test_openai_insufficient_quota_429(self):
        err = FakeProviderError("Error code: 429", status_code=429,
                                body=INSUFFICIENT_QUOTA_BODY)
        assert is_limit_exceeded_error(err) is True

    def test_anthropic_low_credit_400(self):
        err = FakeProviderError(
            "Your credit balance is too low to access the Anthropic API",
            status_code=400,
        )
        assert is_limit_exceeded_error(err) is True

    def test_invalid_key_401_not_limit(self):
        err = FakeProviderError("Invalid API key provided", status_code=401)
        assert is_limit_exceeded_error(err) is False

    def test_invalid_key_403_not_limit(self):
        err = FakeProviderError("Forbidden: key disabled", status_code=403)
        assert is_limit_exceeded_error(err) is False

    def test_generic_429_not_limit(self):
        err = FakeProviderError("Rate limit exceeded, retry in 20s", status_code=429)
        assert is_limit_exceeded_error(err) is False

    def test_gemini_per_minute_429_not_limit(self):
        err = FakeProviderError(GEMINI_PER_MINUTE_429, status_code=429)
        assert is_limit_exceeded_error(err) is False

    def test_no_status_code_not_limit(self):
        err = FakeProviderError("Key limit exceeded (monthly limit)")
        assert is_limit_exceeded_error(err) is False

    def test_500_not_limit(self):
        err = FakeProviderError("billing service unavailable", status_code=500)
        assert is_limit_exceeded_error(err) is False

    def test_response_text_fallback(self):
        text = json.dumps({"error": {"message": "Key limit exceeded (monthly limit)"}})
        err = FakeProviderError("Error code: 403", status_code=403,
                                response=FakeResponse(text=text))
        assert is_limit_exceeded_error(err) is True


class TestClassifierInteractions:
    """Limit-exceeded errors must read as non-retryable and non-auth
    everywhere, not only inside the retry loop's branch ordering."""

    def test_limit_exceeded_is_not_retryable(self):
        # Without the is_retryable_error exclusion, the "429" in the message
        # would match the string fallback and keep the error retryable.
        err = FakeProviderError("Error code: 429", status_code=429,
                                body=INSUFFICIENT_QUOTA_BODY)
        assert is_retryable_error(err) is False

    def test_billing_403_is_not_auth_error(self):
        import openai
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        err = openai.PermissionDeniedError(
            "Key limit exceeded (monthly limit)",
            response=httpx.Response(403, request=request),
            body={"error": {"message": "Key limit exceeded (monthly limit)"}},
        )
        assert is_limit_exceeded_error(err) is True
        assert is_auth_error(err) is False


class TestRetryLoopRouting:
    """The retry loop must fire Limit Exceeded (not Auth Failure) for
    billing errors, fail fast, and leave the auth and Gemini paths alone."""

    def _run(self, error, monkeypatch, max_retries=5):
        from utils import llm_call

        calls = {"n": 0}
        fired = {"limit": 0, "auth": 0}

        class _Client:
            def messages_create(self, **kw):
                calls["n"] += 1
                raise error

        monkeypatch.setattr(llm_call.time, "sleep", lambda s: None)
        monkeypatch.setattr(llm_call.random, "uniform", lambda a, b: 0.0)
        monkeypatch.setattr(
            "webhook_service.fire_limit_exceeded_event",
            lambda *a, **kw: fired.__setitem__("limit", fired["limit"] + 1),
        )
        monkeypatch.setattr(
            "webhook_service.fire_auth_failure_event",
            lambda *a, **kw: fired.__setitem__("auth", fired["auth"] + 1),
        )
        response, last_error = call_window(_Client(), max_retries=max_retries)
        return response, last_error, calls["n"], fired

    def test_key_limit_403_fires_limit_not_auth(self, monkeypatch):
        err = FakeProviderError(OPENROUTER_KEY_LIMIT_403, status_code=403)
        response, last_error, n, fired = self._run(err, monkeypatch)
        assert response is None
        assert last_error is err
        assert n == 1
        assert fired == {"limit": 1, "auth": 0}

    def test_insufficient_quota_429_fast_fails(self, monkeypatch):
        err = FakeProviderError("Error code: 429", status_code=429,
                                body=INSUFFICIENT_QUOTA_BODY)
        # One call only: no in-loop retries and no secondary retry pass.
        response, last_error, n, fired = self._run(err, monkeypatch)
        assert response is None
        assert n == 1
        assert fired == {"limit": 1, "auth": 0}

    def test_invalid_key_401_still_fires_auth(self, monkeypatch):
        import openai
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        err = openai.AuthenticationError(
            "Invalid API key provided",
            response=httpx.Response(401, request=request),
            body={"error": {"message": "Invalid API key provided"}},
        )
        response, last_error, n, fired = self._run(err, monkeypatch)
        assert response is None
        assert n == 1
        assert fired == {"limit": 0, "auth": 1}

    def test_gemini_daily_quota_still_routes_structural(self, monkeypatch):
        body = {
            "error": {
                "message": "You exceeded your current quota (429 rate limit)",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaValue": "50",
                        "quotaDimensions": {"model": "gemini-2.5-pro"},
                    }],
                }],
            }
        }
        err = FakeProviderError("429 rate limit", status_code=429, body=body)
        response, last_error, n, fired = self._run(err, monkeypatch)
        assert response is None
        assert isinstance(last_error, StructuralRateLimitError)
        assert n == 1
        assert fired == {"limit": 0, "auth": 0}


class TestFireLimitExceededEvent:
    def setup_method(self):
        import webhook_service
        webhook_service._last_alert_time.clear()

    def _fire(self, webhooks):
        import webhook_service

        dispatched = []

        def _record(wh, context):
            dispatched.append((wh, context))

        class _SyncThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        with patch('webhook_service.load_webhooks', return_value=webhooks), \
             patch('webhook_service._prepare_and_dispatch', side_effect=_record), \
             patch.object(threading, 'Thread', _SyncThread):
            webhook_service.fire_limit_exceeded_event(
                'openrouter', 'test-model',
                'Key limit exceeded (monthly limit)', 403,
            )
        return dispatched

    def test_event_in_valid_events(self):
        from webhook_service import VALID_EVENTS, EVENT_LIMIT_EXCEEDED
        assert EVENT_LIMIT_EXCEEDED in VALID_EVENTS

    def test_dispatches_to_subscriber_with_context(self):
        webhooks = [{'enabled': True, 'events': ['Limit Exceeded'], 'url': 'http://x'}]
        dispatched = self._fire(webhooks)
        assert len(dispatched) == 1
        context = dispatched[0][1]
        assert context['event'] == 'Limit Exceeded'
        assert context['provider'] == 'openrouter'
        assert context['model'] == 'test-model'
        assert context['error_message'] == 'Key limit exceeded (monthly limit)'
        assert context['status_code'] == 403
        assert 'timestamp' in context

    def test_skips_non_subscribers(self):
        webhooks = [
            {'enabled': True, 'events': ['Auth Failure'], 'url': 'http://x'},
            {'enabled': False, 'events': ['Limit Exceeded'], 'url': 'http://y'},
        ]
        assert self._fire(webhooks) == []

    def test_dedup_window_suppresses_second_fire(self):
        webhooks = [{'enabled': True, 'events': ['Limit Exceeded'], 'url': 'http://x'}]
        assert len(self._fire(webhooks)) == 1
        # Second fire inside the 5-minute window is suppressed.
        assert self._fire(webhooks) == []

    def test_dedup_is_per_event(self):
        # A Limit Exceeded fire must not suppress a later Auth Failure fire.
        import webhook_service
        webhooks = [{'enabled': True,
                     'events': ['Limit Exceeded', 'Auth Failure'],
                     'url': 'http://x'}]
        assert len(self._fire(webhooks)) == 1

        dispatched = []

        class _SyncThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        with patch('webhook_service.load_webhooks', return_value=webhooks), \
             patch('webhook_service._prepare_and_dispatch',
                   side_effect=lambda wh, ctx: dispatched.append(ctx)), \
             patch.object(threading, 'Thread', _SyncThread):
            webhook_service.fire_auth_failure_event(
                'openai', 'test-model', 'Invalid API key', 401,
            )
        assert len(dispatched) == 1
        assert dispatched[0]['event'] == 'Auth Failure'

"""Auth-class LLM failures must not consume the episode retry budget.

A claude-wrapper auth outage (401 body containing claude_cli_not_authenticated)
is operator-fixable and can outlast any retry ladder, so it must not increment
retry_count or trigger the permanently_failed transition.
"""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('auth_error_retry_budget_test_')
from config import MAX_EPISODE_RETRIES
from llm_client import is_auth_error
from main_app import db
from main_app.processing import _handle_processing_failure
from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


def test_wrapper_401_is_auth():
    assert is_auth_error(Exception(
        "Error code: 401 - {'error': {'code': 'claude_cli_not_authenticated'}}"))


def test_plain_401_is_auth():
    assert is_auth_error(Exception("401 authentication_error: invalid api key"))


def test_rate_limit_is_not_auth():
    assert not is_auth_error(Exception("429 rate limit exceeded"))


def test_processing_duration_digits_are_not_auth():
    assert not is_auth_error(Exception("processing took 40100ms"))


def test_wrapped_billing_401_is_not_auth():
    assert not is_auth_error(Exception("error code: 401 - billing limit reached"))


def test_wrapped_invalid_key_401_is_auth():
    assert is_auth_error(Exception("error code: 401 - invalid api key"))


def test_wrapped_invalid_key_403_is_auth():
    assert is_auth_error(Exception("error code: 403 - invalid api key"))


def test_wrapped_billing_403_is_not_auth():
    assert not is_auth_error(Exception("error code: 403 - billing hard limit reached"))


class TestBreakerMaskedAuthError:
    """A wrapper auth outage can trip the breaker before is_auth_error ever
    sees the original 401; the resulting CircuitBreakerOpen must still carry
    the auth marker so the retry-budget freeze isn't bypassed."""

    def test_breaker_opened_by_auth_error_is_detected_as_auth(self):
        cb = CircuitBreaker("test-auth-mask", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception(
            "Error code: 401 - {'error': {'code': 'claude_cli_not_authenticated'}}"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert is_auth_error(exc_info.value)

    def test_breaker_opened_by_generic_500_is_not_auth(self):
        cb = CircuitBreaker("test-generic-mask", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("Error code: 500 - internal server error"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert not is_auth_error(exc_info.value)

    def test_cause_cleared_after_recovery_stops_masking_as_auth(self):
        cb = CircuitBreaker("test-recover-mask", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception(
            "Error code: 401 - {'error': {'code': 'claude_cli_not_authenticated'}}"))
        cb.record_success()  # recovery probe succeeded, breaker closes
        cb.record_failure(Exception("Error code: 500 - internal server error"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert not is_auth_error(exc_info.value)


class TestBreakerMaskedAuthErrorSurvivesTruncation:
    """The ~200-char truncated `cause` embedded in the CircuitBreakerOpen
    message can drop the auth marker from a verbose 401 body. Wiring
    is_auth_error in as the breaker's cause_classifier (as production does
    for _llm_circuit_breaker) classifies from the full trigger text instead,
    so truncation cannot hide the outage."""

    def _breaker(self, name):
        return CircuitBreaker(name, failure_threshold=1, recovery_timeout=60,
                               cause_classifier=is_auth_error)

    def test_verbose_401_beyond_truncation_still_classifies_as_auth(self):
        cb = self._breaker("test-verbose-401")
        verbose_body = (
            "Error code: 401 - " + ("padding " * 30) +
            "{'error': {'code': 'claude_cli_not_authenticated'}}")
        cb.record_failure(Exception(verbose_body))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        # The truncated message alone no longer proves auth (marker cut off).
        assert 'claude_cli_not_authenticated' not in str(exc_info.value)
        # But the attribute, classified from the full text, still does.
        assert exc_info.value.auth_cause is True
        assert is_auth_error(exc_info.value)

    def test_verbose_403_beyond_truncation_still_classifies_as_auth(self):
        cb = self._breaker("test-verbose-403")
        verbose_body = "Error code: 403 - " + ("padding " * 30) + "invalid api key"
        cb.record_failure(Exception(verbose_body))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is True
        assert is_auth_error(exc_info.value)

    def test_billing_403_does_not_classify_as_auth(self):
        cb = self._breaker("test-billing-403")
        cb.record_failure(Exception("Error code: 403 - billing hard limit reached"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False
        assert not is_auth_error(exc_info.value)

    def test_auth_cause_attribute_cleared_after_close(self):
        cb = self._breaker("test-attr-clear")
        cb.record_failure(Exception(
            "Error code: 401 - {'error': {'code': 'claude_cli_not_authenticated'}}"))
        cb.record_success()  # breaker closes
        cb.record_failure(Exception("Error code: 500 - internal server error"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False
        assert not is_auth_error(exc_info.value)


SLUG = 'auth-error-retry-budget-feed'
AUTH_ERROR = Exception(
    "Ad detection failed: All 5 detection windows failed (last error: "
    "Error code: 401 - {'error': {'code': 'claude_cli_not_authenticated'}})")


@pytest.fixture
def seeded_episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Auth Retry Budget Test')
    db.upsert_episode(SLUG, 'ep-1', title='Episode 1', status='processing',
                      original_url='https://example.com/ep1.mp3',
                      retry_count=MAX_EPISODE_RETRIES - 1)
    yield 'ep-1'
    db.delete_podcast(SLUG)


def _fail(episode_id, error):
    episode_data = db.get_episode(SLUG, episode_id)
    with patch('main_app.processing.status_service'):
        _handle_processing_failure(SLUG, episode_id, 'Episode 1', 'Auth Retry Budget Test',
                                   episode_data, error, start_time=0.0)


class TestAuthOutageRetryBudget:
    def test_auth_outage_does_not_increment_retry_count(self, seeded_episode):
        _fail(seeded_episode, AUTH_ERROR)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['retry_count'] == MAX_EPISODE_RETRIES - 1  # unchanged
        assert episode['status'] == 'failed'
        assert 'claude_cli_not_authenticated' in episode['error_message']

    def test_auth_outage_does_not_trigger_permanent_failure_at_ladder_ceiling(self, seeded_episode):
        # retry_count already one below MAX_EPISODE_RETRIES: a normal
        # transient failure here would tip into permanently_failed.
        _fail(seeded_episode, AUTH_ERROR)
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] != 'permanently_failed'

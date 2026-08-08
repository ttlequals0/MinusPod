"""Tests for the circuit breaker utility."""
import pytest
from unittest.mock import patch

from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

# Mutable time for deterministic tests without sleeping
_mock_time = 0.0


def _get_mock_time():
    return _mock_time


def _advance_time(seconds):
    global _mock_time
    _mock_time += seconds


@pytest.fixture(autouse=True)
def reset_mock_time():
    global _mock_time
    _mock_time = 0.0


class TestCircuitBreakerStates:
    """Test circuit breaker state transitions."""

    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        assert cb.state == CircuitBreaker.CLOSED

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED

    def test_check_raises_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert "test" in str(exc_info.value)
        assert exc_info.value.seconds_until_retry > 0

    def test_check_passes_when_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        cb.check()  # Should not raise

    @patch('utils.circuit_breaker.time.time', side_effect=_get_mock_time)
    def test_transitions_to_half_open_after_timeout(self, mock_time):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        _advance_time(61)
        assert cb.state == CircuitBreaker.HALF_OPEN

    @patch('utils.circuit_breaker.time.time', side_effect=_get_mock_time)
    def test_half_open_success_closes(self, mock_time):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        _advance_time(61)
        assert cb.state == CircuitBreaker.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED

    @patch('utils.circuit_breaker.time.time', side_effect=_get_mock_time)
    def test_half_open_failure_reopens(self, mock_time):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        _advance_time(61)
        assert cb.state == CircuitBreaker.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_reset(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        cb.check()  # Should not raise


class TestCircuitBreakerCheck:
    """Test the check method behavior."""

    @patch('utils.circuit_breaker.time.time', side_effect=_get_mock_time)
    def test_check_allows_half_open_probe(self, mock_time):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        _advance_time(61)
        # Should not raise - allows one probe in half_open
        cb.check()

    def test_exception_includes_name(self):
        cb = CircuitBreaker("my-service", failure_threshold=1, recovery_timeout=30)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.name == "my-service"


class TestCircuitBreakerCause:
    """The triggering failure's text should ride along on the open exception."""

    def test_cause_included_in_open_exception(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("Error code: 401 - claude_cli_not_authenticated"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert "claude_cli_not_authenticated" in str(exc_info.value)
        assert exc_info.value.cause == "Error code: 401 - claude_cli_not_authenticated"

    def test_no_cause_when_failure_recorded_without_error(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.cause is None
        assert "opened by" not in str(exc_info.value)

    def test_cause_truncated_to_200_chars(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("x" * 300))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert len(exc_info.value.cause) == 203  # 200 chars + '...'
        assert exc_info.value.cause == "x" * 200 + "..."

    def test_cause_cleared_on_record_success(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("original failure"))
        cb.record_success()
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.cause is None
        assert "original failure" not in str(exc_info.value)

    def test_cause_cleared_on_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("original failure"))
        cb.reset()
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.cause is None
        assert "original failure" not in str(exc_info.value)


class TestCircuitBreakerCauseClassifier:
    """cause_classifier runs on the full, untruncated trigger error, so its
    verdict (CircuitBreakerOpen.auth_cause) does not depend on the truncated
    `cause` text embedded in the exception message."""

    def test_classifier_runs_on_full_untruncated_error(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60,
                             cause_classifier=lambda e: 'marker' in str(e))
        long_text = ("x" * 250) + "marker"
        cb.record_failure(Exception(long_text))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        # The truncated message drops the marker...
        assert "marker" not in str(exc_info.value)
        # ...but the classifier still saw it and stamped the verdict.
        assert exc_info.value.auth_cause is True

    def test_classifier_false_verdict_carried(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60,
                             cause_classifier=lambda e: 'marker' in str(e))
        cb.record_failure(Exception("unrelated failure"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False

    def test_no_classifier_defaults_to_false(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("Error code: 401 - claude_cli_not_authenticated"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False

    def test_auth_cause_cleared_on_record_success(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60,
                             cause_classifier=lambda e: 'marker' in str(e))
        cb.record_failure(Exception("marker present"))
        cb.record_success()
        cb.record_failure(Exception("unrelated failure"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False

    def test_auth_cause_cleared_on_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60,
                             cause_classifier=lambda e: 'marker' in str(e))
        cb.record_failure(Exception("marker present"))
        cb.reset()
        cb.record_failure(Exception("unrelated failure"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.auth_cause is False

    @patch('utils.circuit_breaker.time.time', side_effect=_get_mock_time)
    def test_cause_updated_on_half_open_probe_failure(self, mock_time):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure(Exception("first failure"))
        _advance_time(61)
        assert cb.state == CircuitBreaker.HALF_OPEN
        cb.record_failure(Exception("probe failure"))
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.check()
        assert exc_info.value.cause == "probe failure"

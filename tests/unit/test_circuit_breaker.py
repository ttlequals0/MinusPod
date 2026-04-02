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

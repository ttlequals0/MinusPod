"""Circuit breaker pattern for external service calls.

Prevents cascading failures by short-circuiting after consecutive failures,
allowing the service time to recover before retrying.

States:
    CLOSED  - Normal operation, requests pass through
    OPEN    - Service is down, requests fail immediately for `recovery_timeout` seconds
    HALF_OPEN - After recovery timeout, one probe request is allowed through
"""
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Cap on the triggering-failure text embedded in CircuitBreakerOpen messages.
_CAUSE_MAX_LEN = 200


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and the call is rejected."""

    def __init__(self, name: str, seconds_until_retry: float, cause: Optional[str] = None,
                 auth_cause: bool = False):
        self.name = name
        self.seconds_until_retry = seconds_until_retry
        self.cause = cause
        # Classified from the full untruncated trigger error at open time
        # (see CircuitBreaker.record_failure), not from `cause` above, which
        # is truncated for log readability and may have dropped the marker
        # that a classifier like is_auth_error looks for.
        self.auth_cause = auth_cause
        message = f"Circuit breaker '{name}' is open, retry in {seconds_until_retry:.0f}s"
        if cause:
            message += f" (opened by: {cause})"
        super().__init__(message)


class CircuitBreaker:
    """Simple circuit breaker for external service calls.

    Usage:
        breaker = CircuitBreaker("llm-api")

        breaker.check()  # raises CircuitBreakerOpen if open
        try:
            result = call_external_service()
            breaker.record_success()
        except Exception:
            # Don't call record_failure on HTTP 429 / rate-limit errors --
            # throttling is back-pressure, not an outage. See record_failure.
            breaker.record_failure()
            raise
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_timeout: int = 60,
                 cause_classifier: Optional[Callable[[Exception], bool]] = None):
        """
        Args:
            name: Identifier for this circuit (used in logging)
            failure_threshold: Consecutive failures before opening the circuit
            recovery_timeout: Seconds to wait before allowing a probe request
            cause_classifier: optional predicate run on the full trigger
                exception when the breaker opens; its result is exposed as
                CircuitBreakerOpen.auth_cause so callers can classify a
                breaker-masked failure without depending on the truncated
                message text.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._cause_classifier = cause_classifier

        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._cause: Optional[str] = None
        self._auth_cause = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._evaluate_state()

    def _evaluate_state(self) -> str:
        """Evaluate current state, transitioning OPEN->HALF_OPEN if timeout elapsed.

        Must be called with self._lock held.
        """
        if self._state == self.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = self.HALF_OPEN
                logger.info(f"Circuit breaker '{self.name}': OPEN -> HALF_OPEN (recovery timeout elapsed)")
        return self._state

    def check(self):
        """Check if requests are allowed. Raises CircuitBreakerOpen if not."""
        with self._lock:
            current_state = self._evaluate_state()
            if current_state == self.OPEN:
                seconds_left = self.recovery_timeout - (time.time() - self._last_failure_time)
                raise CircuitBreakerOpen(self.name, max(0, seconds_left),
                                          cause=self._cause, auth_cause=self._auth_cause)

    def record_success(self):
        """Record a successful call. Resets the circuit to CLOSED."""
        with self._lock:
            if self._state in (self.HALF_OPEN, self.OPEN):
                logger.info(f"Circuit breaker '{self.name}': {self._state} -> CLOSED (success)")
            self._state = self.CLOSED
            self._failure_count = 0
            self._cause = None
            self._auth_cause = False

    def record_failure(self, error: Optional[Exception] = None):
        """Record a failed call. Opens the circuit after threshold failures.

        Callers must NOT invoke this for HTTP 429 / rate-limit errors --
        throttling is the provider asking us to slow down, not a provider
        outage, and counting it would open the breaker during normal free-tier
        use.

        error: triggering exception; its text becomes the open-circuit cause
        so a later CircuitBreakerOpen still surfaces it (e.g. an auth outage).
        """
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._cause = self._format_cause(error)
                self._auth_cause = self._classify(error)
                logger.warning(
                    f"Circuit breaker '{self.name}': HALF_OPEN -> OPEN "
                    f"(probe failed, retry in {self.recovery_timeout}s)"
                )
            elif self._failure_count >= self.failure_threshold:
                if self._state != self.OPEN:
                    logger.warning(
                        f"Circuit breaker '{self.name}': CLOSED -> OPEN "
                        f"({self._failure_count} consecutive failures, "
                        f"retry in {self.recovery_timeout}s)"
                    )
                self._state = self.OPEN
                self._cause = self._format_cause(error)
                self._auth_cause = self._classify(error)

    @staticmethod
    def _format_cause(error: Optional[Exception]) -> Optional[str]:
        if error is None:
            return None
        text = str(error)
        if len(text) > _CAUSE_MAX_LEN:
            text = text[:_CAUSE_MAX_LEN] + '...'
        return text

    def _classify(self, error: Optional[Exception]) -> bool:
        """Run cause_classifier on the full, untruncated trigger error."""
        if self._cause_classifier is None or error is None:
            return False
        return bool(self._cause_classifier(error))

    def reset(self):
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0
            self._cause = None
            self._auth_cause = False

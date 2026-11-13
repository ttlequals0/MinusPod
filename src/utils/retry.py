"""Shared retry/backoff utilities."""
import random


def calculate_backoff(
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    exponential_base: int = 2,
    jitter: bool = True,
) -> float:
    """Return the exponential backoff delay in seconds.

    attempt is zero-based; base_delay and max_delay are in seconds; jitter scales
    the delay to 50-150% to avoid a thundering herd.
    """
    delay = min(base_delay * (exponential_base ** attempt), max_delay)
    if jitter:
        delay = delay * (0.5 + random.random())
    return delay

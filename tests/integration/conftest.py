"""Shared fixtures for integration tests."""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear in-memory limiter counters before each test."""
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        # Limiter may not be initialised yet when the first test of a
        # module runs (Flask app import is lazy). Safe to skip.
        pass
    yield

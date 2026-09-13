"""Shared rate-limit 429 body fixture, used by tests across the retry-after,
upstream-reset, and rate-limit-hold suites.

A generic provider's 429 body: a one-hour Retry-After header sits next to a
body carrying the true, farther-out reset (issue: the header alone caused
the queue hold to release an hour early, #696).
"""
PRODUCTION_RESET_BODY = {
    "error": {
        "message": "Upstream rate limit exceeded",
        "type": "upstream_api_error",
        "code": "assistant_rate_limit",
        "rate_limit_type": "session_limit",
        "resets_at": 1788804000,
        "resets_at_iso": "2026-09-07T18:00:00+00:00",
        "seconds_until_reset": 8129,
    }
}

import os

import pytest
from limits import RateLimitItemPerMinute
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter

from utils.ratelimit_storage import ensure_storage_available


def test_unreachable_external_storage_fails_closed():
    storage = storage_from_string('redis://127.0.0.1:1')
    with pytest.raises(RuntimeError, match='backend is unreachable'):
        ensure_storage_available('redis://127.0.0.1:1', storage)


def test_redis_counter_is_shared_between_instances():
    uri = os.environ.get('MINUSPOD_TEST_REDIS_URI')
    if not uri:
        pytest.skip('MINUSPOD_TEST_REDIS_URI is not set')
    first = FixedWindowRateLimiter(storage_from_string(uri))
    second = FixedWindowRateLimiter(storage_from_string(uri))
    item = RateLimitItemPerMinute(1)
    assert first.hit(item, 'shared-worker-test') is True
    assert second.hit(item, 'shared-worker-test') is False


import pytest
from limits.storage import storage_from_string

from utils.ratelimit_storage import ensure_storage_available


def test_unreachable_external_storage_fails_closed():
    storage = storage_from_string('redis://127.0.0.1:1')
    with pytest.raises(RuntimeError, match='backend is unreachable'):
        ensure_storage_available('redis://127.0.0.1:1', storage)

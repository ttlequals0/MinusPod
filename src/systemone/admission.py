"""Fair per-process admission for complete System One operations."""
import hmac
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit


class LocalOperationCapacityTimeout(RuntimeError):
    reason = 'local_operation_capacity_timeout'
    stage = 'admission'

    def __init__(self):
        super().__init__('Local System One operation capacity was unavailable before the deadline')


class _Pool:
    def __init__(self):
        self.condition = threading.Condition()
        self.active = {}
        self.queued = deque()


_pools = {}
_pools_lock = threading.Lock()
_fingerprint_key = secrets.token_bytes(32)


def _pool_for(endpoint, api_key):
    parts = urlsplit(endpoint)
    host = (parts.hostname or '').lower()
    if ':' in host:
        host = f'[{host}]'
    port = parts.port
    if port is not None and (parts.scheme.lower(), port) not in {('https', 443), ('http', 80)}:
        host = f'{host}:{port}'
    normalized = urlunsplit((parts.scheme.lower(), host, parts.path.rstrip('/'), parts.query, ''))
    fingerprint = hmac.digest(_fingerprint_key, (api_key or '').encode(), 'sha256')
    with _pools_lock:
        return _pools.setdefault((normalized, fingerprint), _Pool())


@contextmanager
def operation_admission(endpoint, api_key, limit, *, deadline_at, check=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('maxConcurrentOperations must be a positive integer')
    pool = _pool_for(endpoint, api_key)
    ticket = object()
    admitted = False
    waited = False
    with pool.condition:
        pool.queued.append((ticket, limit))
        try:
            while True:
                if check is not None:
                    check(waited)
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    raise LocalOperationCapacityTimeout()
                effective_limit = min([*pool.active.values(), *(item[1] for item in pool.queued)])
                if pool.queued[0][0] is ticket and len(pool.active) < effective_limit:
                    pool.queued.popleft()
                    pool.active[ticket] = limit
                    admitted = True
                    pool.condition.notify_all()
                    break
                waited = True
                pool.condition.wait(min(remaining, 0.1))
        except BaseException:
            pool.queued.remove((ticket, limit))
            pool.condition.notify_all()
            raise
    try:
        yield
    finally:
        if admitted:
            with pool.condition:
                del pool.active[ticket]
                pool.condition.notify_all()

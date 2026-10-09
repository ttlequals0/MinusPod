"""Whole-operation capacity is shared, fair, and cancellable."""
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from systemone import admission
from systemone.admission import LocalOperationCapacityTimeout, operation_admission


def test_pool_identity_does_not_store_raw_credentials_or_an_unkeyed_digest():
    pool = admission._pool_for('https://example.com/fingerprint', 'first')
    identity = next(key for key, value in admission._pools.items() if value is pool)
    assert 'first' not in identity
    assert identity[1] != b'first'
    assert identity[1] != hashlib.sha256(b'first').digest()


def test_normalized_endpoint_credentials_share_capacity_and_other_keys_do_not():
    with operation_admission('https://EXAMPLE.com:443/systemone/', 'first', 1,
                             deadline_at=time.monotonic() + 2):
        with operation_admission('https://example.com/systemone', 'second', 1,
                                 deadline_at=time.monotonic() + 2):
            pass
        with pytest.raises(LocalOperationCapacityTimeout):
            with operation_admission('https://example.com/systemone', 'first', 4,
                                     deadline_at=time.monotonic() + 0.02):
                raise AssertionError('same identity must share the active limit')
    with operation_admission('https://example.com/systemone', 'first', 1,
                             deadline_at=time.monotonic() + 2):
        pass


def test_cancelled_stricter_waiter_wakes_next_eligible_operation():
    queued = threading.Event()
    cancel = threading.Event()
    entered = threading.Event()

    def strict_check(waited):
        if cancel.is_set():
            raise RuntimeError('cancelled')
        if waited:
            queued.set()

    def strict():
        with operation_admission('https://example.com/cancel', 'first', 1,
                                 deadline_at=time.monotonic() + 3, check=strict_check):
            raise AssertionError('strict waiter must remain queued')

    def relaxed():
        with operation_admission('https://example.com/cancel', 'first', 3,
                                 deadline_at=time.monotonic() + 3):
            entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        with operation_admission('https://example.com/cancel', 'first', 3,
                                 deadline_at=time.monotonic() + 3):
            strict_future = executor.submit(strict)
            assert queued.wait(1)
            relaxed_future = executor.submit(relaxed)
            assert not entered.wait(0.03)
            cancel.set()
            with pytest.raises(RuntimeError, match='cancelled'):
                strict_future.result(timeout=1)
            assert entered.wait(1)
            relaxed_future.result(timeout=1)


def test_active_stricter_limit_drains_and_queue_remains_fifo():
    strict_entered = threading.Event()
    release_strict = threading.Event()
    first_queued = threading.Event()
    second_queued = threading.Event()
    order = []

    def strict():
        with operation_admission('https://example.com/drain', 'first', 1,
                                 deadline_at=time.monotonic() + 3):
            strict_entered.set()
            assert release_strict.wait(2)

    def relaxed(label, queued):
        with operation_admission('https://example.com/drain', 'first', 1,
                                 deadline_at=time.monotonic() + 3,
                                 check=lambda waited: queued.set() if waited else None):
            order.append(label)

    with ThreadPoolExecutor(max_workers=3) as executor:
        strict_future = executor.submit(strict)
        assert strict_entered.wait(1)
        first = executor.submit(relaxed, 'first', first_queued)
        assert first_queued.wait(1)
        second = executor.submit(relaxed, 'second', second_queued)
        assert second_queued.wait(1)
        assert order == []
        release_strict.set()
        strict_future.result(timeout=1)
        first.result(timeout=1)
        second.result(timeout=1)
    assert order == ['first', 'second']


def test_exception_releases_capacity_and_expired_queue_never_enters():
    with pytest.raises(ValueError, match='operation failed'):
        with operation_admission('https://example.com/errors', None, 1,
                                 deadline_at=time.monotonic() + 2):
            raise ValueError('operation failed')
    with operation_admission('https://example.com/errors', None, 1,
                             deadline_at=time.monotonic() + 2):
        pass
    with pytest.raises(LocalOperationCapacityTimeout):
        with operation_admission('https://example.com/errors', None, 1,
                                 deadline_at=time.monotonic() - 1):
            raise AssertionError('expired operation must not enter')


def test_default_four_operations_admit_four_and_queue_fifth():
    entered = threading.Barrier(5)
    release = threading.Event()
    fifth_queued = threading.Event()

    def active():
        with operation_admission('https://example.com/default', None, 4,
                                 deadline_at=time.monotonic() + 3):
            entered.wait(timeout=2)
            assert release.wait(2)

    def fifth():
        with operation_admission('https://example.com/default', None, 4,
                                 deadline_at=time.monotonic() + 0.15,
                                 check=lambda waited: fifth_queued.set() if waited else None):
            raise AssertionError('fifth operation must queue')

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(active) for _ in range(4)]
        entered.wait(timeout=2)
        waiting = executor.submit(fifth)
        assert fifth_queued.wait(1)
        with pytest.raises(LocalOperationCapacityTimeout):
            waiting.result(timeout=1)
        release.set()
        for future in futures:
            future.result(timeout=1)

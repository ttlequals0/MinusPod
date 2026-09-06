"""The rate limiter must not 500 a request when its expiry timer restarts:
limits 5.8.0's check-then-start timer restart lets two threads interleave."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from limits.storage import storage_from_string  # noqa: E402

from utils.ratelimit_storage import (  # noqa: E402
    SCHEME, STORAGE_URI, ThreadSafeMemoryStorage,
)

THREADS = 16


def _stop_timer(storage):
    """Leave the timer dead so the next incr() has to restart it."""
    storage.timer.cancel()
    storage.timer.join()
    assert not storage.timer.is_alive()


def _hammer(storage):
    """Release every thread into incr() at once; collect what they raise."""
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker(n):
        barrier.wait()
        try:
            storage.incr(f'key-{n % 3}', expiry=60)
        except Exception as exc:  # noqa: BLE001 - the point is what escapes
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_scheme_is_registered():
    assert isinstance(storage_from_string(STORAGE_URI), ThreadSafeMemoryStorage)
    assert SCHEME in STORAGE_URI


def test_concurrent_restart_does_not_raise():
    storage = ThreadSafeMemoryStorage()
    try:
        _stop_timer(storage)
        assert _hammer(storage) == []
        assert storage.timer.is_alive()
    finally:
        storage.timer.cancel()


def test_counting_still_works():
    storage = ThreadSafeMemoryStorage()
    try:
        assert storage.incr('counted', expiry=60) == 1
        assert storage.incr('counted', expiry=60) == 2
        assert storage.get('counted') == 2
    finally:
        storage.timer.cancel()


def _force_interleave(storage, monkeypatch):
    """Hold both threads inside Timer() so both assign before either starts:
    the real window is too narrow to race for reliably otherwise."""
    import limits.storage.memory as memory_mod

    gate = threading.Barrier(2)
    real_timer = memory_mod.threading.Timer

    def slow_timer(*args, **kwargs):
        timer = real_timer(*args, **kwargs)
        try:
            gate.wait(timeout=1)
        except threading.BrokenBarrierError:
            # Serialized storage never lets a second thread reach here, which
            # is the fix working; carry on so the first thread completes.
            pass
        return timer

    monkeypatch.setattr(memory_mod.threading, 'Timer', slow_timer)

    errors = []

    def worker():
        try:
            storage.incr('shared', expiry=60)
        except Exception as exc:  # noqa: BLE001 - the point is what escapes
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return errors


def test_restart_is_serialized(monkeypatch):
    """Only one thread may be restarting the timer at a time: stock storage
    lets both through, so the second start() hits an already-started object."""
    storage = ThreadSafeMemoryStorage()
    try:
        _stop_timer(storage)
        assert _force_interleave(storage, monkeypatch) == []
        assert storage.timer.is_alive()
    finally:
        storage.timer.cancel()


def test_starts_the_object_it_built(monkeypatch):
    """The started timer must be the one just constructed, not a re-read of
    self.timer, which a second thread can swap in between."""
    import limits.storage.memory as memory_mod

    built = []
    started = []
    real_timer = memory_mod.threading.Timer

    def recording_timer(*args, **kwargs):
        timer = real_timer(*args, **kwargs)
        built.append(timer)
        real_start = timer.start

        def start():
            started.append(timer)
            real_start()

        timer.start = start
        return timer

    storage = ThreadSafeMemoryStorage()
    try:
        _stop_timer(storage)
        monkeypatch.setattr(memory_mod.threading, 'Timer', recording_timer)
        storage.incr('single', expiry=60)
        assert len(built) == 1
        assert started == built
        assert storage.timer is built[0]
    finally:
        storage.timer.cancel()

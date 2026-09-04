"""Thread-safe in-memory rate-limit storage.

limits 5.8.0 reschedules its expiry timer with a check-then-start that two
request threads can interleave:

    if not self.timer.is_alive():
        self.timer = threading.Timer(...)
        self.timer.start()

Both threads see a dead timer, both assign ``self.timer``, then both call
start() on whichever object landed last, and the loser raises RuntimeError
into the request as a 500. gunicorn runs 8 threads per worker here, so it
surfaced roughly twice a week. Serializing the restart closes the window and
keeps the single-container default working without a Redis dependency.
"""
import threading

from limits.storage import MemoryStorage

# Registered by the StorageRegistry metaclass on class creation, so
# storage_from_string("memory-threadsafe://") resolves to this class.
SCHEME = 'memory-threadsafe'
STORAGE_URI = f'{SCHEME}://'


class ThreadSafeMemoryStorage(MemoryStorage):
    """MemoryStorage whose expiry-timer restart cannot double-start a thread."""

    STORAGE_SCHEME = [SCHEME]

    def __init__(self, uri=None, wrap_exceptions=False, **options):
        self._timer_lock = threading.Lock()
        super().__init__(uri, wrap_exceptions=wrap_exceptions, **options)

    # Name mangling in the base class compiles the call site to
    # self._MemoryStorage__schedule_expiry, so this plain name overrides it.
    def _MemoryStorage__schedule_expiry(self) -> None:
        with self._timer_lock:
            if not self.timer.is_alive():
                timer = threading.Timer(0.01, self._MemoryStorage__expire_events)
                # Start the object we just built, never a re-read attribute.
                self.timer = timer
                timer.start()

    def __getstate__(self):
        state = super().__getstate__()
        # A lock cannot be pickled; __setstate__ builds a fresh one.
        state.pop('_timer_lock', None)
        return state

    def __setstate__(self, state):
        self._timer_lock = threading.Lock()
        super().__setstate__(state)

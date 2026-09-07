"""Bounded admission for remote Whisper requests.

One pool per process. Active only when the operator turns
whisper_pool_enabled on and the backend is the remote API; inactive, every
method is a pass-through so callers need no branch of their own.
"""
import contextlib
import logging
import threading
import time

from config import WHISPER_BACKEND_API, coerce_bool_setting

logger = logging.getLogger('podcast.whisper_pool')

_SETTINGS_TTL_SECONDS = 5.0


def _db_settings_reader():
    """Default reader: DB with a short TTL so worker threads do not hit
    SQLite on every chunk."""
    cache = {'at': 0.0, 'value': None}
    lock = threading.Lock()

    def read():
        with lock:
            if cache['value'] is not None and time.time() - cache['at'] < _SETTINGS_TTL_SECONDS:
                return dict(cache['value'])
        try:
            from database import Database
            db = Database()
            value = {
                'enabled': coerce_bool_setting(db.get_setting('whisper_pool_enabled')),
                'backend': db.get_setting('whisper_backend') or 'local',
                'max_requests': max(1, min(64, int(db.get_setting('whisper_pool_max_requests') or 4))),
                'max_episodes': max(1, min(16, int(db.get_setting('whisper_pool_max_episodes') or 1))),
            }
        except Exception as e:
            logger.warning(f"Could not read whisper pool settings: {e}")
            value = {'enabled': False, 'backend': 'local', 'max_requests': 4, 'max_episodes': 1}
        with lock:
            cache['value'] = value
            cache['at'] = time.time()
        return dict(value)

    def invalidate():
        with lock:
            cache['at'] = 0.0

    read.invalidate = invalidate
    return read


class WhisperPool:
    def __init__(self, read_settings=None):
        self._read = read_settings or _db_settings_reader()
        self._lock = threading.Condition()
        self._settings = {}
        self._capacity = 0
        self._in_flight = 0
        self._transcribing = 0
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        """Re-read settings; a shrink never revokes a held permit.

        force=True bypasses the reader's TTL cache so a setting just written
        this request is visible immediately; the periodic dispatcher pass
        omits it and stays throttled by that cache.
        """
        if force:
            invalidate = getattr(self._read, 'invalidate', None)
            if invalidate is not None:
                invalidate()
        settings = self._read()
        with self._lock:
            self._settings = settings
            self._capacity = settings['max_requests'] if self._is_active(settings) else 0
            self._lock.notify_all()

    @staticmethod
    def _is_active(settings) -> bool:
        return bool(settings.get('enabled')) and settings.get('backend') == WHISPER_BACKEND_API

    @property
    def active(self) -> bool:
        return self._is_active(self._settings)

    @property
    def inactive_reason(self) -> str | None:
        if self.active:
            return None
        return 'local_backend' if self._settings.get('enabled') else 'disabled'

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def max_episodes(self) -> int:
        return self._settings.get('max_episodes', 1) if self.active else 1

    @contextlib.contextmanager
    def slot(self):
        """One in-flight request; pass-through while inactive. A permit
        taken while active is still released the same way after a toggle-off."""
        with self._lock:
            active = self._capacity > 0
            if active:
                while self._in_flight >= self._capacity and self._capacity > 0:
                    self._lock.wait()
                self._in_flight += 1
        try:
            yield
        finally:
            if active:
                with self._lock:
                    self._in_flight -= 1
                    self._lock.notify_all()

    @contextlib.contextmanager
    def transcribing(self):
        """Count an episode inside its transcription stage."""
        with self._lock:
            self._transcribing += 1
        try:
            yield
        finally:
            with self._lock:
                self._transcribing -= 1

    def chunk_workers(self, configured: int) -> int:
        """Per-episode chunk executor size. Every transcribing episode keeps
        one worker and the sum never exceeds capacity."""
        with self._lock:
            if self._capacity == 0:
                return configured
            share = self._capacity // max(1, self._transcribing)
            return max(1, min(configured, share))

    def snapshot(self) -> dict:
        with self._lock:
            settings = self._settings
            return {
                'enabled': bool(settings.get('enabled')),
                'backend': settings.get('backend'),
                'active': self._is_active(settings),
                'inactiveReason': self.inactive_reason,
                'capacity': self._capacity,
                'inFlight': self._in_flight,
                'transcribingEpisodes': self._transcribing,
                'maxEpisodes': {
                    'configured': settings.get('max_episodes', 1),
                    'effective': self.max_episodes,
                },
            }


_pool = None
_pool_lock = threading.Lock()


def get_pool() -> WhisperPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = WhisperPool()
    return _pool

"""
Processing Queue - Cross-process N-slot registry limiting concurrent episode processing.

Slot liveness is tracked by pid (os.kill(pid, 0)), not by a held lock; the
flock only serializes each read-modify-write of the shared state file.
"""
import contextlib
import fcntl
import json
import logging
import os
import threading
import time

# Timeouts are resolved at read time from settings via processing_timeouts.
from processing_timeouts import get_soft_timeout, get_hard_timeout
from utils.atomic_json import write_json_atomic
from utils.paths import resolve_data_dir

logger = logging.getLogger('podcast.processing_queue')


def _sync_status_clear(slug: str, episode_id: str) -> None:
    """Tell StatusService to drop its current_job if it matches.

    Lazy import avoids a circular dependency and keeps ProcessingQueue
    usable in contexts (tests, tooling) where StatusService is absent.
    """
    try:
        from status_service import StatusService
        StatusService().clear_if_matches(slug, episode_id)
    except Exception as e:
        logger.debug(f"Could not sync status_service clear: {e}")


class ProcessingQueue:
    """Cross-process N-slot registry coordinating concurrent episode processing.

    Uses file-based locking (fcntl.flock) to serialize state reads/writes
    across Gunicorn workers; each worker process gets its own instance.
    """

    # Keys are "slug:episode_id"; neither side contains ':'
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        data_dir = resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        self._lock_file_path = data_dir / '.processing_queue.lock'
        self._state_file_path = data_dir / '.processing_queue_state.json'
        self._fd_lock = threading.Lock()  # Protect flock file handle across threads
        self._initialized = True

    def _read_slots(self) -> dict:
        try:
            if self._state_file_path.exists():
                content = self._state_file_path.read_text()
                if content.strip():
                    return json.loads(content).get('slots', {}) or {}
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not read state file: {e}")
        return {}

    def _write_slots(self, slots: dict) -> None:
        if not write_json_atomic(self._state_file_path, {'slots': slots}):
            logger.warning("Could not write state file")

    def _seed_slot(self, slug, episode_id, started_at, pid):
        """Test helper: write a slot as another process would have."""
        with self._flock():
            slots = self._read_slots()
            slots[f"{slug}:{episode_id}"] = {'started_at': started_at, 'pid': pid}
            self._write_slots(slots)

    @contextlib.contextmanager
    def _flock(self):
        """Serialize read-modify-write of the state file across processes."""
        with self._fd_lock:
            with open(self._lock_file_path, 'a') as fd:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        # A pid reused by an unrelated process reads as alive, so a truly dead
        # slot with a recycled pid is only cleared once it hits the hard timeout.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _prune(self, slots: dict) -> dict:
        """Drop slots whose process is gone or whose run passed the hard timeout."""
        now = time.time()
        hard = get_hard_timeout()
        kept = {}
        for key, slot in slots.items():
            slug, _, episode_id = key.partition(':')
            elapsed = now - (slot.get('started_at') or now)
            if not self._pid_alive(int(slot.get('pid') or 0)):
                logger.warning(f"Clearing orphaned queue slot: {key} ({elapsed/60:.0f} min, process gone)")
                _sync_status_clear(slug, episode_id)
                continue
            if elapsed > hard:
                logger.error(
                    f"Force-clearing stuck job: {key} ({elapsed/60:.0f} min exceeds hard "
                    f"timeout {hard/60:.0f} min). Raise 'processing_hard_timeout_seconds' if premature.")
                _sync_status_clear(slug, episode_id)
                continue
            if elapsed > get_soft_timeout():
                logger.warning(f"Long-running job: {key} ({elapsed/60:.0f} min), still in progress")
            kept[key] = slot
        return kept

    def acquire(self, slug: str, episode_id: str, limit: int = 1, timeout: float = 0) -> bool:
        """Take a slot. False when this episode already holds one or the
        registry is at `limit`. `timeout` waits that long for a free slot."""
        key = f"{slug}:{episode_id}"
        deadline = time.time() + max(0.0, timeout)
        while True:
            with self._flock():
                slots = self._prune(self._read_slots())
                if key in slots:
                    logger.warning(f"ProcessingQueue rejecting acquire for {key}: already running")
                    self._write_slots(slots)
                    return False
                if len(slots) < max(1, limit):
                    slots[key] = {'started_at': time.time(), 'pid': os.getpid()}
                    self._write_slots(slots)
                    logger.info(f"ProcessingQueue slot acquired for {key} ({len(slots)}/{limit})")
                    return True
                self._write_slots(slots)
            if time.time() >= deadline:
                return False
            time.sleep(0.1)

    def release(self, slug: str, episode_id: str) -> None:
        key = f"{slug}:{episode_id}"
        with self._flock():
            slots = self._read_slots()
            if slots.pop(key, None) is not None:
                logger.info(f"ProcessingQueue slot released for {key}")
            self._write_slots(slots)

    def release_if_processing(self, slug: str, episode_id: str) -> bool:
        """Release the slot only if this episode currently holds one.

        Best-effort cleanup helper for cancel / feed-delete paths: swallows
        errors so a failed release never breaks the caller. Returns True if a
        release was attempted.
        """
        try:
            if self.is_processing(slug, episode_id):
                self.release(slug, episode_id)
                return True
        except Exception as e:
            logger.warning(f"Could not release processing queue: {e}")
        return False

    def get_current(self) -> list[tuple[str, str]]:
        """Running episodes, oldest first."""
        with self._flock():
            slots = self._prune(self._read_slots())
            self._write_slots(slots)
        ordered = sorted(slots.items(), key=lambda kv: kv[1].get('started_at') or 0)
        return [tuple(key.partition(':')[::2]) for key, _ in ordered]

    def slot_count(self) -> int:
        return len(self.get_current())

    def is_processing(self, slug: str, episode_id: str) -> bool:
        """Check if specific episode is currently being processed."""
        return (slug, episode_id) in self.get_current()

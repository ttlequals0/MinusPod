"""
Processing Queue - Cross-process N-slot registry limiting concurrent episode processing.

Slot liveness is tracked by pid plus that pid's process start time, not by a
held lock; the flock only serializes each read-modify-write of the state file.
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


def _pid_start_time(pid: int) -> float | None:
    """Process start time from /proc/<pid>/stat (field 22), or None if unreadable.

    The comm field can contain spaces and parentheses, so the split starts
    after the last ')': field N is then fields[N - 3].
    """
    try:
        with open(f"/proc/{pid}/stat") as fd:
            return float(fd.read().rpartition(')')[2].split()[19])
    except (OSError, IndexError, ValueError):
        return None


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

    def _seed_slot(self, slug, episode_id, started_at, pid, pid_start=None):
        """Test helper: write a slot as another process would have."""
        with self._flock():
            slots = self._read_slots()
            slots[f"{slug}:{episode_id}"] = {
                'started_at': started_at, 'pid': pid, 'pid_start': pid_start}
            self._write_slots(slots)

    def clear_all(self) -> int:
        """Drop every slot. At leader startup they all belong to the previous
        container run, whose pids this one can legitimately be handed again."""
        with self._flock():
            slots = self._read_slots()
            if slots:
                self._write_slots({})
                logger.info(f"Cleared {len(slots)} processing slot(s) left by a previous run")
        return len(slots)

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

    @contextlib.contextmanager
    def _flock_shared(self):
        """Shared-lock read of the state file; concurrent with other readers,
        blocks only while a writer holds the exclusive lock."""
        with open(self._lock_file_path, 'a') as fd:
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _slot_alive(cls, slot: dict) -> bool:
        """Liveness for one slot: the pid must exist and still be the process
        that took it. A container restart hands out the same low pids again,
        so pid alone would read a slot left by the previous run as live."""
        pid = int(slot.get('pid') or 0)
        if not cls._pid_alive(pid):
            return False
        recorded = slot.get('pid_start')
        if recorded is None:
            return True
        current = _pid_start_time(pid)
        return current is None or current == recorded

    def _prune(self, slots: dict) -> tuple[dict, bool]:
        """Drop slots whose process is gone or whose run passed the hard timeout.

        Returns (kept, changed) so callers can skip writing back an unchanged state.
        """
        now = time.time()
        hard = get_hard_timeout()
        kept = {}
        for key, slot in slots.items():
            slug, _, episode_id = key.partition(':')
            elapsed = now - (slot.get('started_at') or now)
            if not self._slot_alive(slot):
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
        return kept, len(kept) != len(slots)

    def acquire(self, slug: str, episode_id: str, limit: int = 1, timeout: float = 0) -> bool:
        """Take a slot. False when this episode already holds one or the
        registry is at `limit`. `timeout` waits that long for a free slot."""
        key = f"{slug}:{episode_id}"
        deadline = time.time() + max(0.0, timeout)
        while True:
            with self._flock():
                slots, pruned = self._prune(self._read_slots())
                if key in slots:
                    logger.warning(f"ProcessingQueue rejecting acquire for {key}: already running")
                    if pruned:
                        self._write_slots(slots)
                    return False
                if len(slots) < max(1, limit):
                    slots[key] = {'started_at': time.time(), 'pid': os.getpid(),
                                  'pid_start': _pid_start_time(os.getpid())}
                    self._write_slots(slots)
                    logger.info(f"ProcessingQueue slot acquired for {key} ({len(slots)}/{limit})")
                    return True
                if pruned:
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
            slots, pruned = self._prune(self._read_slots())
            if pruned:
                self._write_slots(slots)
        ordered = sorted(slots.items(), key=lambda kv: kv[1].get('started_at') or 0)
        return [tuple(key.partition(':')[::2]) for key, _ in ordered]

    def slot_count(self) -> int:
        return len(self.get_current())

    def is_processing(self, slug: str, episode_id: str) -> bool:
        """Check if specific episode is currently being processed.

        Cheap path: a shared-lock read of just this slot, no prune, no write,
        so a hot liveness check does not contend with acquire/release for the
        exclusive lock.
        """
        key = f"{slug}:{episode_id}"
        with self._flock_shared():
            slot = self._read_slots().get(key)
        if slot is None:
            return False
        return self._slot_alive(slot)

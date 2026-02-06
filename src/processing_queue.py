"""
Processing Queue - Cross-process singleton to prevent concurrent episode processing.

Only one episode can be processed at a time across ALL Gunicorn workers to prevent
OOM issues from multiple Whisper transcriptions running simultaneously on the GPU.

Uses fcntl.flock() for cross-process file locking instead of threading.Lock which
only works within a single process.
"""
import fcntl
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

# Must match StatusService.MAX_JOB_DURATION for consistency
MAX_JOB_DURATION = 3600  # 60 minutes - auto-clear stuck jobs

logger = logging.getLogger('podcast.processing_queue')


class ProcessingQueue:
    """Cross-process single-episode processing queue to prevent OOM from concurrent processing.

    Uses file-based locking (fcntl.flock) to coordinate across Gunicorn workers.
    Each worker process gets its own instance, but they all share the same lock file.
    """

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

        # Use /app/data for persistence (mounted volume in Docker)
        data_dir = Path(os.environ.get('DATA_DIR', '/app/data'))
        data_dir.mkdir(parents=True, exist_ok=True)

        self._lock_file_path = data_dir / '.processing_queue.lock'
        self._state_file_path = data_dir / '.processing_queue_state.json'
        self._lock_fd = None
        self._fd_lock = threading.Lock()  # Protect _lock_fd access across threads
        self._initialized = True

    def _read_state(self) -> dict:
        """Read current processing state from shared file."""
        try:
            if self._state_file_path.exists():
                content = self._state_file_path.read_text()
                if content.strip():
                    return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not read state file: {e}")
        return {'current_episode': None, 'acquired_at': None}

    def _write_state(self, slug: Optional[str], episode_id: Optional[str], acquired_at: Optional[float]):
        """Write processing state to shared file."""
        try:
            state = {
                'current_episode': [slug, episode_id] if slug and episode_id else None,
                'acquired_at': acquired_at
            }
            self._state_file_path.write_text(json.dumps(state))
        except OSError as e:
            logger.warning(f"Could not write state file: {e}")

    def _is_stale(self, state: dict) -> bool:
        """Check if current job has exceeded max duration."""
        if state.get('current_episode') is None or state.get('acquired_at') is None:
            return False
        return (time.time() - state['acquired_at']) > MAX_JOB_DURATION

    def _clear_stale_state(self) -> bool:
        """Clear stale state if job exceeded max duration. Returns True if cleared.

        If THIS process holds the lock, the job is still alive (just long-running),
        so we only log a warning. Only clear state for truly orphaned jobs where the
        lock is not held by this process.
        """
        state = self._read_state()
        if not self._is_stale(state):
            return False

        elapsed = time.time() - state['acquired_at']
        current = state.get('current_episode')

        # If THIS process holds the lock, the job is still alive - not orphaned.
        # Long episodes legitimately exceed MAX_JOB_DURATION. Only log, don't clear.
        if self._lock_fd is not None:
            if current:
                logger.warning(
                    f"Long-running job: {current[0]}:{current[1]} "
                    f"({elapsed/60:.0f} min) - still in progress, not clearing"
                )
            return False

        # Lock NOT held by this process - orphaned from a crashed process
        if current:
            logger.warning(
                f"Clearing orphaned queue state: {current[0]}:{current[1]} "
                f"({elapsed/60:.0f} min, process no longer holds lock)"
            )
        self._write_state(None, None, None)
        return True

    def acquire(self, slug: str, episode_id: str, timeout: float = 0) -> bool:
        """
        Try to acquire processing lock for an episode.

        Uses fcntl.flock() for cross-process coordination. Only one worker
        across all Gunicorn processes can hold this lock at a time.

        Thread-safe within a single process via _fd_lock.

        Args:
            slug: Podcast slug
            episode_id: Episode ID
            timeout: How long to wait for lock (0 = non-blocking)

        Returns:
            True if lock acquired, False if busy
        """
        with self._fd_lock:
            # If this process already holds the lock, reject new acquire
            # This prevents the fd overwrite bug where opening a new fd would
            # orphan the existing one and allow double-acquisition
            if self._lock_fd is not None:
                current = self._read_state().get('current_episode')
                current_str = f"{current[0]}:{current[1]}" if current else "unknown"
                logger.warning(
                    f"ProcessingQueue rejecting acquire for {slug}:{episode_id} - "
                    f"already holding lock for {current_str}"
                )
                return False

            # Clear stale state before attempting to acquire
            self._clear_stale_state()

            try:
                # Open lock file (create if doesn't exist)
                self._lock_fd = open(self._lock_file_path, 'w')

                # Try to acquire exclusive lock
                if timeout > 0:
                    # Blocking with timeout - use LOCK_EX (would block forever)
                    # fcntl doesn't support timeout directly, so we poll
                    start = time.time()
                    while (time.time() - start) < timeout:
                        try:
                            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            time.sleep(0.1)
                    else:
                        # Timeout expired
                        self._lock_fd.close()
                        self._lock_fd = None
                        return False
                else:
                    # Non-blocking
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Lock acquired - write state
                self._write_state(slug, episode_id, time.time())
                logger.info(f"ProcessingQueue lock acquired for {slug}:{episode_id}")
                return True

            except BlockingIOError:
                # Lock is held by another process
                if self._lock_fd:
                    self._lock_fd.close()
                    self._lock_fd = None
                return False
            except OSError as e:
                logger.error(f"ProcessingQueue lock error: {e}")
                if self._lock_fd:
                    self._lock_fd.close()
                    self._lock_fd = None
                return False

    def release(self):
        """Release processing lock. Thread-safe via _fd_lock."""
        with self._fd_lock:
            try:
                if self._lock_fd is not None:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    self._lock_fd.close()
                    self._lock_fd = None
                    logger.info("ProcessingQueue lock released")
            except OSError as e:
                logger.warning(f"Error releasing ProcessingQueue lock: {e}")

            # Clear state file
            self._write_state(None, None, None)

    def get_current(self) -> Optional[Tuple[str, str]]:
        """Get currently processing episode (slug, episode_id) or None.

        Reads from shared state file so all workers see the same state.
        Performs staleness check before returning.
        """
        self._clear_stale_state()
        state = self._read_state()
        current = state.get('current_episode')
        return tuple(current) if current else None

    def is_processing(self, slug: str, episode_id: str) -> bool:
        """Check if specific episode is currently being processed.

        Performs staleness check before returning.
        """
        self._clear_stale_state()
        current = self.get_current()
        return current is not None and current == (slug, episode_id)

    def is_busy(self) -> bool:
        """Check if any episode is currently being processed.

        Performs staleness check before returning.
        """
        self._clear_stale_state()
        return self.get_current() is not None

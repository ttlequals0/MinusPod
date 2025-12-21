"""
Processing Queue - Singleton class to prevent concurrent episode processing.

Only one episode can be processed at a time to prevent OOM issues from
multiple Whisper transcriptions and FFMPEG processes running simultaneously.
"""
import threading
from typing import Optional, Tuple


class ProcessingQueue:
    """Single-episode processing queue to prevent OOM from concurrent processing."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._processing_lock = threading.Lock()
                    cls._instance._current_episode: Optional[Tuple[str, str]] = None
        return cls._instance

    def acquire(self, slug: str, episode_id: str, timeout: float = 0) -> bool:
        """
        Try to acquire processing lock for an episode.

        Args:
            slug: Podcast slug
            episode_id: Episode ID
            timeout: How long to wait for lock (0 = non-blocking)

        Returns:
            True if lock acquired, False if busy
        """
        acquired = self._processing_lock.acquire(blocking=timeout > 0, timeout=timeout if timeout > 0 else -1)
        if acquired:
            self._current_episode = (slug, episode_id)
        return acquired

    def release(self):
        """Release processing lock."""
        try:
            self._processing_lock.release()
        except RuntimeError:
            pass  # Lock wasn't held
        self._current_episode = None

    def get_current(self) -> Optional[Tuple[str, str]]:
        """Get currently processing episode (slug, episode_id) or None."""
        return self._current_episode

    def is_processing(self, slug: str, episode_id: str) -> bool:
        """Check if specific episode is currently being processed."""
        current = self._current_episode
        return current is not None and current == (slug, episode_id)

    def is_busy(self) -> bool:
        """Check if any episode is currently being processed."""
        return self._current_episode is not None

"""
Status Service - Tracks processing status for real-time UI updates.

Provides centralized status tracking for:
- Current processing jobs
- Processing queue state
- Feed refresh status

Uses file-based storage for multi-worker consistency.
"""
import fcntl
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# Status file location - shared across all workers
STATUS_FILE = os.path.join(
    os.environ.get('DATA_DIR', '/app/data'),
    'processing_status.json'
)

# Staleness thresholds (seconds)
MAX_JOB_DURATION = 3600      # 60 minutes - auto-clear stuck jobs (must match processing_queue.py)
MAX_QUEUE_ENTRY_AGE = 3600   # 1 hour - remove stale queue entries


@dataclass
class ProcessingJob:
    """Represents a currently processing episode."""
    slug: str
    episode_id: str
    title: str
    podcast_name: str
    started_at: float
    stage: str = "downloading"  # downloading, transcribing, detecting, processing, complete
    progress: float = 0.0  # 0-100


@dataclass
class FeedRefresh:
    """Represents a feed refresh operation."""
    slug: str
    podcast_name: str
    started_at: float
    new_episodes: int = 0


@dataclass
class SystemStatus:
    """Current system status snapshot."""
    current_job: Optional[ProcessingJob] = None
    queue_length: int = 0
    queued_episodes: List[Dict] = field(default_factory=list)
    feed_refreshes: List[FeedRefresh] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)


class StatusService:
    """Singleton service for tracking and broadcasting system status.

    Uses file-based storage for multi-worker consistency with Gunicorn.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        """Initialize instance state."""
        self._status_lock = threading.Lock()
        self._subscribers: List[callable] = []
        # Ensure status file directory exists
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)

    def _read_status_file(self) -> dict:
        """Read status from shared file with locking.

        Also performs staleness cleanup: auto-clears jobs running longer
        than MAX_JOB_DURATION and removes queue entries older than
        MAX_QUEUE_ENTRY_AGE. This handles cases where workers are
        SIGKILL'd and never call complete_job()/fail_job().
        """
        try:
            if not os.path.exists(STATUS_FILE):
                return self._empty_status()

            with open(STATUS_FILE, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    content = f.read()
                    if not content:
                        return self._empty_status()
                    status = json.loads(content)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Staleness cleanup
            needs_write = False
            now = time.time()

            # Auto-clear stuck current_job
            job = status.get('current_job')
            if job and job.get('started_at'):
                elapsed = now - job['started_at']
                if elapsed > MAX_JOB_DURATION:
                    import logging
                    logger = logging.getLogger('podcast.status')
                    logger.warning(
                        f"Auto-clearing stale job: {job.get('title', 'unknown')} "
                        f"(running {elapsed/60:.0f} min, max {MAX_JOB_DURATION/60:.0f} min)"
                    )
                    status['current_job'] = None
                    needs_write = True

            # Remove stale queue entries
            queued = status.get('queued_episodes', [])
            if queued:
                fresh = [
                    e for e in queued
                    if now - e.get('queued_at', now) <= MAX_QUEUE_ENTRY_AGE
                ]
                if len(fresh) < len(queued):
                    import logging
                    logger = logging.getLogger('podcast.status')
                    logger.warning(
                        f"Removed {len(queued) - len(fresh)} stale queue entries "
                        f"(older than {MAX_QUEUE_ENTRY_AGE/60:.0f} min)"
                    )
                    status['queued_episodes'] = fresh
                    needs_write = True

            if needs_write:
                status['last_updated'] = now
                self._write_status_file(status)

            return status
        except (json.JSONDecodeError, IOError):
            return self._empty_status()

    def _write_status_file(self, status: dict):
        """Write status to shared file with locking."""
        try:
            # Write to temp file then rename for atomicity
            temp_file = STATUS_FILE + '.tmp'
            with open(temp_file, 'w') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(status, f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            os.rename(temp_file, STATUS_FILE)
        except IOError:
            pass  # Best effort - don't crash on write failures

    def set_server_start_time(self, start_time: float):
        """Store server start time in shared status file.

        Always overwrites the existing value. This ensures uptime resets
        on deploy/container restart (when the status file persists but
        the server did restart). Workers starting at slightly different
        times will overwrite each other, but the difference is negligible.
        """
        with self._status_lock:
            status = self._read_status_file()
            status['server_start_time'] = start_time
            self._write_status_file(status)

    def _empty_status(self) -> dict:
        """Return empty status dict."""
        return {
            'current_job': None,
            'queued_episodes': [],
            'feed_refreshes': {},
            'last_updated': time.time()
        }

    def start_job(self, slug: str, episode_id: str, title: str, podcast_name: str):
        """Mark an episode as starting processing."""
        with self._status_lock:
            status = self._read_status_file()
            status['current_job'] = {
                'slug': slug,
                'episode_id': episode_id,
                'title': title,
                'podcast_name': podcast_name,
                'started_at': time.time(),
                'stage': 'downloading',
                'progress': 0.0
            }
            # Remove from queue if it was queued
            status['queued_episodes'] = [
                e for e in status.get('queued_episodes', [])
                if not (e['slug'] == slug and e['episode_id'] == episode_id)
            ]
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def update_job_stage(self, stage: str, progress: float = None):
        """Update the current job's stage and optional progress."""
        with self._status_lock:
            status = self._read_status_file()
            if status.get('current_job'):
                status['current_job']['stage'] = stage
                if progress is not None:
                    status['current_job']['progress'] = progress
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def _clear_current_job(self):
        """Clear the current job from status tracking."""
        with self._status_lock:
            status = self._read_status_file()
            status['current_job'] = None
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def complete_job(self):
        """Mark the current job as complete."""
        self._clear_current_job()

    def fail_job(self):
        """Mark the current job as failed."""
        self._clear_current_job()

    def queue_episode(self, slug: str, episode_id: str, title: str, podcast_name: str):
        """Add an episode to the queue."""
        with self._status_lock:
            status = self._read_status_file()
            queued = status.get('queued_episodes', [])
            # Don't add duplicates
            for e in queued:
                if e['slug'] == slug and e['episode_id'] == episode_id:
                    return
            queued.append({
                'slug': slug,
                'episode_id': episode_id,
                'title': title,
                'podcast_name': podcast_name,
                'queued_at': time.time()
            })
            status['queued_episodes'] = queued
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def get_queue_position(self, slug: str, episode_id: str) -> int:
        """Get queue position for an episode (1-based, 0 if not queued)."""
        with self._status_lock:
            status = self._read_status_file()
            queued = status.get('queued_episodes', [])
            for i, e in enumerate(queued):
                if e['slug'] == slug and e['episode_id'] == episode_id:
                    return i + 1  # 1-based position
            return 0

    def start_feed_refresh(self, slug: str, podcast_name: str):
        """Mark a feed refresh as starting."""
        with self._status_lock:
            status = self._read_status_file()
            refreshes = status.get('feed_refreshes', {})
            refreshes[slug] = {
                'slug': slug,
                'podcast_name': podcast_name,
                'started_at': time.time(),
                'new_episodes': 0
            }
            status['feed_refreshes'] = refreshes
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def complete_feed_refresh(self, slug: str, new_episodes: int = 0):
        """Mark a feed refresh as complete."""
        with self._status_lock:
            status = self._read_status_file()
            refreshes = status.get('feed_refreshes', {})
            if slug in refreshes:
                if new_episodes > 0:
                    # Keep for a few seconds to show the count
                    refreshes[slug]['new_episodes'] = new_episodes
                else:
                    del refreshes[slug]
                status['feed_refreshes'] = refreshes
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def remove_feed_refresh(self, slug: str):
        """Remove a feed refresh status."""
        with self._status_lock:
            status = self._read_status_file()
            refreshes = status.get('feed_refreshes', {})
            if slug in refreshes:
                del refreshes[slug]
                status['feed_refreshes'] = refreshes
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def get_status(self) -> SystemStatus:
        """Get current system status snapshot."""
        with self._status_lock:
            status = self._read_status_file()

            current_job = None
            if status.get('current_job'):
                job = status['current_job']
                current_job = ProcessingJob(
                    slug=job['slug'],
                    episode_id=job['episode_id'],
                    title=job['title'],
                    podcast_name=job['podcast_name'],
                    started_at=job['started_at'],
                    stage=job.get('stage', 'downloading'),
                    progress=job.get('progress', 0.0)
                )

            feed_refreshes = []
            for r in status.get('feed_refreshes', {}).values():
                feed_refreshes.append(FeedRefresh(
                    slug=r['slug'],
                    podcast_name=r['podcast_name'],
                    started_at=r['started_at'],
                    new_episodes=r.get('new_episodes', 0)
                ))

            return SystemStatus(
                current_job=current_job,
                queue_length=len(status.get('queued_episodes', [])),
                queued_episodes=status.get('queued_episodes', []).copy(),
                feed_refreshes=feed_refreshes,
                last_updated=status.get('last_updated', time.time())
            )

    def subscribe(self, callback: callable):
        """Subscribe to status updates."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def _notify_subscribers(self):
        """Notify all subscribers of status change."""
        status = self.get_status()
        for callback in self._subscribers:
            try:
                callback(status)
            except Exception:
                pass  # Don't let subscriber errors break updates

    def to_dict(self) -> dict:
        """Convert current status to JSON-serializable dict."""
        status = self.get_status()
        return {
            'currentJob': {
                'slug': status.current_job.slug,
                'episodeId': status.current_job.episode_id,
                'title': status.current_job.title,
                'podcastName': status.current_job.podcast_name,
                'stage': status.current_job.stage,
                'progress': status.current_job.progress,
                'startedAt': status.current_job.started_at,
                'elapsed': time.time() - status.current_job.started_at
            } if status.current_job else None,
            'queueLength': status.queue_length,
            'queuedEpisodes': [
                {
                    'slug': e['slug'],
                    'episodeId': e['episode_id'],
                    'title': e['title'],
                    'podcastName': e['podcast_name'],
                    'queuedAt': e['queued_at']
                }
                for e in status.queued_episodes
            ],
            'feedRefreshes': [
                {
                    'slug': r.slug,
                    'podcastName': r.podcast_name,
                    'newEpisodes': r.new_episodes,
                    'startedAt': r.started_at
                }
                for r in status.feed_refreshes
            ],
            'lastUpdated': status.last_updated
        }

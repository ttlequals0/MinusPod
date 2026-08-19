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
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from utils.atomic_json import write_json_atomic

# Status file location - shared across all workers
STATUS_FILE = os.path.join(
    os.environ.get('DATA_DIR')
    or os.environ.get('DATA_PATH')
    or os.environ.get('MINUSPOD_DATA_DIR')
    or '/app/data',
    'processing_status.json'
)

# Staleness thresholds resolved from settings at read time.
from processing_timeouts import get_soft_timeout as _get_soft_timeout

logger = logging.getLogger('podcast.status')


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
    current_job: ProcessingJob | None = None
    queue_length: int = 0
    queued_episodes: list[dict] = field(default_factory=list)
    feed_refreshes: list[FeedRefresh] = field(default_factory=list)
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
        self._file_lock = threading.Lock()
        self._subscribers_lock = threading.Lock()
        self._subscribers: list[callable] = []
        self._lock_warned = False
        # Ensure status file directory exists
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)

    @contextmanager
    def _status_transaction(self):
        """Serialize a read-modify-write across threads and gunicorn workers.

        threading.Lock covers only this process, so without the flock two
        workers interleave and one update is silently lost.
        """
        # Lock path derives from STATUS_FILE so a relocated status file, as in
        # tests, cannot end up guarded by a lock somewhere else.
        with self._file_lock:
            fd = None
            try:
                fd = os.open(STATUS_FILE + '.lock', os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as e:
                # Some network mounts refuse flock. Degrade rather than kill a
                # job over a status update, but say so once.
                if fd is not None:
                    os.close(fd)
                    fd = None
                if not self._lock_warned:
                    self._lock_warned = True
                    logger.warning(
                        f"Status lock unavailable ({e}); concurrent worker "
                        f"updates may be lost")
            try:
                yield
            finally:
                if fd is not None:
                    os.close(fd)  # releases the flock

    def _read_status_file(self) -> dict:
        """Parse the status file. Writes only to heal a corrupt one.

        Staleness expiry moved to _expire_stale so a plain read stops
        rewriting the file on every poll. Corruption recovery stays here: it
        is one-shot, and without it every later read logs the same warning.
        """
        try:
            if not os.path.exists(STATUS_FILE):
                return self._empty_status()

            # No flock here: every caller already holds the sidecar lock.
            with open(STATUS_FILE, 'r') as f:
                content = f.read()
            if not content:
                return self._empty_status()
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("processing_status.json is corrupt; treating as empty "
                           "and rewriting clean")
            empty = self._empty_status()
            self._write_status_file(empty)
            return empty
        except OSError:
            return self._empty_status()

    def _expire_stale(self, status: dict, announce: bool) -> bool:
        """Drop a timed-out job and stale queue entries. True if it changed.

        Workers that are SIGKILL'd never call complete_job(), so nothing else
        clears them. `announce` is off for read-only callers, which expire in
        memory only and would otherwise log the same job on every poll.
        """
        changed = False
        now = time.time()
        soft_limit = _get_soft_timeout()

        job = status.get('current_job')
        if job and job.get('started_at'):
            elapsed = now - job['started_at']
            if elapsed > soft_limit:
                if announce:
                    logger.warning(
                        f"Auto-clearing stale job: {job.get('title', 'unknown')} "
                        f"(running {elapsed/60:.0f} min, soft timeout {soft_limit/60:.0f} min). "
                        f"Raise 'processing_soft_timeout_seconds' in settings if this was premature."
                    )
                status['current_job'] = None
                changed = True

        queued = status.get('queued_episodes', [])
        if queued:
            fresh = [e for e in queued if now - e.get('queued_at', now) <= soft_limit]
            if len(fresh) < len(queued):
                if announce:
                    logger.warning(
                        f"Removed {len(queued) - len(fresh)} stale queue entries "
                        f"(older than {soft_limit/60:.0f} min)"
                    )
                status['queued_episodes'] = fresh
                changed = True

        if changed:
            status['last_updated'] = now
        return changed

    def _load(self) -> dict:
        """Read and expire, persisting the expiry. Callers must hold the lock."""
        status = self._read_status_file()
        if self._expire_stale(status, announce=True):
            self._write_status_file(status)
        return status

    def _peek(self) -> dict:
        """Read-only view with staleness applied in memory but not written."""
        status = self._read_status_file()
        self._expire_stale(status, announce=False)
        return status

    def _write_status_file(self, status: dict):
        """Write status to the shared file. Best effort, never raises."""
        write_json_atomic(STATUS_FILE, status)

    def set_server_start_time(self, start_time: float):
        """Store server start time in shared status file.

        Always overwrites the existing value. This ensures uptime resets
        on deploy/container restart (when the status file persists but
        the server did restart). Workers starting at slightly different
        times will overwrite each other, but the difference is negligible.
        """
        with self._status_transaction():
            status = self._load()
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
        with self._status_transaction():
            status = self._load()
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
        with self._status_transaction():
            status = self._load()
            if status.get('current_job'):
                status['current_job']['stage'] = stage
                if progress is not None:
                    status['current_job']['progress'] = progress
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def _clear_current_job(self):
        """Clear the current job from status tracking."""
        with self._status_transaction():
            status = self._load()
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

    def clear_if_matches(self, slug: str, episode_id: str) -> bool:
        """Clear current_job only if it matches (slug, episode_id).

        Used by ProcessingQueue orphan recovery so the UI does not show a
        killed job as still transcribing for up to MAX_JOB_DURATION.
        """
        with self._status_transaction():
            status = self._load()
            job = status.get('current_job')
            if not job or job.get('slug') != slug or job.get('episode_id') != episode_id:
                return False
            status['current_job'] = None
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()
        return True

    def queue_episode(self, slug: str, episode_id: str, title: str, podcast_name: str):
        """Add an episode to the queue."""
        with self._status_transaction():
            status = self._load()
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

    def remove_queued_episode(self, slug: str, episode_id: str) -> bool:
        """Drop an episode from the display queue. Returns True if it was present."""
        with self._status_transaction():
            status = self._load()
            queued = status.get('queued_episodes', [])
            remaining = [
                e for e in queued
                if not (e['slug'] == slug and e['episode_id'] == episode_id)
            ]
            if len(remaining) == len(queued):
                return False
            status['queued_episodes'] = remaining
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()
        return True

    def remove_feed_from_queue(self, slug: str) -> int:
        """Drop all queued episodes for a feed. Returns count removed."""
        with self._status_transaction():
            status = self._load()
            queued = status.get('queued_episodes', [])
            remaining = [e for e in queued if e['slug'] != slug]
            removed = len(queued) - len(remaining)
            if removed:
                status['queued_episodes'] = remaining
                status['last_updated'] = time.time()
                self._write_status_file(status)
        if removed:
            self._notify_subscribers()
        return removed

    def get_queue_position(self, slug: str, episode_id: str) -> int:
        """Get queue position for an episode (1-based, 0 if not queued)."""
        with self._status_transaction():
            status = self._peek()
            queued = status.get('queued_episodes', [])
            for i, e in enumerate(queued):
                if e['slug'] == slug and e['episode_id'] == episode_id:
                    return i + 1  # 1-based position
            return 0

    def start_feed_refresh(self, slug: str, podcast_name: str):
        """Mark a feed refresh as starting."""
        with self._status_transaction():
            status = self._load()
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
        with self._status_transaction():
            status = self._load()
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
        with self._status_transaction():
            status = self._load()
            refreshes = status.get('feed_refreshes', {})
            if slug in refreshes:
                del refreshes[slug]
                status['feed_refreshes'] = refreshes
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def get_status(self) -> SystemStatus:
        """Get current system status snapshot."""
        with self._status_transaction():
            status = self._peek()

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
        # _subscribers is touched from SSE-request threads and the processing
        # thread; guard mutation/iteration with the status lock so a concurrent
        # subscribe/unsubscribe can't corrupt the list mid-notify
        # (concurrency-sweep-3).
        with self._subscribers_lock:
            self._subscribers.append(callback)

        def _unsubscribe():
            with self._subscribers_lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def _notify_subscribers(self):
        """Notify all subscribers of status change."""
        status = self.get_status()
        # Snapshot under the lock, then call callbacks outside it so a slow or
        # re-entrant callback can't hold the lock or hit a mutated list.
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        warned = getattr(self, '_warned_subscribers', None)
        if warned is None:
            warned = self._warned_subscribers = set()
        for callback in subscribers:
            try:
                callback(status)
                warned.discard(callback)
            except Exception as e:
                # A subscriber error must not break the broadcast loop. Surface
                # the first failure at warning so it isn't silently dropped, then
                # drop to debug so a persistently broken listener can't spam a
                # warning on every status update.
                if callback in warned:
                    logger.debug(f"Status subscriber callback still failing: {e}")
                else:
                    warned.add(callback)
                    logger.warning(f"Status subscriber callback failed: {e}")

    def to_dict(self, status: SystemStatus | None = None) -> dict:
        """Convert status to a JSON-serializable dict.

        Subscribers are handed a snapshot; passing it back avoids a second
        cross-process lock acquisition per open SSE stream, per update.
        """
        status = status if status is not None else self.get_status()
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


def reconcile_startup_state(db) -> None:
    """Clear stale processing state left by the previous container run.

    At boot, no job can legitimately be in-flight (single-process container).
    Reads processing_status.json; if a current_job is present, resets that
    episode's DB status to 'pending' and drops all queued_episodes display
    entries so the UI is clean before the queue processor thread starts.

    db - Database instance (provides get_connection())
    """
    ss = StatusService()
    with ss._status_transaction():
        # Raw read, not _load(): expiring the job here would hide it from the
        # DB reset below and leave the row stuck on 'processing' (#2522).
        status = ss._read_status_file()
        job = status.get('current_job')
        queued = status.get('queued_episodes', [])
        changed = False

        if job:
            status['current_job'] = None
            changed = True
        if queued:
            status['queued_episodes'] = []
            changed = True

        if changed:
            status['last_updated'] = time.time()
            ss._write_status_file(status)

    # Outside the status lock: SQLite can block for busy_timeout (30s) and
    # every get_status() in both workers would queue behind it.
    if queued:
        logger.warning(f"Startup: dropping {len(queued)} stale queue display entries")
    if job:
        slug = job.get('slug', '')
        episode_id = job.get('episode_id', '')
        logger.warning(
            f"Startup: clearing stale job from previous run: {slug}:{episode_id}"
        )
        # Mirror reset_stuck_processing_episodes: reset to pending, no retry penalty.
        conn = db.get_connection()
        conn.execute(
            """UPDATE episodes SET
               status = 'pending',
               error_message = 'Reset after container restart (no retry penalty)'
               WHERE episode_id = ?
                 AND podcast_id = (SELECT id FROM podcasts WHERE slug = ?)
                 AND status = 'processing'""",
            (episode_id, slug),
        )
        conn.commit()

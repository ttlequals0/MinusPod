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

from database import Database
from utils.atomic_json import write_json_atomic
from utils.paths import resolve_data_dir


def _status_file_path() -> str:
    """Shared status file location, resolved fresh so a relocated data dir is honoured."""
    return str(resolve_data_dir() / 'processing_status.json')

# Staleness thresholds resolved from settings at read time.
from processing_timeouts import get_soft_timeout as _get_soft_timeout

logger = logging.getLogger('podcast.status')


def _episode_is_processing(slug: str, episode_id: str) -> bool:
    try:
        row = Database().get_connection().execute(
            "SELECT 1 FROM processing_runs r JOIN podcasts p ON p.id = r.podcast_id "
            "WHERE p.slug = ? AND r.episode_id = ? "
            "AND r.state IN ('running', 'cancel_requested')",
            (slug, episode_id),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("Could not verify processing state for %s:%s: %s",
                       slug, episode_id, exc)
        return True


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
    run_id: str | None = None


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
    jobs: list[ProcessingJob] = field(default_factory=list)
    queue_length: int = 0
    queued_episodes: list[dict] = field(default_factory=list)
    feed_refreshes: list[FeedRefresh] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)
    revision: int = 0

    @property
    def current_job(self) -> ProcessingJob | None:
        return self.jobs[0] if self.jobs else None


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

    @staticmethod
    def _key(slug, episode_id):
        return f"{slug}:{episode_id}"

    @staticmethod
    def _jobs(status: dict) -> dict:
        """Jobs map; a pre-pool file carried one current_job."""
        jobs = status.get('jobs')
        if jobs is None:
            jobs = {}
            legacy = status.pop('current_job', None)
            if legacy:
                jobs[f"{legacy['slug']}:{legacy['episode_id']}"] = legacy
            status['jobs'] = jobs
        return jobs

    def _init(self):
        """Initialize instance state."""
        self._file_lock = threading.Lock()
        self._subscribers_lock = threading.Lock()
        self._subscribers: list[callable] = []
        self._lock_warned = False
        self.status_file = _status_file_path()
        # Ensure status file directory exists
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)

    @contextmanager
    def _status_transaction(self):
        """Serialize a read-modify-write across threads and gunicorn workers.

        threading.Lock covers only this process, so without the flock two
        workers interleave and one update is silently lost.
        """
        # Lock path derives from self.status_file so a relocated status file, as in
        # tests, cannot end up guarded by a lock somewhere else.
        with self._file_lock:
            fd = None
            try:
                fd = os.open(self.status_file + '.lock', os.O_CREAT | os.O_RDWR, 0o644)
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
            if not os.path.exists(self.status_file):
                return self._empty_status()

            # No flock here: every caller already holds the sidecar lock.
            with open(self.status_file, 'r') as f:
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

        jobs = self._jobs(status)
        stale_keys = []
        for key, job in jobs.items():
            if not job.get('started_at') or now - job['started_at'] <= soft_limit:
                continue
            # Age is only suspicion. A durable live owner keeps both capacity
            # and display state; database failures fail closed in is_processing.
            if _episode_is_processing(job.get('slug'), job.get('episode_id')):
                continue
            stale_keys.append(key)
        for key in stale_keys:
            job = jobs.pop(key)
            elapsed = now - job['started_at']
            if announce:
                logger.warning(
                    f"Auto-clearing stale job: {job.get('title', 'unknown')} "
                    f"(running {elapsed/60:.0f} min, soft timeout {soft_limit/60:.0f} min). "
                    f"Raise 'processing_soft_timeout_seconds' in settings if this was premature."
                )
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
        status['revision'] = int(status.get('revision', 0)) + 1
        write_json_atomic(self.status_file, status)

    def get_server_start_time(self) -> float | None:
        """Shared server start time, or None when it was never recorded."""
        value = self._peek().get('server_start_time')
        return value if isinstance(value, (int, float)) else None

    def claim_server_start_time(self, start_time: float, owner: str) -> float:
        """Record start_time for this run; a stored stamp from the same owner wins
        (a respawn keeps uptime), a new owner overwrites (a deploy resets it)."""
        with self._status_transaction():
            status = self._load()
            stored = status.get('server_start_time')
            if (status.get('server_start_owner') == owner
                    and isinstance(stored, (int, float))
                    and stored <= start_time):
                return stored
            status['server_start_time'] = start_time
            status['server_start_owner'] = owner
            self._write_status_file(status)
            return start_time

    def _empty_status(self) -> dict:
        """Return empty status dict."""
        return {
            'jobs': {},
            'queued_episodes': [],
            'feed_refreshes': {},
            'last_updated': time.time(),
            'revision': 0,
        }

    def start_job(self, slug: str, episode_id: str, title: str, podcast_name: str,
                  run_id: str = None):
        """Mark an episode as starting processing."""
        with self._status_transaction():
            status = self._load()
            self._jobs(status)[self._key(slug, episode_id)] = {
                'slug': slug,
                'episode_id': episode_id,
                'title': title,
                'podcast_name': podcast_name,
                'started_at': time.time(),
                'stage': 'downloading',
                'progress': 0.0
            }
            if run_id:
                self._jobs(status)[self._key(slug, episode_id)]['run_id'] = run_id
            # Remove from queue if it was queued
            status['queued_episodes'] = [
                e for e in status.get('queued_episodes', [])
                if not (e['slug'] == slug and e['episode_id'] == episode_id)
            ]
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def update_job_stage(self, slug: str, episode_id: str, stage: str,
                         progress: float = None, run_id: str = None):
        """Update one job's stage and optional progress."""
        with self._status_transaction():
            status = self._load()
            job = self._jobs(status).get(self._key(slug, episode_id))
            if job and (run_id is None or job.get('run_id') == run_id):
                job['stage'] = stage
                if progress is not None:
                    job['progress'] = progress
                status['last_updated'] = time.time()
                self._write_status_file(status)
        self._notify_subscribers()

    def _clear_job(self, slug: str, episode_id: str, run_id: str = None):
        """Remove one job from status tracking."""
        with self._status_transaction():
            status = self._load()
            jobs = self._jobs(status)
            job = jobs.get(self._key(slug, episode_id))
            if run_id is not None and (not job or job.get('run_id') != run_id):
                return
            jobs.pop(self._key(slug, episode_id), None)
            status['last_updated'] = time.time()
            self._write_status_file(status)
        self._notify_subscribers()

    def complete_job(self, slug: str, episode_id: str, run_id: str = None):
        """Mark a job as complete."""
        self._clear_job(slug, episode_id, run_id)

    def fail_job(self, slug: str, episode_id: str, run_id: str = None):
        """Mark a job as failed."""
        self._clear_job(slug, episode_id, run_id)

    def clear_if_matches(self, slug: str, episode_id: str,
                         run_id: str = None) -> bool:
        """Remove the job for (slug, episode_id) if present.

        Used by ProcessingQueue orphan recovery so the UI does not show a
        killed job as still transcribing for up to MAX_JOB_DURATION.
        """
        with self._status_transaction():
            status = self._load()
            jobs = self._jobs(status)
            job = jobs.get(self._key(slug, episode_id))
            if run_id is not None and (not job or job.get('run_id') != run_id):
                return False
            job = jobs.pop(self._key(slug, episode_id), None)
            if not job:
                return False
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

            jobs = [
                ProcessingJob(
                    slug=job['slug'],
                    episode_id=job['episode_id'],
                    title=job['title'],
                    podcast_name=job['podcast_name'],
                    started_at=job['started_at'],
                    stage=job.get('stage', 'downloading'),
                    progress=job.get('progress', 0.0),
                    run_id=job.get('run_id'),
                )
                for job in sorted(self._jobs(status).values(), key=lambda j: j['started_at'])
            ]

            feed_refreshes = []
            for r in status.get('feed_refreshes', {}).values():
                feed_refreshes.append(FeedRefresh(
                    slug=r['slug'],
                    podcast_name=r['podcast_name'],
                    started_at=r['started_at'],
                    new_episodes=r.get('new_episodes', 0)
                ))

            return SystemStatus(
                jobs=jobs,
                queue_length=len(status.get('queued_episodes', [])),
                queued_episodes=status.get('queued_episodes', []).copy(),
                feed_refreshes=feed_refreshes,
                last_updated=status.get('last_updated', time.time()),
                revision=int(status.get('revision', 0)),
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

        def job_dict(j):
            return {
                'slug': j.slug, 'episodeId': j.episode_id, 'title': j.title,
                'podcastName': j.podcast_name, 'stage': j.stage,
                'progress': j.progress, 'startedAt': j.started_at,
                'elapsed': time.time() - j.started_at,
                'runId': j.run_id,
            }
        jobs = [job_dict(j) for j in status.jobs]
        return {
            'currentJob': jobs[0] if jobs else None,
            'jobs': jobs,
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
            'lastUpdated': status.last_updated,
            'revision': status.revision,
        }


def reconcile_startup_state(db) -> None:
    """Clear display and episode state that has no active durable run."""
    ss = StatusService()
    with ss._status_transaction():
        status = ss._read_status_file()
        jobs = list(ss._jobs(status).values())
        queued = list(status.get('queued_episodes', []))

    stale_jobs = []
    conn = db.get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        for job in jobs:
            slug = job.get('slug', '')
            episode_id = job.get('episode_id', '')
            active = conn.execute(
                "SELECT r.run_id FROM processing_runs r "
                "JOIN podcasts p ON p.id = r.podcast_id "
                "WHERE p.slug = ? AND r.episode_id = ? "
                "AND r.state IN ('running', 'cancel_requested')",
                (slug, episode_id),
            ).fetchone()
            if active and (
                    not job.get('run_id') or job.get('run_id') == active['run_id']):
                continue
            stale_jobs.append(job)
            if active:
                continue
            conn.execute(
                """UPDATE episodes SET
                   status = 'pending',
                   error_message = 'Reset after container restart (no retry penalty)'
                   WHERE episode_id = ?
                     AND podcast_id = (SELECT id FROM podcasts WHERE slug = ?)
                     AND status = 'processing'
                     AND NOT EXISTS (
                       SELECT 1 FROM processing_runs r
                       WHERE r.podcast_id = episodes.podcast_id
                         AND r.episode_id = episodes.episode_id
                         AND r.state IN ('running', 'cancel_requested')
                     )""",
                (episode_id, slug),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    with ss._status_transaction():
        status = ss._read_status_file()
        current_jobs = ss._jobs(status)
        changed = False
        for job in stale_jobs:
            key = ss._key(job.get('slug', ''), job.get('episode_id', ''))
            if current_jobs.get(key) == job:
                current_jobs.pop(key)
                changed = True
        if queued:
            remaining = [
                entry for entry in status.get('queued_episodes', [])
                if entry not in queued
            ]
            if len(remaining) != len(status.get('queued_episodes', [])):
                status['queued_episodes'] = remaining
                changed = True
        if changed:
            status['last_updated'] = time.time()
            ss._write_status_file(status)

    if queued:
        logger.warning(f"Startup: dropping {len(queued)} stale queue display entries")
    for job in stale_jobs:
        logger.warning(
            "Startup: clearing stale job from previous run: %s:%s",
            job.get('slug', ''), job.get('episode_id', ''),
        )

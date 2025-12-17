"""
Status Service - Tracks processing status for real-time UI updates.

Provides centralized status tracking for:
- Current processing jobs
- Processing queue state
- Feed refresh status
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime


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
    """Singleton service for tracking and broadcasting system status."""

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
        self._current_job: Optional[ProcessingJob] = None
        self._queued_episodes: List[Dict] = []
        self._feed_refreshes: Dict[str, FeedRefresh] = {}
        self._subscribers: List[callable] = []

    def start_job(self, slug: str, episode_id: str, title: str, podcast_name: str):
        """Mark an episode as starting processing."""
        with self._status_lock:
            self._current_job = ProcessingJob(
                slug=slug,
                episode_id=episode_id,
                title=title,
                podcast_name=podcast_name,
                started_at=time.time()
            )
            # Remove from queue if it was queued
            self._queued_episodes = [
                e for e in self._queued_episodes
                if not (e['slug'] == slug and e['episode_id'] == episode_id)
            ]
        self._notify_subscribers()

    def update_job_stage(self, stage: str, progress: float = None):
        """Update the current job's stage and optional progress."""
        with self._status_lock:
            if self._current_job:
                self._current_job.stage = stage
                if progress is not None:
                    self._current_job.progress = progress
        self._notify_subscribers()

    def complete_job(self):
        """Mark the current job as complete."""
        with self._status_lock:
            self._current_job = None
        self._notify_subscribers()

    def fail_job(self):
        """Mark the current job as failed."""
        with self._status_lock:
            self._current_job = None
        self._notify_subscribers()

    def queue_episode(self, slug: str, episode_id: str, title: str, podcast_name: str):
        """Add an episode to the queue."""
        with self._status_lock:
            # Don't add duplicates
            for e in self._queued_episodes:
                if e['slug'] == slug and e['episode_id'] == episode_id:
                    return
            self._queued_episodes.append({
                'slug': slug,
                'episode_id': episode_id,
                'title': title,
                'podcast_name': podcast_name,
                'queued_at': time.time()
            })
        self._notify_subscribers()

    def start_feed_refresh(self, slug: str, podcast_name: str):
        """Mark a feed refresh as starting."""
        with self._status_lock:
            self._feed_refreshes[slug] = FeedRefresh(
                slug=slug,
                podcast_name=podcast_name,
                started_at=time.time()
            )
        self._notify_subscribers()

    def complete_feed_refresh(self, slug: str, new_episodes: int = 0):
        """Mark a feed refresh as complete."""
        with self._status_lock:
            if slug in self._feed_refreshes:
                if new_episodes > 0:
                    # Keep for a few seconds to show the count
                    self._feed_refreshes[slug].new_episodes = new_episodes
                else:
                    del self._feed_refreshes[slug]
        self._notify_subscribers()

    def remove_feed_refresh(self, slug: str):
        """Remove a feed refresh status."""
        with self._status_lock:
            if slug in self._feed_refreshes:
                del self._feed_refreshes[slug]
        self._notify_subscribers()

    def get_status(self) -> SystemStatus:
        """Get current system status snapshot."""
        with self._status_lock:
            return SystemStatus(
                current_job=self._current_job,
                queue_length=len(self._queued_episodes),
                queued_episodes=self._queued_episodes.copy(),
                feed_refreshes=list(self._feed_refreshes.values()),
                last_updated=time.time()
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

"""Auto-process queue mixin for MinusPod database."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Set, Tuple

logger = logging.getLogger(__name__)


class QueueMixin:
    """Auto-process queue management methods."""

    def is_auto_process_enabled(self) -> bool:
        """Check if auto-process is enabled globally."""
        setting = self.get_setting('auto_process_enabled')
        return setting == 'true' if setting else True  # Default to enabled

    def is_auto_process_enabled_for_podcast(self, slug: str) -> bool:
        """Check if auto-process is enabled for a specific podcast.

        Returns: True if enabled (considering both global and podcast-level settings)
        """
        # Check global setting first
        global_enabled = self.is_auto_process_enabled()

        # Get podcast-level override
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return global_enabled

        override = podcast.get('auto_process_override')
        if override == 'true':
            return True
        elif override == 'false':
            return False
        else:
            # No override, use global setting
            return global_enabled

    def queue_episode_for_processing(self, slug: str, episode_id: str,
                                      original_url: str, title: str = None,
                                      published_at: str = None,
                                      description: str = None) -> Optional[int]:
        """Add an episode to the auto-process queue. Returns queue ID or None if already queued."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            logger.error(f"Cannot queue episode: podcast not found: {slug}")
            return None

        podcast_id = podcast['id']

        try:
            cursor = conn.execute(
                """INSERT INTO auto_process_queue
                   (podcast_id, episode_id, original_url, title, published_at, description)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(podcast_id, episode_id) DO NOTHING""",
                (podcast_id, episode_id, original_url, title, published_at, description)
            )
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception as e:
            logger.error(f"Failed to queue episode for processing: {e}")
            return None

    def upsert_episode_for_processing(self, slug: str, episode_id: str,
                                      original_url: str, title: str = None,
                                      published_at: str = None,
                                      description: str = None) -> Optional[int]:
        """Add or reset an episode in the auto-process queue to 'pending'.

        Unlike queue_episode_for_processing (which skips already-queued rows),
        this method resets the status and attempt counter even when a completed
        or failed row already exists.  Used by bulk process/reprocess actions
        so re-queuing is reliable regardless of prior queue history.

        Returns queue row ID or None on failure.
        """
        conn = self.get_connection()

        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            logger.error(f"Cannot upsert episode for processing: podcast not found: {slug}")
            return None

        podcast_id = podcast['id']

        try:
            cursor = conn.execute(
                """INSERT INTO auto_process_queue
                   (podcast_id, episode_id, original_url, title, published_at, description,
                    status, attempts, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, NULL)
                   ON CONFLICT(podcast_id, episode_id) DO UPDATE SET
                     status = 'pending',
                     attempts = 0,
                     error_message = NULL,
                     original_url = excluded.original_url,
                     title = COALESCE(excluded.title, auto_process_queue.title),
                     published_at = COALESCE(excluded.published_at, auto_process_queue.published_at),
                     description = COALESCE(excluded.description, auto_process_queue.description),
                     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')""",
                (podcast_id, episode_id, original_url, title, published_at, description)
            )
            conn.commit()
            return cursor.lastrowid if cursor.lastrowid else None
        except Exception as e:
            logger.error(f"Failed to upsert episode for processing: {e}")
            return None


    def get_next_queued_episode(self) -> Optional[Dict]:
        """Get the next pending episode from the queue (FIFO order, read-only)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT q.*, p.slug as podcast_slug, p.title as podcast_title
               FROM auto_process_queue q
               JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending'
               ORDER BY q.created_at ASC
               LIMIT 1"""
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def claim_next_queued_episode(self) -> Optional[Dict]:
        """Atomically claim the next pending episode, marking it 'processing'.

        Closes the SELECT-then-mark gap in get_next_queued_episode: the
        conditional ``UPDATE ... WHERE status='pending'`` plus the rowcount
        guard means only one consumer can win a given row (SQLite serializes
        the writes), so the dequeue is safe even if a second queue consumer is
        ever added. Returns the claimed row (status='processing'), or None if
        the queue is empty. On the rare lost race it tries the next pending row.
        """
        conn = self.get_connection()
        for _ in range(5):
            row = conn.execute(
                """SELECT q.*, p.slug as podcast_slug, p.title as podcast_title
                   FROM auto_process_queue q
                   JOIN podcasts p ON q.podcast_id = p.id
                   WHERE q.status = 'pending'
                   ORDER BY q.created_at ASC
                   LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """UPDATE auto_process_queue
                   SET status = 'processing',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ? AND status = 'pending'""",
                (row['id'],),
            )
            conn.commit()
            if cursor.rowcount == 1:
                claimed = dict(row)
                claimed['status'] = 'processing'
                return claimed
            # Lost the race to another consumer; try the next pending row.
        return None

    def update_queue_status(self, queue_id: int, status: str,
                            error_message: str = None) -> bool:
        """Update the status of a queued episode."""
        conn = self.get_connection()
        if error_message:
            conn.execute(
                """UPDATE auto_process_queue SET
                   status = ?,
                   error_message = ?,
                   attempts = attempts + 1,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ?""",
                (status, error_message, queue_id)
            )
        else:
            conn.execute(
                """UPDATE auto_process_queue SET
                   status = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ?""",
                (status, queue_id)
            )
        conn.commit()
        return True

    def close_queue_rows_for_episode(self, slug: str, episode_id: str) -> int:
        """Mark any non-terminal queue rows for this episode as completed.

        Guards the double-trigger bug where a manual
        POST /episodes/<id>/reprocess finishes the job but leaves the
        background-enqueued row in auto_process_queue still pending,
        which then caused the queue processor to re-run the same episode.
        Safe to call on every successful finalize -- the UPDATE is a
        no-op when there is no matching pending/processing/failed row.
        Returns the number of rows touched.
        """
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return 0
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """UPDATE auto_process_queue
                   SET status = 'completed',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE podcast_id = ?
                     AND episode_id = ?
                     AND status IN ('pending', 'processing', 'failed')""",
                (podcast['id'], episode_id)
            )
            conn.commit()
            return cursor.rowcount
        except Exception:
            conn.rollback()
            raise

    def get_queue_status(self) -> Dict:
        """Get auto-process queue status summary."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT
               COUNT(*) FILTER (WHERE status = 'pending') as pending,
               COUNT(*) FILTER (WHERE status = 'processing') as processing,
               COUNT(*) FILTER (WHERE status = 'completed') as completed,
               COUNT(*) FILTER (WHERE status = 'failed') as failed,
               COUNT(*) as total
               FROM auto_process_queue"""
        )
        row = cursor.fetchone()
        return dict(row) if row else {'pending': 0, 'processing': 0, 'completed': 0, 'failed': 0, 'total': 0}

    def clear_completed_queue_items(self, older_than_hours: int = 24) -> int:
        """Clear completed queue items older than specified hours. Returns count deleted."""
        conn = self.get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cursor = conn.execute(
            """DELETE FROM auto_process_queue
               WHERE status = 'completed' AND updated_at < ?""",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount

    def clear_pending_queue_items(self) -> int:
        """Clear all pending items from the auto-process queue. Returns count deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            """DELETE FROM auto_process_queue WHERE status = 'pending'"""
        )
        conn.commit()
        return cursor.rowcount

    def reset_orphaned_queue_items(self, stuck_minutes: int = 35, max_attempts: int = 3) -> Tuple[int, int]:
        """Reset queue items stuck in 'processing' for too long.

        This catches orphaned queue items where the worker crashed or was killed
        without properly updating the status. Items exceeding max_attempts are
        marked as 'failed' permanently. Items under max_attempts are reset to
        'pending' WITHOUT incrementing attempts -- orphan resets are not failures.
        Only actual processing failures (in _handle_processing_failure) increment
        the attempts counter.

        Args:
            stuck_minutes: Minutes after which a 'processing' item is considered orphaned
            max_attempts: Maximum retry attempts before marking as permanently failed

        Returns:
            Tuple of (reset_count, failed_count)
        """
        conn = self.get_connection()

        # First: Mark items that exceeded max attempts as permanently failed
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'failed',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error_message = 'Exceeded max retry attempts'
               WHERE status = 'processing'
               AND attempts >= ?
               AND datetime(updated_at) < datetime('now', ? || ' minutes')
               RETURNING id, episode_id""",
            (max_attempts, f'-{stuck_minutes}')
        )
        failed_items = cursor.fetchall()

        # Second: Reset items under max attempts, NO attempt increment (orphan != failure)
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'pending',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error_message = 'Reset after worker crash (no attempt penalty)'
               WHERE status = 'processing'
               AND attempts < ?
               AND datetime(updated_at) < datetime('now', ? || ' minutes')
               RETURNING id, episode_id""",
            (max_attempts, f'-{stuck_minutes}')
        )
        reset_items = cursor.fetchall()
        conn.commit()

        for row in failed_items:
            logger.warning(f"Queue item exceeded max attempts, marking failed: id={row['id']}, episode_id={row['episode_id']}")
        for row in reset_items:
            logger.info(f"Reset orphaned queue item (no attempt penalty): id={row['id']}, episode_id={row['episode_id']}")

        return len(reset_items), len(failed_items)

    def reset_failed_queue_items(self, max_retries: int = 4, max_age_hours: int = 48) -> int:
        """Reset failed queue items eligible for automatic retry with backoff.

        Backoff ladder (5 total attempts, ~1h50m tail):
        attempt 1 -> 5 min, attempt 2 -> 15 min, attempt 3 -> 30 min, attempt 4+ -> 60 min.
        Only resets where episode status is 'failed' (not 'permanently_failed'),
        retry_count < max_retries, and the item failed within max_age_hours.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'pending',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE id IN (
                   SELECT q.id
                   FROM auto_process_queue q
                   JOIN episodes e ON q.podcast_id = e.podcast_id
                                    AND q.episode_id = e.episode_id
                   WHERE q.status = 'failed'
                     AND e.status = 'failed'
                     AND e.retry_count < ?
                     AND datetime(q.updated_at) > datetime('now', '-' || ? || ' hours')
                     AND datetime(q.updated_at) < datetime('now',
                         CASE
                             WHEN q.attempts <= 1 THEN '-5 minutes'
                             WHEN q.attempts = 2 THEN '-15 minutes'
                             WHEN q.attempts = 3 THEN '-30 minutes'
                             ELSE '-60 minutes'
                         END
                     )
               )
               RETURNING id, episode_id""",
            (max_retries, max_age_hours)
        )
        reset_items = cursor.fetchall()
        conn.commit()
        for row in reset_items:
            logger.info(f"Reset failed queue item for retry: id={row['id']}, episode_id={row['episode_id']}")
        return len(reset_items)

    # -- Offline queue (#482): deferred-episode lifecycle --

    def get_deferred_episodes(self) -> List[Dict]:
        """All episodes waiting in the offline queue, oldest deferral first."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.*, p.slug AS podcast_slug, p.title AS podcast_title
               FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE e.status = 'deferred'
               ORDER BY e.deferred_at ASC"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_deferred_episodes(self) -> int:
        """Number of episodes waiting in the offline queue."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM episodes WHERE status = 'deferred'"
        ).fetchone()
        return row['n'] if row else 0

    def expire_deferred_episodes(self, ttl_hours: int) -> List[Dict]:
        """Fail offline-queue episodes whose TTL has run out.

        Marked permanently_failed (a plain 'failed' would be resurrected by
        the reset_failed_queue_items retry ladder) with an explicit TTL
        message; the matching auto_process_queue row is closed the same way.
        Returns the expired rows so the caller can fire failure webhooks.
        """
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT e.id, e.podcast_id, e.episode_id, e.title, e.error_message,
                      p.slug AS podcast_slug, p.title AS podcast_title
               FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE e.status = 'deferred'
                 AND datetime(e.deferred_at) < datetime('now', '-' || ? || ' hours')""",
            (ttl_hours,)
        ).fetchall()
        expired = []
        for row in rows:
            row = dict(row)
            message = (f"Offline queue TTL expired after {ttl_hours} hours: "
                       f"{row['error_message'] or 'service unreachable'}")
            row['error_message'] = message
            cursor = conn.execute(
                """UPDATE episodes
                   SET status = 'permanently_failed',
                       error_message = ?,
                       deferred_at = NULL,
                       deferred_service = NULL,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ? AND status = 'deferred'""",
                (message, row['id'])
            )
            if cursor.rowcount != 1:
                # Lost a race with a concurrent user action (e.g. a manual
                # reprocess flipped it to pending between the SELECT and this
                # UPDATE); its fresh queue row must not be failed either.
                continue
            conn.execute(
                """UPDATE auto_process_queue
                   SET status = 'failed',
                       error_message = ?,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE podcast_id = ? AND episode_id = ?
                     AND status != 'completed'""",
                (message, row['podcast_id'], row['episode_id'])
            )
            logger.warning(
                "Offline queue TTL expired for %s:%s after %dh; marking permanently_failed",
                row['podcast_slug'], row['episode_id'], ttl_hours,
            )
            expired.append(row)
        conn.commit()
        return expired

    def requeue_deferred_episodes(self, services: Set[str]) -> int:
        """Flip deferred episodes back to pending for reachable services.

        Each episode gets its auto_process_queue row upserted to pending (the
        background processor's atomic claim drives it from there).
        deferred_service NULL is treated as 'llm'. deferred_at is deliberately
        KEPT: it marks the first entry into the offline queue, so the TTL
        keeps ticking across re-drive cycles (success and TTL expiry clear
        it). Episodes on auto-process-disabled feeds without a user-initiated
        reprocess stay deferred -- the claim-time gate would otherwise close
        their queue row and strand them in 'pending' outside every ladder.
        """
        requeued = 0
        for episode in self.get_deferred_episodes():
            service = episode.get('deferred_service') or 'llm'
            if service not in services:
                continue
            slug = episode['podcast_slug']
            if not (episode.get('reprocess_requested_at')
                    or self.is_auto_process_enabled_for_podcast(slug)):
                continue
            self.upsert_episode_for_processing(
                slug, episode['episode_id'], episode['original_url'],
                title=episode.get('title'),
                published_at=episode.get('published_at'),
                description=episode.get('description'),
            )
            self.upsert_episode(
                slug, episode['episode_id'],
                status='pending', error_message=None,
            )
            logger.info(
                "Offline queue: %s reachable again, re-queued %s:%s",
                service, slug, episode['episode_id'],
            )
            requeued += 1
        return requeued

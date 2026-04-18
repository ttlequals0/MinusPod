"""Auto-process queue mixin for MinusPod database."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple

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

    def get_next_queued_episode(self) -> Optional[Dict]:
        """Get the next pending episode from the queue (FIFO order)."""
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

    def reset_failed_queue_items(self, max_retries: int = 3, max_age_hours: int = 48) -> int:
        """Reset failed queue items eligible for automatic retry with backoff.

        Backoff: attempt 1 -> 5 min, attempt 2 -> 15 min, attempt 3+ -> 45 min.
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
                             ELSE '-45 minutes'
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

"""Auto-process queue mixin for MinusPod database."""
import logging
from datetime import datetime, timedelta, timezone

from config import DEFER_SERVICE_LLM
from utils.time import ISO_FORMAT, utc_now

logger = logging.getLogger(__name__)

# Priority boosts (#625): applied on top of the feed's base queue_priority
# (10 high / 0 normal / -10 low) at enqueue time. These are the fallback
# defaults; operators can change the stored queue_*_boost settings, and
# compute_queue_priority reads through to them on every enqueue.
FRESH_EPISODE_BOOST = 5
MANUAL_REQUEST_BOOST = 20
BULK_REQUEST_BOOST = 0
FRESH_WINDOW_HOURS = 48

# Bounds for a hand-set row priority (#696). Wide enough to clear a feed's
# base priority plus every boost, tight enough that a typo cannot park a row
# beyond anything the queue will ever enqueue.
QUEUE_PRIORITY_MIN = -1000
QUEUE_PRIORITY_MAX = 1000

_BOOST_SETTINGS = (
    ('queue_manual_boost', MANUAL_REQUEST_BOOST),
    ('queue_fresh_boost', FRESH_EPISODE_BOOST),
    ('queue_bulk_boost', BULK_REQUEST_BOOST),
)


def _resolve_boost(key: str, fallback: int) -> int:
    from database import Database
    try:
        return int(Database().get_setting(key))
    except Exception:
        return fallback


def resolve_queue_boosts() -> dict[str, int]:
    """Stored boost settings with per-key fallback to the constants.

    Inline import: database.queue is part of the Database mixin family, so a
    module-level Database import would be circular.
    """
    return {key: _resolve_boost(key, fallback) for key, fallback in _BOOST_SETTINGS}

# Bound on the row-level detail returned by get_queue_status.
_QUEUE_STATUS_ITEMS_LIMIT = 100

# Bound on the pending rows returned by get_pending_queued_episodes.
PENDING_QUEUE_LIMIT = 200


def compute_queue_priority(feed_priority, published_at_iso, manual=False,
                            bulk=False, now=None, apply_fresh_boost=True):
    """Base feed priority plus boosts for fresh episodes, manual requests,
    and bulk operations.

    ``manual`` is a single-episode user action (reprocess, JIT play);
    ``bulk`` is backlog work (Reprocess All, segment re-renders), which
    defaults to no boost so it cannot starve manual requests or fresh
    releases. Boost sizes are operator-configurable settings.
    """
    boosts = resolve_queue_boosts()
    p = int(feed_priority or 0)
    if manual:
        p += boosts['queue_manual_boost']
    if bulk:
        p += boosts['queue_bulk_boost']
    if apply_fresh_boost and published_at_iso:
        try:
            published = datetime.fromisoformat(published_at_iso.replace('Z', '+00:00'))
            now = now or datetime.now(timezone.utc)
            if (now - published) <= timedelta(hours=FRESH_WINDOW_HOURS):
                p += boosts['queue_fresh_boost']
        except ValueError:
            pass
    return p


class QueueMixin:
    """Auto-process queue management methods."""

    def is_auto_process_enabled(self) -> bool:
        """Check if auto-process is enabled globally."""
        setting = self.get_setting('auto_process_enabled')
        return setting == 'true' if setting else True  # Default to enabled

    def is_auto_process_enabled_for_podcast(self, slug: str,
                                            podcast: dict | None = None) -> bool:
        """Check if auto-process is enabled for a specific podcast.

        podcast: an already-fetched row, so a caller that needs other columns
        too does not pay for a second get_podcast_by_slug query.
        Returns: True if enabled (considering both global and podcast-level settings)
        """
        # Check global setting first
        global_enabled = self.is_auto_process_enabled()

        # Get podcast-level override
        if podcast is None:
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
                                      description: str = None,
                                      priority: int = 0) -> int | None:
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
                   (podcast_id, episode_id, original_url, title, published_at, description, priority)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(podcast_id, episode_id) DO NOTHING""",
                (podcast_id, episode_id, original_url, title, published_at, description, priority)
            )
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to queue episode for processing: {e}")
            return None

    def upsert_episode_for_processing(self, slug: str, episode_id: str,
                                      original_url: str, title: str = None,
                                      published_at: str = None,
                                      description: str = None,
                                      priority: int = 0) -> int | None:
        """Add or reset an episode in the auto-process queue to 'pending'.

        Unlike queue_episode_for_processing (which skips already-queued rows),
        this method reopens a completed or failed row. Its attempt counter is
        reset with it; a row already pending keeps its count, so a client
        polling a busy play request cannot restart the retry ladder.  Used by bulk process/reprocess actions
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
                    priority, status, attempts, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL)
                   ON CONFLICT(podcast_id, episode_id) DO UPDATE SET
                     status = 'pending',
                     attempts = CASE WHEN auto_process_queue.status = 'pending'
                                     THEN auto_process_queue.attempts ELSE 0 END,
                     -- A pending row's priority can only rise. Keeping the
                     -- old value guards boosts against background re-upserts;
                     -- taking MAX also lets a later JIT play or manual
                     -- reprocess climb past a bulk backlog instead of being
                     -- silently discarded (#625 follow-up).
                     priority = CASE WHEN auto_process_queue.status = 'pending'
                                     THEN MAX(auto_process_queue.priority, excluded.priority)
                                     ELSE excluded.priority END,
                     error_message = NULL,
                     original_url = excluded.original_url,
                     title = COALESCE(excluded.title, auto_process_queue.title),
                     published_at = COALESCE(excluded.published_at, auto_process_queue.published_at),
                     description = COALESCE(excluded.description, auto_process_queue.description),
                     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')""",
                (podcast_id, episode_id, original_url, title, published_at, description, priority)
            )
            conn.commit()
            return cursor.lastrowid if cursor.lastrowid else None
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to upsert episode for processing: {e}")
            return None


    def get_next_queued_episode(self) -> dict | None:
        """Get the next pending episode from the queue (FIFO order, read-only)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT q.*, p.slug as podcast_slug, p.title as podcast_title
               FROM auto_process_queue q
               JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending'
               ORDER BY q.priority DESC, q.created_at ASC
               LIMIT 1"""
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_pending_queued_episodes(self, limit: int = PENDING_QUEUE_LIMIT,
                                    offset: int = 0) -> list[dict]:
        """Pending queue rows in dequeue order (same ORDER BY as the claim).

        Feeds the Processing Queue panel, which showed only the active job plus
        the display queue and so hid the auto-process backlog entirely. Capped
        at `limit` rows with an optional `offset` for pagination; each row
        carries total_pending (the uncapped count, via a window function
        evaluated before the LIMIT) so a caller can say how much of the
        backlog it is showing.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT q.episode_id, q.title, q.priority, q.created_at,
                      p.slug as podcast_slug, p.title as podcast_title,
                      COUNT(*) OVER () as total_pending
               FROM auto_process_queue q
               JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending'
               ORDER BY q.priority DESC, q.created_at ASC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
        return [dict(row) for row in cursor.fetchall()]

    def set_queue_row_priority(self, slug: str, episode_id: str,
                               priority: int | None = None,
                               delta: int | None = None) -> int | None:
        """Set or nudge one pending row's priority; returns the stored value,
        or None when the episode has no pending row.

        Direct write: unlike the MAX() monotonic rule in
        upsert_episode_for_processing, this can raise or lower. A delta is
        applied in SQL so two nudges racing on a stale read cannot cancel
        each other out. Re-enqueueing (MAX rule) and a feed-level
        queuePriority change (restamp_pending_priorities) still override it.
        """
        expr, value = ('?', priority) if delta is None else ('priority + ?', delta)
        where = ("""WHERE episode_id = ? AND status = 'pending'
                      AND podcast_id = (SELECT id FROM podcasts WHERE slug = ?)""")
        conn = self.get_connection()
        cursor = conn.execute(
            f"""UPDATE auto_process_queue
                SET priority = MAX(?, MIN(?, {expr})),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                {where}""",  # noqa: S608
            (QUEUE_PRIORITY_MIN, QUEUE_PRIORITY_MAX, int(value), episode_id, slug)
        )
        if not cursor.rowcount:
            # An UPDATE that matched nothing still opens a transaction; close
            # it rather than leave one for the #566 leak guard to find.
            conn.rollback()
            return None
        if delta is None:
            conn.commit()
            return max(QUEUE_PRIORITY_MIN, min(QUEUE_PRIORITY_MAX, int(value)))
        # A relative write is the only case whose result we cannot predict.
        row = conn.execute(
            f"SELECT priority FROM auto_process_queue {where}",  # noqa: S608
            (episode_id, slug)
        ).fetchone()
        conn.commit()
        return row['priority'] if row else None

    def count_pending_queued_episodes(self) -> int:
        """Uncapped pending-row count for the paginated queue view: the
        window-function total rides on the page's rows, which vanish past
        the last page."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM auto_process_queue WHERE status = 'pending'"
        ).fetchone()
        return row['n'] if row else 0

    def has_user_requested_pending_row(self) -> bool:
        """True when a pending row's episode carries reprocess_requested_at.

        The rate-limit pause gate uses this to wave through work the user
        asked for by hand instead of parking a Play behind a provider backoff
        window. Reads the same user-intent mark the auto-process gate does,
        rather than inferring intent from a priority number: a stored priority
        is base + boosts, so it cannot tell a manual request from a high-
        priority feed, and a manual boost of 0 would match every row.
        """
        conn = self.get_connection()
        row = conn.execute(
            """SELECT 1 FROM auto_process_queue q
               JOIN episodes e ON e.podcast_id = q.podcast_id
                                AND e.episode_id = q.episode_id
               WHERE q.status = 'pending'
                 AND e.reprocess_requested_at IS NOT NULL
               LIMIT 1"""
        ).fetchone()
        return row is not None

    def get_pending_queue_keys(self, episode_ids: list[str]) -> set:
        """(podcast_slug, episode_id) for pending rows among `episode_ids`.

        Lets GET /episodes/processing dedup StatusService's display extras
        (a handful of ids) against the pending backlog without a full scan.
        """
        if not episode_ids:
            return set()
        conn = self.get_connection()
        placeholders = ','.join('?' * len(episode_ids))
        cursor = conn.execute(
            f"""SELECT p.slug as podcast_slug, q.episode_id
                FROM auto_process_queue q
                JOIN podcasts p ON q.podcast_id = p.id
                WHERE q.status = 'pending' AND q.episode_id IN ({placeholders})""",  # noqa: S608
            episode_ids
        )
        return {(r['podcast_slug'], r['episode_id']) for r in cursor.fetchall()}

    def claim_next_queued_episode(self) -> dict | None:
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
                   ORDER BY q.priority DESC, q.created_at ASC
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

    def _update_queue_status(self, queue_id: int, status: str,
                             error_message: str = None,
                             expect_status: str = None) -> bool:
        """Write a queue row's status. Returns whether a row changed.

        Private: callers reporting on a claim they hold go through
        close_claimed_queue_row, whose guard keeps a mid-run requeue alive.
        ``expect_status`` is that guard.
        """
        conn = self.get_connection()
        where = 'WHERE id = ?'
        tail = [queue_id]
        if expect_status is not None:
            where += ' AND status = ?'
            tail.append(expect_status)
        if error_message:
            cursor = conn.execute(
                f"""UPDATE auto_process_queue SET
                   status = ?,
                   error_message = ?,
                   attempts = attempts + 1,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   {where}""",  # noqa: S608
                (status, error_message, *tail)
            )
        else:
            cursor = conn.execute(
                f"""UPDATE auto_process_queue SET
                   status = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   {where}""",  # noqa: S608
                (status, *tail)
            )
        conn.commit()
        return cursor.rowcount > 0

    def close_claimed_queue_row(self, queue_id: int, status: str,
                                error_message: str = None) -> bool:
        """Report a drainer verdict on a row this claim still holds.

        The write is skipped when something re-queued the episode mid-run
        (degraded re-detect, low-ad-yield rerun), so that rerun survives.
        Returns whether a row changed.
        """
        return self._update_queue_status(queue_id, status, error_message,
                                         expect_status='processing')

    def get_queue_row_status(self, queue_id: int) -> str | None:
        """Current status of one queue row, or None when it is gone."""
        row = self.get_connection().execute(
            'SELECT status FROM auto_process_queue WHERE id = ?', (queue_id,)
        ).fetchone()
        return row['status'] if row else None

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

    def get_queue_status(self) -> dict:
        """Auto-process queue status summary, plus the pending/processing rows
        (with priority) driving the dequeue order (#625)."""
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
        result = dict(row) if row else {'pending': 0, 'processing': 0, 'completed': 0, 'failed': 0, 'total': 0}

        items = conn.execute(
            """SELECT q.id, q.episode_id, q.status, q.priority, q.created_at,
                      p.slug as podcast_slug
               FROM auto_process_queue q
               JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status IN ('pending', 'processing')
               ORDER BY q.priority DESC, q.created_at ASC
               LIMIT ?""",
            (_QUEUE_STATUS_ITEMS_LIMIT,)
        ).fetchall()
        result['items'] = [dict(r) for r in items]
        return result

    def restamp_pending_priorities(self, podcast_id: int, feed_priority: int) -> int:
        """Re-stamp pending queue rows for a podcast after its feed priority changes.

        Approximation: recomputes each row with manual=False, so a manual boost
        already applied to a still-pending row is lost (a manual reprocess
        re-enqueues on its own). Returns rows touched.
        """
        conn = self.get_connection()
        apply_fresh_boost = self.get_setting_bool('process_new_episodes_first', True)
        rows = conn.execute(
            """SELECT id, published_at FROM auto_process_queue
               WHERE podcast_id = ? AND status = 'pending'""",
            (podcast_id,)
        ).fetchall()
        for row in rows:
            new_priority = compute_queue_priority(
                feed_priority, row['published_at'], manual=False,
                apply_fresh_boost=apply_fresh_boost)
            conn.execute(
                """UPDATE auto_process_queue SET priority = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ?""",
                (new_priority, row['id'])
            )
        conn.commit()
        return len(rows)

    def clear_completed_queue_items(self, older_than_hours: int = 24) -> int:
        """Clear completed queue items older than specified hours. Returns count deleted."""
        conn = self.get_connection()
        cutoff = (utc_now() - timedelta(hours=older_than_hours)).strftime(ISO_FORMAT)
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

    def reset_orphaned_queue_items(self, stuck_minutes: int = 35, max_attempts: int = 3) -> tuple[int, int]:
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

    # Pending recuts: review decisions recorded but not yet cut into audio.

    def mark_episode_pending_recut(self, slug: str, episode_id: str) -> None:
        """Stamp an episode as having unapplied review decisions.

        Idempotent and first-write-wins: the stamp marks when the episode
        first went stale, so several edits over an afternoon still recut once.
        """
        conn = self.get_connection()
        conn.execute(
            """UPDATE episodes SET pending_recut_at = COALESCE(pending_recut_at, ?)
               WHERE episode_id = ?
                 AND podcast_id = (SELECT id FROM podcasts WHERE slug = ?)""",
            (utc_now().strftime(ISO_FORMAT), episode_id, slug)
        )
        conn.commit()

    def clear_episode_pending_recut(self, slug: str, episode_id: str) -> None:
        """Drop the stamp once a recut has applied the decisions."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE episodes SET pending_recut_at = NULL
               WHERE episode_id = ?
                 AND podcast_id = (SELECT id FROM podcasts WHERE slug = ?)""",
            (episode_id, slug)
        )
        conn.commit()

    def get_episodes_pending_recut(self, limit: int = 1000,
                                   slug: str | None = None) -> list[dict]:
        """Episodes with unapplied review decisions, oldest stamp first.

        `slug` scopes the result to one feed, so a feed page can apply its own
        pending recuts without touching the rest of the queue.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.episode_id, e.title, e.status, e.original_url,
                      e.pending_recut_at, p.slug AS podcast_slug,
                      p.title AS podcast_title
               FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE e.pending_recut_at IS NOT NULL
                 AND (? IS NULL OR p.slug = ?)
               ORDER BY e.pending_recut_at ASC
               LIMIT ?""",
            (slug, slug, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_episodes_pending_recut(self, slug: str | None = None) -> int:
        """How many episodes are waiting for an apply, optionally one feed's.

        The unscoped count stays single-table: joining podcasts would drop an
        episode whose feed row is missing, changing a number nothing asked to
        change.
        """
        if slug is None:
            row = self.get_connection().execute(
                "SELECT COUNT(*) AS n FROM episodes "
                "WHERE pending_recut_at IS NOT NULL"
            ).fetchone()
        else:
            row = self.get_connection().execute(
                """SELECT COUNT(*) AS n
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.pending_recut_at IS NOT NULL AND p.slug = ?""",
                (slug,)
            ).fetchone()
        return row['n'] if row else 0

    # Deferred-episode lifecycle: offline queue (#482), rate-limit hold (#696)

    @staticmethod
    def _deferred_service_clause(service, exclude_service, col):
        """WHERE fragment and params selecting one deferred_service, or every
        service but one. NULL reads as DEFER_SERVICE_LLM."""
        if service is not None:
            return f"AND COALESCE({col}, '{DEFER_SERVICE_LLM}') = ?", [service]
        if exclude_service is not None:
            return f"AND COALESCE({col}, '{DEFER_SERVICE_LLM}') != ?", [exclude_service]
        return "", []

    def get_deferred_episodes(self, service: str | None = None,
                              exclude_service: str | None = None) -> list[dict]:
        """Deferred episodes, oldest deferral first.

        Pass `service` for one owner, `exclude_service` for everything but
        one, neither for every deferred row.
        """
        clause, params = self._deferred_service_clause(
            service, exclude_service, 'e.deferred_service')
        conn = self.get_connection()
        cursor = conn.execute(
            f"""SELECT e.*, p.slug AS podcast_slug, p.title AS podcast_title
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.status = 'deferred'
                  {clause}
                ORDER BY e.deferred_at ASC""",  # noqa: S608
            params
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_deferred_episodes(self, service: str | None = None,
                                exclude_service: str | None = None) -> int:
        """Number of deferred episodes; filters as get_deferred_episodes."""
        clause, params = self._deferred_service_clause(
            service, exclude_service, 'deferred_service')
        conn = self.get_connection()
        row = conn.execute(
            f"""SELECT COUNT(*) AS n FROM episodes
                WHERE status = 'deferred'
                  {clause}""",  # noqa: S608
            params
        ).fetchone()
        return row['n'] if row else 0

    def expire_deferred_episodes(self, ttl_hours: int,
                                 service: str | None = None,
                                 exclude_service: str | None = None,
                                 label: str = 'Offline queue') -> list[dict]:
        """Fail deferred episodes whose TTL has run out, in the caller's scope.

        Filters as get_deferred_episodes; `label` names the owner in the
        message and the log. Rows are marked permanently_failed (a plain
        'failed' would be resurrected by the reset_failed_queue_items retry
        ladder) and the matching auto_process_queue row is closed the same
        way. Returns the expired rows so the caller can fire failure webhooks.
        """
        clause, params = self._deferred_service_clause(
            service, exclude_service, 'e.deferred_service')
        conn = self.get_connection()
        rows = conn.execute(
            f"""SELECT e.id, e.podcast_id, e.episode_id, e.title, e.error_message,
                       p.slug AS podcast_slug, p.title AS podcast_title
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.status = 'deferred'
                  {clause}
                  AND datetime(e.deferred_at) < datetime('now', '-' || ? || ' hours')""",  # noqa: S608
            params + [ttl_hours]
        ).fetchall()
        expired = []
        for row in rows:
            row = dict(row)
            message = (f"{label} TTL expired after {ttl_hours} hours: "
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
                "%s TTL expired for %s:%s after %dh; marking permanently_failed",
                label, row['podcast_slug'], row['episode_id'], ttl_hours,
            )
            expired.append(row)
        conn.commit()
        return expired

    def requeue_deferred_episodes(self, services: set[str]) -> int:
        """Flip deferred episodes back to pending for the given services.

        Callers pass exactly the set they own: the offline tick passes
        probe-derived reachable services (never 'llm_rate_limit', whose
        release waits on the hold's reset tick instead of a probe).

        Each episode gets its auto_process_queue row upserted to pending (the
        background processor's atomic claim drives it from there).
        deferred_service NULL reads as llm. deferred_at is deliberately
        KEPT: it marks the first entry into the offline queue, so the TTL
        keeps ticking across re-drive cycles (success and TTL expiry clear
        it). Episodes on auto-process-disabled feeds without a user-initiated
        reprocess stay deferred -- the claim-time gate would otherwise close
        their queue row and strand them in 'pending' outside every ladder.
        """
        requeued = 0
        for episode in self.get_deferred_episodes():
            service = episode.get('deferred_service') or DEFER_SERVICE_LLM
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
                "%s released, re-queued %s:%s",
                service, slug, episode['episode_id'],
            )
            requeued += 1
        return requeued

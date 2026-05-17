"""Background tasks: background_rss_refresh, background_queue_processor, run_cleanup, reset_stuck."""
import logging
import os
import shutil
import time

from config import MAX_EPISODE_RETRIES
from utils.constants import EpisodeStatus
# Singletons are bound in main_app/__init__.py before this submodule
# is loaded by the explicit `from main_app.background import ...` at
# the bottom of that file, so the apparent circular import is safe.
# Replaces a positional 5-tuple from _get_components() that the audit
# flagged as silently break-on-reorder.
from main_app import db, storage, shutdown_event, processing_queue, status_service

refresh_logger = logging.getLogger('podcast.refresh')
audio_logger = logging.getLogger('podcast.audio')


def run_cleanup():
    """Run episode cleanup based on retention period."""
    try:
        reset_count, freed_mb = db.cleanup_old_episodes(storage=storage)
        if reset_count > 0:
            refresh_logger.info(f"Cleanup: reset {reset_count} episodes to discovered, freed {freed_mb:.1f} MB")
    except Exception as e:
        refresh_logger.error(f"Cleanup failed: {e}")

    # Clean orphan podcast directories (podcasts deleted from DB but directories remain)
    try:
        valid_slugs = {p['slug'] for p in db.get_all_podcasts()}
        podcast_base = os.path.join(storage.data_dir, 'podcasts')
        if os.path.exists(podcast_base):
            for slug in os.listdir(podcast_base):
                if slug not in valid_slugs:
                    orphan_path = os.path.join(podcast_base, slug)
                    if os.path.isdir(orphan_path):
                        refresh_logger.warning(f"Removing orphan podcast directory: {slug}")
                        shutil.rmtree(orphan_path, ignore_errors=True)
    except Exception as e:
        refresh_logger.error(f"Orphan cleanup failed: {e}")

    # Periodic search index rebuild (every 6 hours)
    try:
        last_rebuild = getattr(run_cleanup, '_last_index_rebuild', 0)
        if time.time() - last_rebuild > 21600:
            count = db.rebuild_search_index()
            run_cleanup._last_index_rebuild = time.time()
            refresh_logger.info(f"Periodic search index rebuild: {count} items indexed")
    except Exception as e:
        refresh_logger.error(f"Search index rebuild failed: {e}")


def background_rss_refresh():
    """Background task to refresh RSS feeds every 15 minutes.

    Uses shutdown_event.wait() instead of time.sleep() to allow
    graceful shutdown interruption.
    """
    from main_app.feeds import refresh_all_feeds
    from pricing_fetcher import refresh_pricing_if_stale
    from community_sync import community_pattern_sync_tick
    while not shutdown_event.is_set():
        refresh_all_feeds()
        run_cleanup()
        refresh_pricing_if_stale()  # TTL-gated, fetches once per 24h
        # Community pattern sync -- gated by settings.community_sync_enabled
        # and the cron schedule; safe to call every tick.
        try:
            community_pattern_sync_tick(db)
        except Exception as e:
            refresh_logger.warning(f"community_pattern_sync_tick failed: {e}")
        # Wait 15 minutes, but allow early exit on shutdown
        shutdown_event.wait(timeout=900)


def background_queue_processor():
    """Background task to process queued episodes for auto-processing.

    Uses shutdown_event for graceful shutdown support.
    """
    from main_app.processing import start_background_processing
    refresh_logger.info("Auto-process queue processor started")
    backoff_seconds = 30  # Initial backoff for busy queue
    orphan_check_interval = 0  # Counter for orphan check (every 10 iterations)
    while not shutdown_event.is_set():
        try:
            # Periodically check for orphaned queue items (every ~5 minutes)
            orphan_check_interval += 1
            if orphan_check_interval >= 10:
                orphan_check_interval = 0
                reset_count, failed_count = db.reset_orphaned_queue_items(stuck_minutes=65)
                if reset_count > 0 or failed_count > 0:
                    refresh_logger.info(f"Reset {reset_count} orphaned queue items, {failed_count} exceeded max attempts")

                retry_count = db.reset_failed_queue_items(max_retries=MAX_EPISODE_RETRIES)
                if retry_count > 0:
                    refresh_logger.info(f"Reset {retry_count} failed queue items for automatic retry")

            # Get next queued episode
            queued = db.get_next_queued_episode()

            if queued:
                queue_id = queued['id']
                slug = queued['podcast_slug']
                episode_id = queued['episode_id']
                original_url = queued['original_url']
                title = queued.get('title', 'Unknown')
                podcast_name = queued.get('podcast_title', slug)
                published_at = queued.get('published_at')
                description = queued.get('description')

                # Auto-process gate: skip if disabled, UNLESS the episode was
                # explicitly reprocessed by a user (reprocess_requested_at set).
                if not db.is_auto_process_enabled_for_podcast(slug):
                    episode_row = db.get_episode(slug, episode_id)
                    user_requested = bool(episode_row and episode_row.get('reprocess_requested_at'))
                    if not user_requested:
                        db.update_queue_status(queue_id, 'completed', 'Auto-process disabled for this feed')
                        refresh_logger.info(f"[{slug}:{episode_id}] Skipped - auto-process disabled for this feed")
                        continue
                    refresh_logger.info(f"[{slug}:{episode_id}] Auto-process disabled but user-initiated reprocess; honoring")

                refresh_logger.info(f"[{slug}:{episode_id}] Auto-processing queued episode: {title}")

                try:
                    # Try to start background processing using the existing queue
                    started, reason = start_background_processing(
                        slug, episode_id, original_url, title, podcast_name, description, None, published_at
                    )

                    if started:
                        # Only mark as processing AFTER we successfully acquired the lock
                        db.update_queue_status(queue_id, 'processing')
                        # Reset backoff on successful start
                        backoff_seconds = 30
                        # Wait for processing to complete (poll status).
                        # Cap at the hard timeout so this waiter outlives a slow
                        # but successful job when the user has raised the limit.
                        from processing_timeouts import get_hard_timeout
                        max_wait = get_hard_timeout()
                        waited = 0
                        while waited < max_wait and not shutdown_event.is_set():
                            shutdown_event.wait(timeout=10)
                            waited += 10
                            episode = db.get_episode(slug, episode_id)
                            if episode and episode['status'] in ('processed', 'failed', 'permanently_failed'):
                                break

                        # Check final status
                        episode = db.get_episode(slug, episode_id)
                        if episode and episode['status'] == 'processed':
                            db.update_queue_status(queue_id, 'completed')
                            refresh_logger.info(f"[{slug}:{episode_id}] Auto-process completed successfully")
                        elif episode and episode['status'] == 'processing':
                            # Still processing after timeout - don't mark as failed, let it continue
                            # Put back in queue to check again later
                            db.update_queue_status(queue_id, 'pending')
                            refresh_logger.info(f"[{slug}:{episode_id}] Still processing after {max_wait}s, will check again later")
                        else:
                            # Actually failed - get the real error message
                            error_msg = episode.get('error_message') if episode else None
                            if not error_msg:
                                error_msg = f"Processing ended with status: {episode.get('status') if episode else 'unknown'}"
                            db.update_queue_status(queue_id, 'failed', error_msg)
                            episode_status = episode.get('status') if episode else None
                            if episode_status == EpisodeStatus.PERMANENTLY_FAILED:
                                refresh_logger.warning(f"[{slug}:{episode_id}] Auto-process permanently failed: {error_msg}")
                            else:
                                refresh_logger.info(f"[{slug}:{episode_id}] Auto-process failed (transient), will auto-retry: {error_msg}")
                    elif reason == "already_processing":
                        # Episode is already being processed, wait with backoff
                        refresh_logger.info(f"[{slug}:{episode_id}] Already processing, waiting {backoff_seconds}s...")
                        shutdown_event.wait(timeout=backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 300)  # Max 5 minutes
                    else:
                        # Queue is busy with another episode, try again later with backoff
                        db.update_queue_status(queue_id, 'pending')  # Put back in queue
                        refresh_logger.debug(f"[{slug}:{episode_id}] Queue busy, will retry in {backoff_seconds}s")
                        shutdown_event.wait(timeout=backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 300)  # Max 5 minutes

                except Exception as e:
                    db.update_queue_status(queue_id, 'failed', str(e))
                    refresh_logger.error(f"[{slug}:{episode_id}] Auto-process error: {e}")

            else:
                # No queued episodes, wait before checking again
                shutdown_event.wait(timeout=30)

            # Periodically clean up completed queue items
            db.clear_completed_queue_items(older_than_hours=24)

        except Exception as e:
            refresh_logger.error(f"Queue processor error: {e}")
            shutdown_event.wait(timeout=60)  # Wait before retrying on error


def reset_stuck_processing_episodes():
    """Reset any episodes stuck in 'processing' status from previous crash.

    Only resets episodes that have been processing for longer than 30 minutes
    to avoid killing actively-processing jobs when a worker restarts.

    Does NOT increment retry_count for orphan resets -- infrastructure crashes
    (SIGKILL, OOM, worker timeout) are not processing failures. Only actual
    processing errors (via _handle_processing_failure) increment retry_count.
    Episodes are marked permanently_failed only when retry_count (from real
    failures) reaches MAX_EPISODE_RETRIES.
    """
    conn = db.get_connection()
    cursor = conn.execute(
        """SELECT e.id, e.episode_id, e.retry_count, p.slug
           FROM episodes e
           JOIN podcasts p ON e.podcast_id = p.id
           WHERE e.status = 'processing'
             AND datetime(e.updated_at) < datetime('now', '-30 minutes')"""
    )
    stuck = cursor.fetchall()

    reset_count = 0
    failed_count = 0

    for row in stuck:
        current_retry_count = row['retry_count'] or 0

        if current_retry_count >= MAX_EPISODE_RETRIES:
            # Already exceeded retries from real failures - mark as permanently failed
            refresh_logger.warning(
                f"Marking episode as permanently_failed (retry_count={current_retry_count}): "
                f"{row['slug']}/{row['episode_id']}"
            )
            conn.execute(
                """UPDATE episodes SET
                   status = 'permanently_failed',
                   error_message = 'Exceeded retry limit after repeated processing failures'
                   WHERE id = ?""",
                (row['id'],)
            )
            failed_count += 1
        else:
            # Reset to pending without incrementing retry_count (orphan != failure)
            refresh_logger.info(
                f"Resetting stuck episode (no retry penalty, retry_count={current_retry_count}): "
                f"{row['slug']}/{row['episode_id']}"
            )
            conn.execute(
                """UPDATE episodes SET
                   status = 'pending',
                   error_message = 'Reset after worker crash (no retry penalty)'
                   WHERE id = ?""",
                (row['id'],)
            )
            reset_count += 1

    conn.commit()

    if stuck:
        refresh_logger.info(
            f"Stuck episode cleanup: {reset_count} reset to pending, "
            f"{failed_count} marked permanently_failed"
        )

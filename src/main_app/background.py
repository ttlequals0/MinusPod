"""Background tasks: background_rss_refresh, background_queue_processor, run_cleanup, reset_stuck."""
import logging
import os
import shutil
import threading
import time

import run_log
from config import (
    MAX_EPISODE_RETRIES, resolve_episode_log_retention_days,
    title_matches_skip_patterns,
)
from utils.constants import CANCELED_ERROR_MESSAGE, EpisodeStatus
from whisper_pool import get_pool
# Singletons are bound in main_app/__init__.py before this submodule
# is loaded by the explicit `from main_app.background import ...` at
# the bottom of that file, so the apparent circular import is safe.
from main_app import db, storage, shutdown_event
from recents_feed import rebuild_recents_feed

refresh_logger = logging.getLogger('podcast.refresh')
audio_logger = logging.getLogger('podcast.audio')

# Set once this process wins the background-leader lock at startup (see
# _try_become_background_leader in main_app/__init__.py). Gates episode runs
# while the whisper pool is active so per-run state stays in one process.
_is_leader = False
IDLE_WAIT_SECONDS = 5.0
MAINTENANCE_INTERVAL_SECONDS = 300.0


def mark_background_leader() -> None:
    global _is_leader
    _is_leader = True


def is_background_leader() -> bool:
    return _is_leader


def _run_tick(tick_fn, name):
    """Run one scheduled tick, logging (never raising) on failure."""
    try:
        tick_fn(db)
    except Exception as e:
        refresh_logger.warning(f"{name} failed: {e}")
        # A tick that died mid-write may have left a transaction open; clear
        # it here so the next write in this iteration cannot commit the
        # tick's partial work (issue #566).
        db.clear_leaked_transaction(refresh_logger, name)


def run_cleanup():
    """Run episode cleanup based on retention period."""
    try:
        reset_count, freed_mb = db.cleanup_old_episodes(storage=storage)
        if reset_count > 0:
            refresh_logger.info(f"Cleanup: reset {reset_count} episodes to discovered, freed {freed_mb:.1f} MB")
            rebuild_recents_feed()
    except Exception as e:
        refresh_logger.error(f"Cleanup failed: {e}")
        db.clear_leaked_transaction(refresh_logger, 'cleanup')

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

    # Episode run logs (#660): prune by age and drop files no row points at.
    # Hourly at most; the cleanup tick runs far more often than the log tree
    # changes, and the sweep walks every stored log.
    try:
        last_sweep = getattr(run_cleanup, '_last_run_log_sweep', 0)
        if time.time() - last_sweep > 3600:
            run_cleanup._last_run_log_sweep = time.time()
            pruned, orphans = run_log.sweep_expired_logs(
                db, storage.data_dir, resolve_episode_log_retention_days(db))
            if pruned or orphans:
                refresh_logger.info(
                    f"Cleanup: removed {pruned} expired run log(s) and {orphans} orphan file(s)")
    except Exception as e:
        refresh_logger.error(f"Run log cleanup failed: {e}")

    # Periodic search index rebuild (every 6 hours). rebuild_search_index
    # logs "Search index rebuilt with N items" itself, so no duplicate log
    # line is needed at this caller.
    try:
        last_rebuild = getattr(run_cleanup, '_last_index_rebuild', 0)
        if time.time() - last_rebuild > 21600:
            db.rebuild_search_index()
            run_cleanup._last_index_rebuild = time.time()
    except Exception as e:
        refresh_logger.error(f"Search index rebuild failed: {e}")
        db.clear_leaked_transaction(refresh_logger, 'search index rebuild')


def background_rss_refresh():
    """Background task to refresh RSS feeds on a configurable interval,
    default 15 minutes.

    Uses shutdown_event.wait() instead of time.sleep() to allow
    graceful shutdown interruption.
    """
    from main_app.feeds import refresh_all_feeds
    from pricing_fetcher import refresh_pricing_if_stale
    from community_sync import community_pattern_sync_tick
    from db_backup_service import db_backup_tick
    from update_checker import update_check_tick
    while not shutdown_event.is_set():
        refresh_all_feeds()
        run_cleanup()
        refresh_pricing_if_stale()  # TTL-gated, fetches once per 24h
        # Community pattern sync -- gated by settings.community_sync_enabled
        # and the cron schedule; safe to call every tick.
        _run_tick(community_pattern_sync_tick, 'community_pattern_sync_tick')
        # Scheduled DB backup -- gated by settings.db_backup_enabled and the
        # cron schedule; safe to call every tick.
        _run_tick(db_backup_tick, 'db_backup_tick')
        # Daily update check -- gated by settings.update_check_enabled and a
        # 24h internal timer; safe to call every tick.
        _run_tick(update_check_tick, 'update_check_tick')
        # Guard point for issue #566: a tick that swallowed a write failure
        # may have left a transaction open. Clear it before the long sleep
        # so it cannot block other writers for the whole interval.
        db.clear_leaked_transaction(refresh_logger, 'refresh loop')
        try:
            interval_minutes = int(db.get_setting('rss_refresh_interval_minutes') or 15)
        except (TypeError, ValueError):
            interval_minutes = 15
        interval_minutes = min(max(interval_minutes, 5), 1440)
        # Wait, but allow early exit on shutdown. A changed setting applies
        # after the current wait completes.
        shutdown_event.wait(timeout=interval_minutes * 60)


def _run_claimed_episode(queued: dict, running: set) -> bool:
    """Handle one claim's gates and start inline; the run itself polls on its own thread.

    Returns False on a bounce (dispatcher backs off before reclaiming); True once the row is consumed.
    """
    try:
        queue_id = queued['id']
        slug = queued['podcast_slug']
        episode_id = queued['episode_id']
        original_url = queued['original_url']
        title = queued.get('title', 'Unknown')
        podcast_name = queued.get('podcast_title', slug)
        published_at = queued.get('published_at')
        description = queued.get('description')
    except Exception as e:
        # Malformed claim row: nothing has touched it yet, so put it back
        # rather than mark it failed.
        queue_id = queued.get('id') if isinstance(queued, dict) else None
        refresh_logger.error(f"Auto-process error reading claimed row {queue_id}: {e}")
        if queue_id is not None:
            db.clear_leaked_transaction(refresh_logger, 'auto-process error path')
            db.close_claimed_queue_row(queue_id, 'pending')
        return False

    try:
        from main_app.processing import start_background_processing

        # Every status write below reports on the row this claim
        # holds, so all of them go through close_claimed_queue_row.
        # One podcast fetch feeds both gates below (auto-process
        # override and title_skip_patterns live on the same row).
        podcast = db.get_podcast_by_slug(slug)
        auto_process_enabled = db.is_auto_process_enabled_for_podcast(slug, podcast=podcast)
        title_blacklisted = title_matches_skip_patterns(
            title, podcast.get('title_skip_patterns') if podcast else None)

        # An explicit user reprocess bypasses both gates below; only
        # fetched when a gate would otherwise skip this claim.
        user_requested = False
        if not auto_process_enabled or title_blacklisted:
            episode_row = db.get_episode(slug, episode_id)
            user_requested = bool(episode_row and episode_row.get('reprocess_requested_at'))

        # Auto-process gate (inside the try so a gate error reverts the
        # claimed row instead of leaving it stuck in 'processing'): skip
        # if disabled, UNLESS the episode was explicitly reprocessed by a
        # user (reprocess_requested_at set).
        if not auto_process_enabled:
            if not user_requested:
                db.close_claimed_queue_row(queue_id, 'completed', 'Auto-process disabled for this feed')
                refresh_logger.info(f"[{slug}:{episode_id}] Skipped - auto-process disabled for this feed")
                return True
            refresh_logger.info(f"[{slug}:{episode_id}] Auto-process disabled but user-initiated reprocess; honoring")

        # Title blacklist gate: mirrors the auto-process gate above,
        # also bypassed by an explicit user reprocess.
        if title_blacklisted and not user_requested:
            db.close_claimed_queue_row(queue_id, 'completed', 'skipped: title blacklist')
            refresh_logger.info(f"[{slug}:{episode_id}] Skipped - title blacklist match: {title}")
            return True

        refresh_logger.info(f"[{slug}:{episode_id}] Auto-processing queued episode: {title}")

        # Try to start background processing using the existing queue
        started, reason = start_background_processing(
            slug, episode_id, original_url, title, podcast_name, description, None, published_at
        )

        if started:
            # Row stays claimed 'processing'. A daemon thread polls it to
            # completion and files the verdict, freeing this thread to keep
            # claiming up to the pool's remaining slots.
            waiter = threading.Thread(
                target=_wait_for_claimed_episode, args=(queue_id, slug, episode_id),
                daemon=True, name=f"episode-{slug}-{episode_id}")
            running.add(waiter)
            waiter.start()
            return True
        elif reason == "already_processing":
            # Episode is already being processed elsewhere. Release our
            # claim back to 'pending'; the dispatcher backs off before retrying.
            db.close_claimed_queue_row(queue_id, 'pending')
            refresh_logger.info(f"[{slug}:{episode_id}] Already processing, will retry")
            return False
        else:
            # Queue busy or rate-limit paused; put back in queue and back off.
            db.close_claimed_queue_row(queue_id, 'pending')
            refresh_logger.debug(f"[{slug}:{episode_id}] Queue busy, will retry")
            return False

    except Exception as e:
        # Clear any leaked transaction before the status write so
        # the bookkeeping cannot commit partial work (issue #566).
        db.clear_leaked_transaction(refresh_logger, 'auto-process error path')
        db.close_claimed_queue_row(queue_id, 'failed', str(e))
        refresh_logger.error(f"[{slug}:{episode_id}] Auto-process error: {e}")
        return True


def _wait_for_claimed_episode(queue_id: int, slug: str, episode_id: str) -> None:
    """Poll a started run to completion and close its claimed row with the verdict."""
    from processing_queue import ProcessingQueue
    from rate_limit_hold import is_queue_paused
    try:
        # Wait for processing to complete (poll status).
        # Cap at the hard timeout so this waiter outlives a slow
        # but successful job when the user has raised the limit.
        from processing_timeouts import get_hard_timeout
        max_wait = get_hard_timeout()
        waited = 0
        queue = ProcessingQueue()
        # Consecutive polls where the row says processing but no
        # worker holds the lock. One poll of grace lets a job
        # that just finished write its status first.
        orphan_polls = 0
        while waited < max_wait and not shutdown_event.is_set():
            shutdown_event.wait(timeout=10)
            waited += 10
            episode = db.get_episode(slug, episode_id)
            if episode and episode['status'] in ('processed', 'failed', 'permanently_failed', 'deferred'):
                break
            # A rate-limit hold put the row back to pending.
            if episode and episode['status'] == 'pending' and is_queue_paused(db):
                break
            if queue.is_processing(slug, episode_id):
                orphan_polls = 0
                continue
            orphan_polls += 1
            if orphan_polls >= 2:
                row_status = episode.get('status') if episode else 'missing'
                refresh_logger.warning(
                    f"[{slug}:{episode_id}] Row says {row_status} but no worker holds "
                    f"the lock after {waited}s; treating as orphaned"
                )
                break

        # The job writes its status before its finally block drops
        # the lock; wait that gap out so the next claim does not trip
        # acquire's same-process rejection.
        for _ in range(60):
            if shutdown_event.is_set() or not queue.is_processing(slug, episode_id):
                break
            shutdown_event.wait(timeout=0.5)

        # Check final status
        episode = db.get_episode(slug, episode_id)
        if episode and episode['status'] == 'processed':
            # finalize already closes the row on success, so only a
            # row back in 'pending' means the run queued a rerun.
            if (db.close_claimed_queue_row(queue_id, 'completed')
                    or db.get_queue_row_status(queue_id) != 'pending'):
                refresh_logger.info(f"[{slug}:{episode_id}] Auto-process completed successfully")
            else:
                refresh_logger.info(
                    f"[{slug}:{episode_id}] Auto-process completed; a rerun was "
                    f"queued during the run and stays queued")
        elif episode and episode['status'] == 'processing':
            # Still running: requeue rather than fail. The next
            # claim restarts an orphan, since the lock gates a
            # start, not the row's status.
            db.close_claimed_queue_row(queue_id, 'pending')
            if queue.is_processing(slug, episode_id):
                refresh_logger.info(f"[{slug}:{episode_id}] Still processing after {waited}s, will check again later")
            else:
                refresh_logger.warning(f"[{slug}:{episode_id}] Orphaned after {waited}s with no worker on it; requeued")
        elif episode and episode['status'] == 'deferred':
            # Offline queue (#482) owns the episode now. Close
            # the row so it is not counted as a failure (the
            # retry ladder would skip it anyway); the re-drive
            # re-opens it as pending once the service is back.
            db.close_claimed_queue_row(queue_id, 'completed')
            refresh_logger.info(f"[{slug}:{episode_id}] Deferred to offline queue (endpoint unreachable)")
        elif (episode and episode['status'] == 'pending'
                and is_queue_paused(db)):
            # The failure handler already reopened the row as
            # pending; it is claimed again after the reset.
            refresh_logger.info(f"[{slug}:{episode_id}] Paused by rate-limit hold; stays queued")
        elif (episode and episode['status'] == 'pending'
                and episode.get('error_message') == CANCELED_ERROR_MESSAGE):
            # Only a user cancel closes the row. The stuck-row
            # sweep also writes 'pending', and that one still
            # needs the retry ladder.
            db.close_claimed_queue_row(queue_id, 'completed')
            refresh_logger.info(f"[{slug}:{episode_id}] Cancelled; queue row closed")
        else:
            # Actually failed - get the real error message
            error_msg = episode.get('error_message') if episode else None
            if not error_msg:
                error_msg = f"Processing ended with status: {episode.get('status') if episode else 'unknown'}"
            wrote = db.close_claimed_queue_row(queue_id, 'failed', error_msg)
            episode_status = episode.get('status') if episode else None
            if not wrote and db.get_queue_row_status(queue_id) == 'pending':
                refresh_logger.info(
                    f"[{slug}:{episode_id}] Run ended as {episode_status}; a rerun "
                    f"was queued during the run and stays queued")
            elif episode_status == EpisodeStatus.PERMANENTLY_FAILED:
                refresh_logger.warning(f"[{slug}:{episode_id}] Auto-process permanently failed: {error_msg}")
            else:
                refresh_logger.info(f"[{slug}:{episode_id}] Auto-process failed (transient), will auto-retry: {error_msg}")

    except Exception as e:
        # Clear any leaked transaction before the status write so
        # the bookkeeping cannot commit partial work (issue #566).
        db.clear_leaked_transaction(refresh_logger, 'auto-process error path')
        db.close_claimed_queue_row(queue_id, 'failed', str(e))
        refresh_logger.error(f"[{slug}:{episode_id}] Auto-process error: {e}")


def background_queue_processor():
    """Dispatcher: keep up to the pool's max_episodes claimed rows running."""
    from offline_queue import offline_queue_tick
    from rate_limit_hold import get_hold_until, hold_is_active, rate_limit_hold_tick
    refresh_logger.info("Auto-process queue processor started")
    running: set[threading.Thread] = set()
    # Backdated so the very first pass always runs the maintenance block.
    last_maintenance = time.monotonic() - MAINTENANCE_INTERVAL_SECONDS
    backoff = 30  # Initial backoff for a bounced claim
    rate_limit_pause_logged = False
    while not shutdown_event.is_set():
        # Guard point for issue #566 (see Database.rollback_open_transaction).
        db.clear_leaked_transaction(refresh_logger, 'queue processor')
        try:
            # Orphan/failed-item sweep, stuck-row reset, offline-queue drive:
            # every 5 minutes of wall-clock time (the first pass runs it too).
            if time.monotonic() - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
                last_maintenance = time.monotonic()
                reset_count, failed_count = db.reset_orphaned_queue_items(stuck_minutes=65)
                if reset_count > 0 or failed_count > 0:
                    refresh_logger.info(f"Reset {reset_count} orphaned queue items, {failed_count} exceeded max attempts")

                retry_count = db.reset_failed_queue_items(max_retries=MAX_EPISODE_RETRIES)
                if retry_count > 0:
                    refresh_logger.info(f"Reset {retry_count} failed queue items for automatic retry")

                # Episode rows orphaned in 'processing' by a killed worker.
                # This ran at startup only, so a row could sit unprocessable
                # until the next restart.
                reset_stuck_processing_episodes()

                # Offline queue (#482): expire deferred episodes past their
                # TTL and re-queue the rest once their service is reachable.
                _run_tick(offline_queue_tick, 'offline_queue_tick')

            for waiter in list(running):
                if not waiter.is_alive():
                    running.discard(waiter)

            # Rate-limit pause gate (#696): every claim waits for the
            # provider's reset, then the tick drops the stale marker.
            hold_until = get_hold_until(db)
            if hold_is_active(hold_until):
                if not rate_limit_pause_logged:
                    refresh_logger.info(
                        "Queue paused: LLM provider rate limit; waiting for reset")
                    rate_limit_pause_logged = True
                shutdown_event.wait(timeout=30)
                continue
            rate_limit_pause_logged = False
            if hold_until:
                _run_tick(rate_limit_hold_tick, 'rate_limit_hold_tick')

            # Refreshed every pass (cheap: the settings reader has its own
            # TTL) so an operator raising max_episodes takes effect without
            # a restart.
            pool = get_pool()
            pool.refresh()
            limit = pool.max_episodes
            claimed_any = False
            bounced = False
            while len(running) < limit and not shutdown_event.is_set():
                queued = db.claim_next_queued_episode()
                if not queued:
                    break
                claimed_any = True
                if _run_claimed_episode(queued, running):
                    backoff = 30
                else:
                    bounced = True
                    break

            # Periodically clean up completed queue items
            db.clear_completed_queue_items(older_than_hours=24)
            if bounced:
                # A claim bounced (busy elsewhere, rate-limited); back off
                # instead of reclaiming the same row at full speed.
                shutdown_event.wait(timeout=backoff)
                backoff = min(backoff * 2, 300)  # Max 5 minutes
            elif not claimed_any:
                # No queued episodes, wait before checking again
                shutdown_event.wait(timeout=IDLE_WAIT_SECONDS if pool.active else 30)
            elif limit == 1 and running:
                # Inactive pool with a run in flight: wait for it as before.
                # A gate-skipped claim adds nothing to `running`, so there is
                # nothing to join; the next pass claims again immediately.
                next(iter(running)).join()

        except Exception as e:
            refresh_logger.error(f"Queue processor error: {e}")
            db.clear_leaked_transaction(refresh_logger, 'queue processor error path')
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
    from processing_queue import ProcessingQueue

    conn = db.get_connection()
    cursor = conn.execute(
        """SELECT e.id, e.episode_id, e.retry_count, p.slug
           FROM episodes e
           JOIN podcasts p ON e.podcast_id = p.id
           WHERE e.status = 'processing'
             AND datetime(e.updated_at) < datetime('now', '-30 minutes')"""
    )
    stuck = cursor.fetchall()

    # Age alone cannot distinguish a slow pass from a crash; the lock can. A row
    # is only written at start and at ad_detection_status, so a long
    # transcription looks stale while the job is very much alive.
    running = ProcessingQueue().get_current()
    reset_count = 0
    failed_count = 0

    for row in stuck:
        if (row['slug'], row['episode_id']) in running:
            continue

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

    if reset_count or failed_count:
        refresh_logger.info(
            f"Stuck episode cleanup: {reset_count} reset to pending, "
            f"{failed_count} marked permanently_failed"
        )

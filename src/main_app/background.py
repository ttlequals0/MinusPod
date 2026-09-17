"""Background tasks: background_rss_refresh, background_queue_processor, run_cleanup, reset_stuck."""
import logging
import math
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

import run_log
from config import (
    FEED_REFRESH_OUTAGE_MIN_FEEDS, MAX_EPISODE_RETRIES,
    resolve_episode_log_retention_days, title_matches_skip_patterns,
)
from database.queue import PENDING_QUEUE_LIMIT
from utils.constants import CANCELED_ERROR_MESSAGE, EpisodeStatus
from utils.time import parse_iso_utc
from whisper_pool import get_pool
# Singletons are bound in main_app/__init__.py before this submodule
# is loaded by the explicit `from main_app.background import ...` at
# the bottom of that file, so the apparent circular import is safe.
from main_app import db, storage, shutdown_event
from recents_feed import rebuild_recents_feed

refresh_logger = logging.getLogger('podcast.refresh')
audio_logger = logging.getLogger('podcast.audio')

IDLE_WAIT_SECONDS = 5.0
# Idle wait while a hold blocks every pending row: the scan reads up to
# BLOCKED_SCAN_MAX_ROWS, so repeating it on the short tick is wasted work.
HELD_IDLE_WAIT_SECONDS = 30.0
MAINTENANCE_INTERVAL_SECONDS = 300.0
# The refresh loop wakes on this short tick and refreshes only the feeds due
# this tick, so feed writes spread across the interval instead of landing in
# one whole-corpus burst that starves every other writer.
REFRESH_TICK_SECONDS = 60.0
# Shared-outage retry: each consecutive outage pass doubles the shortened
# wait, up to the normal refresh interval.
OUTAGE_RETRY_MAX_DOUBLINGS = 4
# Hold gating scans pending rows a page at a time and keeps paging only while
# every row of the page is blocked, so a pass that claims several rows does
# not meet an unscored one.
BLOCKED_SCAN_PAGE = PENDING_QUEUE_LIMIT
BLOCKED_SCAN_MAX_ROWS = 1000
# Cooldown before a failed search index rebuild is attempted again. The
# rebuild is a whole-corpus write, so retrying it every pass feeds the
# lock contention that made it fail.
INDEX_REBUILD_RETRY_SECONDS = 2700
INDEX_REBUILD_INTERVAL_SECONDS = 21600
# Ceiling on deferring to active runs: an instance that is never idle would
# otherwise never rebuild at all.
INDEX_REBUILD_MAX_DEFERRAL_SECONDS = 2 * INDEX_REBUILD_INTERVAL_SECONDS


def _blocked_queue_entries(db, held_pairs: set, legacy_hold: bool = False) -> set:
    """Pending (slug, episode_id) pairs the dispatcher must skip while held.

    Scored per row because a pass-through override is: one feed can hold
    blocked and eligible rows at once. ``legacy_hold`` is the unscoped
    pre-migration marker, which blocks every row needing any LLM account.
    Unresolvable routes leave a row eligible for the per-run check to refuse.
    """
    from main_app.processing import (
        _admission_gates, _required_providers_for_admission, _resolve_route_snapshot,
    )
    # Both are slug-independent, so one resolution covers the whole scan.
    snapshot = _resolve_route_snapshot()
    if snapshot is None:
        return set()
    gates = _admission_gates(db)
    blocked: set = set()
    scanned = 0
    while scanned < BLOCKED_SCAN_MAX_ROWS:
        try:
            page = db.get_pending_queued_episodes(
                limit=BLOCKED_SCAN_PAGE, offset=scanned)
        except Exception as e:
            refresh_logger.warning(
                f"Could not list pending episodes for hold gating: {e}")
            return blocked
        if not page:
            return blocked
        scanned += len(page)
        page_all_blocked = True
        for row in page:
            entry = (row['podcast_slug'], row['episode_id'])
            required = _required_providers_for_admission(
                entry[0], entry[1], snapshot=snapshot, gates=gates, queue_row=row)
            needs_an_account = required is None or bool(required)
            if ((legacy_hold and needs_an_account)
                    or (required and held_pairs & set(required))):
                blocked.add(entry)
                continue
            page_all_blocked = False
        if not page_all_blocked or len(page) < BLOCKED_SCAN_PAGE:
            break
    return blocked


def _run_tick(tick_fn, name):
    """Run one scheduled tick, logging (never raising) on failure.

    Returns the tick's own return value, or None if it raised.
    """
    try:
        return tick_fn(db)
    except Exception as e:
        refresh_logger.warning(f"{name} failed: {e}")
        # A tick that died mid-write may have left a transaction open; clear
        # it here so the next write in this iteration cannot commit the
        # tick's partial work (issue #566).
        db.clear_leaked_transaction(refresh_logger, name)
        return None


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
        from processing_queue import ProcessingQueue
        overdue_by = time.time() - getattr(run_cleanup, '_last_index_rebuild', 0)
        if overdue_by > INDEX_REBUILD_INTERVAL_SECONDS:
            # A whole-corpus rewrite alongside a live run is what produces the
            # "database is locked" failures; wait for an idle pass, but only
            # up to the deferral ceiling.
            if (overdue_by <= INDEX_REBUILD_MAX_DEFERRAL_SECONDS
                    and ProcessingQueue().slot_count() > 0):
                refresh_logger.debug("Search index rebuild deferred: a run is active")
            else:
                try:
                    db.rebuild_search_index()
                    run_cleanup._last_index_rebuild = time.time()
                except Exception as e:
                    refresh_logger.error(f"Search index rebuild failed: {e}")
                    db.clear_leaked_transaction(refresh_logger, 'search index rebuild')
                    # Back off instead of rebuilding again on the next pass: the
                    # retry feeds the same contention that made this attempt fail.
                    run_cleanup._last_index_rebuild = (
                        time.time() - INDEX_REBUILD_INTERVAL_SECONDS
                        + INDEX_REBUILD_RETRY_SECONDS)
    except Exception as e:
        # The rebuild has its own handler, so this is the slot lookup failing;
        # it touched no index, so the cadence stays as it was.
        refresh_logger.error(f"Search index rebuild check failed: {e}")


def _resolve_refresh_interval_seconds() -> float:
    """The configured refresh interval in seconds, clamped 5-1440 minutes."""
    try:
        interval_minutes = int(db.get_setting('rss_refresh_interval_minutes') or 15)
    except (TypeError, ValueError):
        interval_minutes = 15
    return min(max(interval_minutes, 5), 1440) * 60


def _due_batch_size(interval_seconds: float, active_count: int) -> int:
    """Per-tick feed cap that covers the whole set once per interval, floored at
    the outage sample size so a backlog still gives outage detection a batch to
    judge. The due-filter keeps steady-state cadence to once per interval."""
    ticks = max(interval_seconds / REFRESH_TICK_SECONDS, 1.0)
    steady = math.ceil(active_count / ticks)
    return max(steady, FEED_REFRESH_OUTAGE_MIN_FEEDS)


def background_rss_refresh():
    """Refresh RSS feeds on a short tick, staggering the due feeds across the
    configured interval (default 15 minutes) so their writes do not contend in
    one burst. Heavy maintenance runs behind its own timer, not every tick.

    Uses shutdown_event.wait() instead of time.sleep() to allow graceful
    shutdown interruption.
    """
    from main_app.feeds import refresh_due_feeds
    from pricing_fetcher import refresh_pricing_if_stale
    from community_sync import community_pattern_sync_tick
    from db_backup_service import db_backup_tick
    from update_checker import update_check_tick
    outage_passes = 0
    last_maintenance = 0.0
    while not shutdown_event.is_set():
        interval_seconds = _resolve_refresh_interval_seconds()
        batch = _due_batch_size(interval_seconds, db.count_subscribed_feeds())
        result = refresh_due_feeds(batch, interval_seconds)

        # Maintenance keeps its old once-per-interval cadence rather than
        # running on every short tick; run_cleanup's orphan-directory walk is
        # not internally time-gated.
        if time.monotonic() - last_maintenance >= interval_seconds:
            last_maintenance = time.monotonic()
            run_cleanup()
            refresh_pricing_if_stale()  # TTL-gated, fetches once per 24h
            # Each of these is gated by its own setting and schedule, so it is
            # safe to call on the maintenance cadence.
            _run_tick(community_pattern_sync_tick, 'community_pattern_sync_tick')
            _run_tick(db_backup_tick, 'db_backup_tick')
            _run_tick(update_check_tick, 'update_check_tick')
        # Guard point for issue #566: a tick that swallowed a write failure
        # may have left a transaction open. Clear it before the wait so it
        # cannot block other writers.
        db.clear_leaked_transaction(refresh_logger, 'refresh loop')
        wait_seconds = REFRESH_TICK_SECONDS

        # The tick already retries far sooner than the interval, so a shared
        # outage instead backs the wait off, doubling each consecutive pass up
        # to the interval so a down host is not hit every tick. Recovery drops
        # straight back to the tick.
        outage = result.get('outage') if isinstance(result, dict) else None
        if outage and outage.get('detected'):
            retry_at = parse_iso_utc(outage.get('nextRetryAt'))
            if retry_at:
                backoff = (retry_at - datetime.now(timezone.utc)).total_seconds()
                backoff *= 2 ** min(outage_passes, OUTAGE_RETRY_MAX_DOUBLINGS)
                if backoff > 0:
                    wait_seconds = max(wait_seconds, min(backoff, interval_seconds))
            outage_passes += 1
            # The refresh batch armed this setting at the base delay and the
            # system health panel renders it, so correct it to the attempt
            # this loop will actually make.
            try:
                db.set_setting(
                    'feeds_next_refresh_retry_at',
                    (datetime.now(timezone.utc)
                     + timedelta(seconds=wait_seconds)).isoformat())
            except Exception as e:
                refresh_logger.warning(f"Could not record the next refresh retry: {e}")
        else:
            outage_passes = 0

        # Wait, but allow early exit on shutdown. A changed setting applies
        # after the current wait completes.
        shutdown_event.wait(timeout=wait_seconds)


ClaimResult = Literal['started', 'skipped', 'bounced']


def _run_claimed_episode(queued: dict, running: set) -> ClaimResult:
    """Handle one claim's gates and start inline; the run itself polls on its own thread.

    'started': a waiter thread now owns the run. 'skipped': the row was closed
    (completed or failed) without starting a run. 'bounced': the row went back
    to 'pending' and the dispatcher should back off before reclaiming it.
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
        return 'bounced'

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
                return 'skipped'
            refresh_logger.info(f"[{slug}:{episode_id}] Auto-process disabled but user-initiated reprocess; honoring")

        # Title blacklist gate: mirrors the auto-process gate above,
        # also bypassed by an explicit user reprocess.
        if title_blacklisted and not user_requested:
            db.close_claimed_queue_row(queue_id, 'completed', 'skipped: title blacklist')
            refresh_logger.info(f"[{slug}:{episode_id}] Skipped - title blacklist match: {title}")
            return 'skipped'

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
            try:
                waiter.start()
            except Exception as e:
                # The run is already live (start_background_processing
                # returned True); a failed poller thread must not mark the
                # row failed. Leave it as 'processing' for the orphan sweep.
                refresh_logger.error(f"[{slug}:{episode_id}] Could not start claim waiter thread: {e}")
                return 'skipped'
            running.add(waiter)
            return 'started'
        elif reason == "already_processing":
            # Episode is already being processed elsewhere. Release our
            # claim back to 'pending'; the dispatcher backs off before retrying.
            db.close_claimed_queue_row(queue_id, 'pending')
            refresh_logger.info(f"[{slug}:{episode_id}] Already processing, will retry")
            return 'bounced'
        else:
            # Queue busy or rate-limit paused; put back in queue and back off.
            db.close_claimed_queue_row(queue_id, 'pending')
            refresh_logger.debug(f"[{slug}:{episode_id}] Queue busy, will retry")
            return 'bounced'

    except Exception as e:
        # Clear any leaked transaction before the status write so
        # the bookkeeping cannot commit partial work (issue #566).
        db.clear_leaked_transaction(refresh_logger, 'auto-process error path')
        db.close_claimed_queue_row(queue_id, 'failed', str(e))
        refresh_logger.error(f"[{slug}:{episode_id}] Auto-process error: {e}")
        return 'skipped'


def _wait_for_claimed_episode(queue_id: int, slug: str, episode_id: str) -> None:
    """Poll a started run to completion and close its claimed row with the verdict."""
    from processing_queue import ProcessingQueue
    from rate_limit_hold import any_hold_active
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
            episode = db.get_episode_state(slug, episode_id)
            if episode and episode['status'] in ('processed', 'failed', 'permanently_failed', 'deferred'):
                break
            # A rate-limit hold put the row back to pending.
            if episode and episode['status'] == 'pending' and any_hold_active(db):
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
                and any_hold_active(db)):
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
    from processing_queue import ProcessingQueue, is_processing_paused
    from rate_limit_hold import (
        active_held_pairs, get_active_hold, probe_rate_limit, rate_limit_hold_tick,
    )
    refresh_logger.info("Auto-process queue processor started")
    registry = ProcessingQueue()
    running: set[threading.Thread] = set()
    # Backdated so the very first pass always runs the maintenance block.
    last_maintenance = time.monotonic() - MAINTENANCE_INTERVAL_SECONDS
    backoff = 30  # Initial backoff for a bounced claim
    rate_limit_pause_logged = False
    processing_pause_logged = False
    while not shutdown_event.is_set():
        # Guard point for issue #566 (see Database.rollback_open_transaction).
        db.clear_leaked_transaction(refresh_logger, 'queue processor')
        try:
            # Orphan/failed-item sweep, stuck-row reset, offline-queue drive:
            # every 5 minutes of wall-clock time (the first pass runs it too).
            if time.monotonic() - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
                last_maintenance = time.monotonic()
                # A live slot means the run is still going, whatever the row's
                # age says: updated_at is only written at a stage change.
                reset_count, failed_count = db.reset_orphaned_queue_items(
                    stuck_minutes=65, exclude_running=registry.get_current())
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

                db.clear_completed_queue_items(older_than_hours=24)

            for waiter in list(running):
                if not waiter.is_alive():
                    running.discard(waiter)

            # Runs every pass, not only while held: the tick reaps expired
            # markers and fires the resume event, so the last hold to expire
            # still gets cleaned up (#696).
            _run_tick(rate_limit_hold_tick, 'rate_limit_hold_tick')
            # Holds defer only entries whose required account is held; the
            # unscoped legacy marker blocks every account, so under it only
            # work that needs no LLM account gets through.
            legacy_until, _ = get_active_hold(db)
            held_pairs = active_held_pairs(db)
            blocked_episodes: set = set()
            if legacy_until or held_pairs:
                if _run_tick(probe_rate_limit, 'rate_limit_probe'):
                    # The probe cleared a hold; re-read it all on a fresh pass.
                    rate_limit_pause_logged = False
                    continue
                legacy_until, _ = get_active_hold(db)
                held_pairs = active_held_pairs(db)
            if legacy_until:
                if not rate_limit_pause_logged:
                    refresh_logger.info(
                        "Queue paused: LLM provider rate limit; only work needing "
                        "no LLM account dispatches until reset")
                    rate_limit_pause_logged = True
            else:
                rate_limit_pause_logged = False
            if legacy_until or held_pairs:
                blocked_episodes = _blocked_queue_entries(
                    db, held_pairs, legacy_hold=bool(legacy_until))

            # Idle-wait while paused instead of claiming and bouncing, which
            # would ramp the backoff to its 5-minute ceiling and stall resume;
            # this bounds resume latency to one IDLE_WAIT_SECONDS pass.
            if is_processing_paused(db):
                if not processing_pause_logged:
                    refresh_logger.info(
                        "New processing paused by operator; queue holds until resumed")
                    processing_pause_logged = True
                shutdown_event.wait(timeout=IDLE_WAIT_SECONDS)
                continue
            processing_pause_logged = False

            # Refreshed every pass (cheap: the settings reader has its own
            # TTL) so an operator raising max_episodes takes effect without
            # a restart.
            pool = get_pool()
            pool.refresh()
            limit = pool.max_episodes
            claimed_any = False
            bounced = False
            nothing_to_claim = False
            # The registry, not `running`: it also sees runs a Play or
            # Reprocess started on this leader outside the dispatcher.
            while registry.slot_count() < limit and not shutdown_event.is_set():
                queued = db.claim_next_queued_episode(exclude_episodes=blocked_episodes)
                if not queued:
                    nothing_to_claim = True
                    break
                claimed_any = True
                result = _run_claimed_episode(queued, running)
                if result == 'started':
                    backoff = 30
                elif result == 'bounced':
                    bounced = True
                    break
                # 'skipped': row already closed, neither resets backoff nor bounces

            if bounced:
                # A claim bounced (busy elsewhere, rate-limited); back off
                # instead of reclaiming the same row at full speed.
                shutdown_event.wait(timeout=backoff)
                backoff = min(backoff * 2, 300)  # Max 5 minutes
            elif not claimed_any:
                # No queued episodes, wait before checking again. Under a hold
                # an empty claim means the scan just scored every pending row,
                # so wait longer than the idle tick before repeating it.
                if nothing_to_claim and (legacy_until or held_pairs):
                    shutdown_event.wait(timeout=HELD_IDLE_WAIT_SECONDS)
                else:
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

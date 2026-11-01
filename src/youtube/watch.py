"""SponsorBlock gate: schedule YouTube episodes before they enter the queue."""
import logging
from datetime import datetime, timedelta

from config import DEFER_SERVICE_SPONSORBLOCK
from database.queue import compute_queue_priority
from offline_queue import record_probe_state
from utils.json_fields import load_json_list
from utils.markers import parse_ad_markers
from utils.time import parse_iso_utc, utc_now, utc_now_iso
from youtube import settings as yt_settings, sponsorblock

logger = logging.getLogger(__name__)

BATCH = 50
MAX_BACKOFF = timedelta(hours=24)
TERMINAL = ('llm', 'exhausted')
IN_FLIGHT = ('pending', 'processing')
RETRY_SOON = sponsorblock.RETRY_SOON


def schedule_new_episode(db, slug, episode_id, resolved) -> bool:
    """First sighting of a video (no SponsorBlock state yet): wait out the processing delay before the first SponsorBlock check."""
    due = utc_now() + timedelta(minutes=resolved.process_delay_minutes)
    return db.schedule_sponsorblock_wait(slug, episode_id, due.isoformat())


def _ceiling_passed(row, resolved, now) -> bool:
    anchor = parse_iso_utc(row.get('published_at')) or parse_iso_utc(row.get('created_at')) or now
    return now >= anchor + timedelta(days=resolved.watch_max_days)


def _error_retry(state, resolved, now) -> datetime:
    """Retry a failed lookup at the state's normal interval, capped at 24 h."""
    if state in ('waiting', 'probing'):
        gap = timedelta(minutes=resolved.probe_interval_minutes)
    else:
        gap = timedelta(hours=resolved.watch_interval_hours)
    return now + min(gap, MAX_BACKOFF)


def transition(row, resolved, marks, now, has_retained_original, current_uuids=None) -> dict:
    """Pure SponsorBlock state machine: next state, schedule and queue/recut intent for one row.

    marks: None when the request failed, else whether any usable marker remains after filtering.
    current_uuids: uuid set of the converted markers (empty set is a genuine empty set); None when
    the response was unusable (duration mismatch), which never triggers a recut.
    """
    state = row.get('sb_state') or 'waiting'
    attempts = int(row.get('sb_attempts') or 0)
    watch_next = (now + timedelta(hours=resolved.watch_interval_hours)).isoformat()
    out = {'sb_state': state, 'sb_attempts': attempts, 'sb_next_check_at': None,
           'sb_last_error': None, 'queue': False, 'recut': False}
    if state in TERMINAL:
        return out
    if marks is None:
        out['sb_next_check_at'] = _error_retry(state, resolved, now).isoformat()
        out['sb_last_error'] = 'SponsorBlock request failed'
        return out
    if state in ('waiting', 'probing'):
        if marks:
            out.update(sb_state='probing', queue=True)
            return out
        attempts += 1
        out['sb_attempts'] = attempts
        if attempts < resolved.probe_attempts:
            out.update(sb_state='probing',
                       sb_next_check_at=(now + timedelta(minutes=resolved.probe_interval_minutes)).isoformat())
        elif resolved.llm_fallback:
            out.update(sb_state='llm', queue=True)
        else:
            out.update(sb_state='watching', sb_next_check_at=watch_next)
        return out
    if state == 'watching':
        if marks:
            out['queue'] = True
        elif _ceiling_passed(row, resolved, now):
            out.update(sb_state='exhausted', queue=True)
        else:
            out['sb_next_check_at'] = watch_next
        return out
    if state == 'done':
        if _ceiling_passed(row, resolved, now):
            return out
        known = set(load_json_list(row.get('sb_segment_uuids')))
        if current_uuids is not None and current_uuids != known and has_retained_original:
            out.update(queue=True, recut=True)
        out['sb_next_check_at'] = watch_next
        return out
    return out


def queue_for_processing(db, row, podcast, *, recut: bool):
    """Queue an episode: recut from the retained original, else the normal auto-process queue."""
    # Lazy: main_app instantiates Storage on import.
    from main_app import status_service
    from main_app.processing import start_background_processing
    slug, episode_id = row['slug'], row['episode_id']
    title = row.get('title') or 'Unknown'
    podcast_name = podcast.get('title') or slug
    if recut:
        db.upsert_episode(slug, episode_id, status='pending', reprocess_mode='recut',
                          reprocess_requested_at=utc_now_iso(), retry_count=0, error_message=None)
        started, _ = start_background_processing(slug, episode_id, row['original_url'], title, podcast_name,
                                                 row.get('description'), None, row.get('published_at'))
        if started:
            return
        priority = compute_queue_priority(podcast.get('queue_priority'), row.get('published_at'), manual=True)
    else:
        priority = compute_queue_priority(podcast.get('queue_priority'), row.get('published_at'), manual=False)
    db.upsert_episode_for_processing(slug, episode_id, row['original_url'], row.get('title'),
                                     row.get('published_at'), row.get('description'), priority=priority)
    status_service.queue_episode(slug, episode_id, title, podcast_name)


def _safety_check_at(state, resolved, now) -> str:
    """A non-NULL next check written before queueing, so a crash between steps cannot orphan the row."""
    if state in ('waiting', 'probing'):
        return (now + timedelta(minutes=resolved.probe_interval_minutes)).isoformat()
    return (now + timedelta(hours=resolved.watch_interval_hours)).isoformat()


def _stored_non_sponsorblock_markers(db, slug, episode_id) -> list[dict]:
    row = db.get_episode(slug, episode_id) or {}
    return [m for m in (parse_ad_markers(row.get('ad_markers_json')) or []) if m.get('source') != 'sponsorblock']


def _process_row(db, row, podcast, now, storage, base) -> bool:
    """Run one due row through the state machine; True when the SponsorBlock request failed."""
    slug, episode_id = row['slug'], row['episode_id']
    if row.get('status') in IN_FLIGHT:
        db.update_episode_sponsorblock_by_pk(row['id'], sb_next_check_at=(now + RETRY_SOON).isoformat())
        return False
    resolved = yt_settings.resolve_feed(db, podcast, base=base)
    marks = sponsorblock.fetch_segments(row['youtube_video_id']) if row.get('youtube_video_id') else []
    if marks is None:
        markers, current_uuids, marks_present = [], None, None
    else:
        markers, note = sponsorblock.to_markers(marks, row.get('original_duration'))
        marks_present = bool(markers)
        current_uuids = None if note == 'duration_mismatch' else set(sponsorblock.uuid_set(markers))
    original = storage.get_original_path(slug, episode_id)
    out = transition(row, resolved, marks_present, now, bool(original and original.exists()), current_uuids)
    fields = {k: out[k] for k in ('sb_state', 'sb_attempts', 'sb_next_check_at', 'sb_last_error')}
    if out['queue']:
        fields['sb_next_check_at'] = fields['sb_next_check_at'] or _safety_check_at(out['sb_state'], resolved, now)
    if out['recut']:
        merged = _stored_non_sponsorblock_markers(db, slug, episode_id) + markers
        merged.sort(key=lambda m: m['start'])
        db.save_episode_details(slug, episode_id, ad_markers=merged)
    db.update_episode_sponsorblock_by_pk(row['id'], **fields)
    if out['queue']:
        try:
            queue_for_processing(db, row, podcast, recut=out['recut'])
        except Exception:
            db.update_episode_sponsorblock_by_pk(row['id'], sb_state=row.get('sb_state'),
                                                 sb_attempts=int(row.get('sb_attempts') or 0))
            raise
        if not out['recut']:
            db.update_episode_sponsorblock_by_pk(row['id'], sb_next_check_at=None)
        logger.info(f"[{slug}:{episode_id}] SponsorBlock gate: {row.get('sb_state')} -> "
                    f"{out['sb_state']}{' (recut)' if out['recut'] else ''}, queued")
    return marks is None


def sponsorblock_tick(db):
    """Probe SponsorBlock for due YouTube episodes and queue the ones that are ready."""
    if not yt_settings.enabled(db):
        return
    now = utc_now()
    was_reachable = sponsorblock.availability(db)[0]
    due = db.get_due_sponsorblock_episodes(now.isoformat(), BATCH)
    # A good verdict stands until a fetch fails; probe only to find out or to detect recovery.
    if was_reachable is not True:
        reachable = sponsorblock.record_availability(db)
        if reachable and was_reachable is False:
            logger.info("SponsorBlock is reachable again; resuming held episodes")
        elif not reachable and was_reachable is not False:
            logger.warning("SponsorBlock is unreachable; holding YouTube episodes that wait on it")
        if not reachable:
            return
    from main_app import storage  # lazy: see queue_for_processing
    base = yt_settings.load_global(db)
    podcasts = {}
    request_failed = False
    deferred = 0
    for row in due:
        slug = row['slug']
        try:
            if request_failed:
                db.update_episode_sponsorblock_by_pk(
                    row['id'], sb_next_check_at=(now + RETRY_SOON).isoformat(),
                    sb_last_error='SponsorBlock request failed')
                deferred += 1
                continue
            if slug not in podcasts:
                podcasts[slug] = db.get_podcast_by_slug(slug)
            if podcasts[slug]:
                request_failed = _process_row(db, row, podcasts[slug], now, storage, base)
        except Exception as exc:
            logger.exception(f"[{slug}:{row.get('episode_id')}] SponsorBlock tick failed")
            try:
                db.update_episode_sponsorblock_by_pk(
                    row['id'], sb_next_check_at=(now + RETRY_SOON).isoformat(),
                    sb_last_error=type(exc).__name__)
            except Exception:
                logger.exception(f"[{slug}:{row.get('episode_id')}] SponsorBlock retry reschedule failed")
    if request_failed:
        if was_reachable is True:
            record_probe_state(db, DEFER_SERVICE_SPONSORBLOCK, False)
        logger.warning(f"SponsorBlock request failed; deferred {deferred} remaining episode(s) by "
                       f"{int(RETRY_SOON.total_seconds() // 60)} min")

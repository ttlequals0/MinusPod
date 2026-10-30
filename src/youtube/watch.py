"""SponsorBlock gate: schedule YouTube episodes before they enter the queue."""
import json
import logging
from dataclasses import replace as _replace
from datetime import datetime, timedelta, timezone

from database.queue import compute_queue_priority
from utils.time import parse_iso_utc, utc_now_iso
from youtube import settings as yt_settings, sponsorblock

logger = logging.getLogger(__name__)

BATCH = 50
MAX_BACKOFF = timedelta(hours=24)
TERMINAL = ('llm', 'exhausted')


def schedule_new_episode(db, slug, episode_id, podcast) -> bool:
    """First sighting of a video (no SponsorBlock state yet): wait out the processing delay before the first SponsorBlock check."""
    resolved = yt_settings.resolve_feed(db, podcast)
    due = datetime.now(timezone.utc) + timedelta(minutes=resolved.process_delay_minutes)
    return db.schedule_sponsorblock_wait(slug, episode_id, due.isoformat())


def replace_settings(settings, **changes):
    """Return a copy of the settings with the given fields changed."""
    return _replace(settings, **changes)


def _ceiling_passed(row, resolved, now) -> bool:
    anchor = parse_iso_utc(row.get('published_at')) or parse_iso_utc(row.get('created_at')) or now
    return now >= anchor + timedelta(days=resolved.watch_max_days)


def _backoff(row, now) -> datetime:
    """Double the previous gap, floor 5 min, cap 24 h."""
    last = parse_iso_utc(row.get('sb_next_check_at'))
    gap = (now - last) if last else timedelta(minutes=30)
    gap = max(gap, timedelta(minutes=5))
    return now + min(gap * 2, MAX_BACKOFF)


def transition(row, resolved, marks, now, has_retained_original) -> dict:
    """Pure SponsorBlock state machine: next state, schedule and queue/recut intent for one row."""
    state = row.get('sb_state') or 'waiting'
    attempts = int(row.get('sb_attempts') or 0)
    watch_next = (now + timedelta(hours=resolved.watch_interval_hours)).isoformat()
    out = {'sb_state': state, 'sb_attempts': attempts, 'sb_next_check_at': None,
           'sb_last_error': None, 'queue': False, 'recut': False}
    if state in TERMINAL:
        return out
    if marks is None:
        out['sb_next_check_at'] = _backoff(row, now).isoformat()
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
        known = set(json.loads(row.get('sb_segment_uuids') or '[]'))
        current = {m.get('UUID') for m in marks if m.get('UUID')}
        if marks and current != known and has_retained_original:
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


def _process_row(db, row, podcast, now, storage):
    slug, episode_id = row['slug'], row['episode_id']
    resolved = yt_settings.resolve_feed(db, podcast)
    marks = sponsorblock.fetch_segments(row['youtube_video_id']) if row.get('youtube_video_id') else []
    original = storage.get_original_path(slug, episode_id)
    out = transition(row, resolved, marks, now, has_retained_original=bool(original and original.exists()))
    fields = {k: out[k] for k in ('sb_state', 'sb_attempts', 'sb_next_check_at', 'sb_last_error')}
    if out['recut']:
        markers, _note = sponsorblock.to_markers(marks, row.get('original_duration'))
        if markers:
            db.save_episode_details(slug, episode_id, ad_markers=markers)
            fields['sb_segment_uuids'] = json.dumps(sponsorblock.uuid_set(markers))
        else:
            out['queue'] = out['recut'] = False
    db.update_episode_sponsorblock(slug, episode_id, **fields)
    if out['queue']:
        queue_for_processing(db, row, podcast, recut=out['recut'])
        logger.info(f"[{slug}:{episode_id}] SponsorBlock gate: {row.get('sb_state')} -> "
                    f"{out['sb_state']}{' (recut)' if out['recut'] else ''}, queued")


def sponsorblock_tick(db):
    """Probe SponsorBlock for due YouTube episodes and queue the ones that are ready."""
    if not db.get_setting_bool('youtube_enabled', False):
        return
    from main_app import storage  # lazy: see queue_for_processing
    now = datetime.now(timezone.utc)
    podcasts = {}
    for row in db.get_due_sponsorblock_episodes(now.isoformat(), BATCH):
        slug = row['slug']
        try:
            if slug not in podcasts:
                podcasts[slug] = db.get_podcast_by_slug(slug)
            if podcasts[slug]:
                _process_row(db, row, podcasts[slug], now, storage)
        except Exception:
            logger.exception(f"[{slug}:{row.get('episode_id')}] SponsorBlock tick failed")

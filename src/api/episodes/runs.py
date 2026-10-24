"""Reprocess, cancel, passthrough, recut and processing queue routes."""

import json
import logging
import threading
from flask import request
from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, get_status_service,
)
from config import (
    episode_matches_feed_filters, is_keep_like, is_pending_review, normalize_segment_category, resolve_processing_mode, DEFAULT_SEGMENT_ACTION,
    PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION, PROCESSING_MODE_CUE_ONLY,
)
from database.podcasts import is_local_feed
from ad_validator import user_trimmed_keep_ranges
from audio_processor import AudioProcessor
from cancel import request_cancellation, wait_for_cancellation
from database.queue import (
    compute_queue_priority, PENDING_QUEUE_LIMIT,
    QUEUE_PRIORITY_MAX, QUEUE_PRIORITY_MIN,
)
from llm_client import start_episode_token_tracking, get_episode_token_totals
import run_context
from reprocess_modes import (
    REPROCESS_MODE_NEEDS_TRANSCRIPT, batch_clear_episodes_for_mode, clear_episode_for_mode, reset_episode_for_reprocess,
)
from utils.constants import EpisodeStatus
from utils.text import parse_transcript_segments
from utils.time import epoch_to_iso, utc_now_iso
from api.episodes import common

logger = logging.getLogger('podcast.api')


def _check_recut_preconditions(db, slug, episode_id, episode):
    """Check the retained original, saved segments (only if a transcript exists) and detections.

    Returns an actionable error_response, or None when the recut can run."""
    if not get_storage().get_original_path(slug, episode_id).exists():
        return error_response(
            'Original audio was not retained for this episode, so it cannot '
            'be re-cut. Use "reprocess" or "full" to rebuild it from source.', 409)
    if episode.get('has_transcript') and not db.get_original_segments(slug, episode_id):
        return error_response(
            'No saved transcript segments for this episode; recut needs them '
            'to re-time the transcript. Use "reprocess" or "full" first.', 409)
    if not episode.get('ad_markers_json'):
        return error_response(
            'No ad detections to cut. Detect ads first with "reprocess" or "full".', 409)
    return None


def _overlaps_any(marker, spans) -> bool:
    return any(s['start'] < marker['end'] and s['end'] > marker['start']
               for s in spans or ())


def _marker_wants_cut(marker, action, false_positives, confirmed) -> bool:
    """Whether the recorded decisions ask for this marker to leave the audio.

    Conservative: anything not plainly kept/marked, rejected, or awaiting
    review counts as wanted, so an unclear marker costs a recut rather than
    a silently dropped decision.
    """
    if is_keep_like(action):
        return False
    if _overlaps_any(marker, false_positives):
        return False
    # An approved hold stays held until a recut applies it, so the marker
    # alone does not say it is wanted.
    if marker.get('approved') or _overlaps_any(marker, confirmed):
        return True
    if is_pending_review(marker):
        return False
    return (marker.get('validation') or {}).get('decision') != 'REJECT'


def _same_cut(a, b, tol=0.05) -> bool:
    if abs(a['start'] - b['start']) > tol or abs(a['end'] - b['end']) > tol:
        return False
    # Legacy rows carry no replacement_duration; compare it only when stored.
    if 'replacement_duration' in a and 'replacement_duration' in b:
        return abs(a['replacement_duration'] - b['replacement_duration']) <= tol
    return True


def chapters_only_decisions(markers, applied_cuts, original_duration,
                            actions=None, false_positives=(), confirmed=(),
                            keep_override=None, replacement_sound_enabled=True):
    """True when cutting the current markers reproduces the applied cuts.

    The decisions then changed only which ad chapters the audio should carry,
    so an apply can rebuild those instead of re-rendering the file. `actions`
    is the feed's resolved category action map, so a recategorized marker is
    judged the way the recut would judge it; `keep_override(marker)` is True
    where a pattern or differential rule cuts a keep anyway. Unknown inputs
    (no persisted cut list, no known duration) answer False: a recut is the
    safe fallback.
    """
    if applied_cuts is None or not original_duration:
        return False
    if not replacement_sound_enabled and any('replacement_duration' not in cut for cut in applied_cuts):
        return False
    # A marker without bounds cannot be compared; dropping it shortens the
    # wanted list, which answers False rather than guessing.
    resolved = [
        (m, actions.get(normalize_segment_category(m.get('category')),
                        DEFAULT_SEGMENT_ACTION)
            if actions is not None else m.get('action_applied'))
        for m in markers
        if m.get('start') is not None and m.get('end') is not None
    ]
    resolved = [(m, DEFAULT_SEGMENT_ACTION
                 if is_keep_like(action) and keep_override and keep_override(m) else action)
                for m, action in resolved]
    protected = [*(m for m, action in resolved if is_keep_like(action)),
                 *user_trimmed_keep_ranges(list(confirmed))]
    wanted = AudioProcessor(replacement_sound_enabled=replacement_sound_enabled).compute_applied_cuts(
        [dict(m, beep=(action == 'beep')) for m, action in resolved
         if _marker_wants_cut(m, action, false_positives, confirmed)],
        original_duration, cut_barriers=protected, hard_barriers=protected,
    )
    return (len(wanted) == len(applied_cuts)
            and all(_same_cut(w, a)
                    for w, a in zip(wanted, applied_cuts, strict=True)))


def _apply_needs_chapters_only(db, slug, episode_id, episode, markers) -> bool:
    """chapters_only_decisions for one pending-recut episode."""
    # Module-level import would be a cycle: main_app imports this package.
    from main_app.pipeline.keep_rules import _keep_overridden, _load_stored_dai_differential, _make_keep_differential_override
    differential_override = _make_keep_differential_override(
        _load_stored_dai_differential(slug, episode_id))
    return chapters_only_decisions(
        markers, db.get_applied_cuts(slug, episode_id),
        episode.get('original_duration'),
        actions=db.resolve_segment_actions(slug),
        false_positives=db.get_false_positive_corrections(
            episode['podcast_id'], episode_id),
        confirmed=db.get_confirmed_corrections(
            episode['podcast_id'], episode_id),
        replacement_sound_enabled=db.resolve_audio_output(slug)['replacement_sound_enabled'],
        keep_override=lambda m: _keep_overridden(dict(m), differential_override))


# Reprocess-mode rules shared by the three reprocess endpoints
# (reprocess_all_episodes = 'batch', bulk_episode_action = 'bulk',
# reprocess_episode_with_mode = 'single'). Fields:
#   contexts:         endpoints that may request the mode. recut stays
#                     single-episode-only by design.
#   preconditions:    extra per-episode input checks, single-episode-only
#                     (returns an error_response or None).
# What each mode wipes before requeueing, and which modes need a saved
# transcript, live in reprocess_modes, which the pipeline imports as well.
REPROCESS_MODE_SPECS = {
    'reprocess': {
        'contexts': ('batch', 'bulk', 'single'),
        'preconditions': None,
    },
    'full': {
        'contexts': ('batch', 'bulk', 'single'),
        'preconditions': None,
    },
    'llm': {
        'contexts': ('batch', 'bulk', 'single'),
        'preconditions': None,
    },
    'recut': {
        'contexts': ('single',),
        'preconditions': _check_recut_preconditions,
    },
}

# Waiting-list pagination for GET /episodes/processing (#696).
_QUEUE_PAGE_DEFAULT_LIMIT = PENDING_QUEUE_LIMIT
_QUEUE_PAGE_MAX_LIMIT = 1000


def _mode_allowed(mode, context):
    spec = REPROCESS_MODE_SPECS.get(mode)
    return spec is not None and context in spec['contexts']


@api.route('/feeds/<slug>/episodes/<episode_id>/reprocess', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def reprocess_episode(slug, episode_id):
    """Legacy URL for the mode-aware reprocess handler."""
    return _reprocess_episode_with_mode(slug, episode_id, legacy=True)


@api.route('/feeds/<slug>/reprocess-all', methods=['POST'])
@limiter.limit("2 per minute")
@log_request
def reprocess_all_episodes(slug):
    """Queue all processed episodes for reprocessing.

    This is useful when ad detection logic has improved and you want to
    re-detect ads in all episodes of a podcast.

    Modes:
    - reprocess (default): Use pattern DB + Claude (leverages learned patterns)
    - full: Skip pattern DB entirely, Claude does fresh analysis without learned patterns
    - llm: Re-detect + re-cut using each saved transcript, skipping
      re-transcription (issue #349). Episodes without a transcript are skipped.
    """
    db = get_database()

    # Get mode from request body
    data = request.get_json() or {}
    mode = data.get('mode', 'reprocess')

    if not _mode_allowed(mode, 'batch'):
        return error_response('Invalid mode. Use "reprocess", "full", or "llm"', 400)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    # Get all episodes that have been processed
    episodes, _ = db.get_episodes(slug, status=EpisodeStatus.PROCESSED.value)

    if not episodes:
        return json_response({
            'message': 'No processed episodes to reprocess',
            'queued': 0,
            'skipped': 0,
            'mode': mode
        })

    queued = []
    skipped = []

    for episode in episodes:
        episode_id = episode['episode_id']

        # Skip if already processing
        if episode.get('status') == EpisodeStatus.PROCESSING:
            skipped.append({'episodeId': episode_id, 'reason': 'Already processing'})
            continue

        # LLM-only reprocess needs a saved transcript; skip episodes without one.
        if REPROCESS_MODE_NEEDS_TRANSCRIPT[mode] and not db.has_transcript(slug, episode_id):
            skipped.append({'episodeId': episode_id,
                            'reason': 'No transcript for LLM-only reprocess'})
            continue

        try:
            # Keep existing audio until the new version is durable (orchestration-5).
            clear_episode_for_mode(db, slug, episode_id, mode)

            # Reset status to pending with reprocess mode for priority queue
            db.upsert_episode(
                slug, episode_id,
                status=EpisodeStatus.PENDING.value,
                reprocess_mode=mode,
                reprocess_requested_at=utc_now_iso(),
                retry_count=0,
                error_message=None
            )

            queued.append({'episodeId': episode_id, 'title': episode.get('title', '')})
            logger.info(f"Queued for reprocessing: {slug}:{episode_id}")

        except Exception as e:
            logger.error(f"Failed to queue {slug}:{episode_id} for reprocessing: {e}")
            skipped.append({'episodeId': episode_id, 'reason': 'queue failed'})

    logger.info(f"Batch reprocess {slug} (mode={mode}): {len(queued)} queued, {len(skipped)} skipped")

    return json_response({
        'message': f'Queued {len(queued)} episodes for {mode} reprocessing',
        'queued': len(queued),
        'skipped': len(skipped),
        'mode': mode,
        'episodes': {
            'queued': queued,
            'skipped': skipped
        }
    })


# Terminal statuses a user can act on in bulk (reprocess/delete).
# 'deferred' episodes (#482) are included so a stuck offline queue
# can be force-retried or cleaned up by hand.
REPROCESSABLE_STATUSES = ('processed', 'failed', 'permanently_failed', 'deferred')

@api.route('/feeds/<slug>/episodes/bulk', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def bulk_episode_action(slug):
    """Bulk actions on episodes: process, reprocess, reprocess_full, reprocess_llm, delete.

    reprocess_llm re-detects + re-cuts using each saved transcript, skipping
    re-transcription (issue #349); episodes without a transcript are skipped.
    """
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    data = request.get_json()
    if not data:
        return error_response('Request body required', 400)

    episode_ids = data.get('episodeIds', [])
    action = data.get('action', '')

    if not episode_ids:
        return error_response('episodeIds is required and must be non-empty', 400)
    if len(episode_ids) > 500:
        return error_response('Maximum 500 episodes per bulk action', 400)
    if action not in ('process', 'reprocess', 'reprocess_full', 'reprocess_llm', 'delete'):
        return error_response('Invalid action. Use: process, reprocess, reprocess_full, reprocess_llm, delete', 400)

    queued = 0
    skipped = 0
    freed_mb = 0.0
    errors = []
    skipped_episodes = []
    eligible_ids = []
    enqueue_ids = []

    # Batch-fetch all episodes upfront to avoid N+1 queries
    all_episodes = db.get_episodes_by_ids(slug, episode_ids)
    episodes_by_id = {ep['episode_id']: ep for ep in all_episodes}

    if action == 'process':
        # Discovered rows follow the existing bulk path. Pending rows can only
        # be restored when no queue entry or active run already owns them.
        eligible_ids = []
        stranded_pending = []
        for episode_id in episode_ids:
            episode = episodes_by_id.get(episode_id)
            if not episode:
                skipped += 1
                continue
            if episode_matches_feed_filters(episode, podcast):
                skipped += 1
                skipped_episodes.append({
                    'episodeId': episode_id,
                    'reason': 'Episode matches feed filters',
                })
                continue
            if episode.get('status') == EpisodeStatus.DISCOVERED.value:
                eligible_ids.append(episode_id)
            elif episode.get('status') == EpisodeStatus.PENDING.value:
                stranded_pending.append({
                    'episode_id': episode_id,
                    'priority': compute_queue_priority(
                        podcast.get('queue_priority'), episode.get('published_at'), bulk=True),
                })
            else:
                skipped += 1
        if eligible_ids:
            # reprocess_requested_at marks the row as user-initiated so the
            # background drainer's auto-process-disabled gate bypasses it.
            queued = db.batch_set_episodes_pending(slug, eligible_ids,
                                                    reprocess_requested_at=utc_now_iso())
            skipped += len(eligible_ids) - queued
            if queued:
                enqueue_ids = eligible_ids
        pending_result = db.requeue_stranded_pending_episodes(
            slug, stranded_pending, utc_now_iso())
        queued += len(pending_result['queued'])
        skipped += len(pending_result['skipped'])
        skipped_episodes.extend(pending_result['skipped'])

    elif action in ('reprocess', 'reprocess_full', 'reprocess_llm'):
        # File cleanup must be per-episode, but DB updates are batched
        mode = {'reprocess_full': 'full', 'reprocess_llm': 'llm'}.get(action, 'reprocess')
        eligible_ids = []
        for episode_id in episode_ids:
            try:
                episode = episodes_by_id.get(episode_id)
                if not episode or episode.get('status') not in REPROCESSABLE_STATUSES:
                    skipped += 1
                    continue
                # LLM-only reprocess needs a saved transcript; skip episodes without one.
                if REPROCESS_MODE_NEEDS_TRANSCRIPT[mode] and not db.has_transcript(slug, episode_id):
                    skipped += 1
                    continue
                # Keep existing audio until the new version is durable (orchestration-5).
                eligible_ids.append(episode_id)
            except Exception as e:
                logger.error(f"Bulk action error for {slug}:{episode_id}: {e}")
                errors.append(f"{episode_id}: bulk action failed")
        if eligible_ids:
            batch_clear_episodes_for_mode(db, slug, eligible_ids, mode)
            now_str = utc_now_iso()
            queued = db.batch_set_episodes_pending(slug, eligible_ids,
                                                    reprocess_mode=mode,
                                                    reprocess_requested_at=now_str)
            if queued:
                enqueue_ids = eligible_ids

    elif action == 'delete':
        # Collect eligible IDs, let delete_episodes handle batching
        eligible_ids = []
        for episode_id in episode_ids:
            episode = episodes_by_id.get(episode_id)
            if not episode or episode.get('status') not in REPROCESSABLE_STATUSES:
                skipped += 1
                continue
            eligible_ids.append(episode_id)
        if eligible_ids:
            try:
                active = db.mark_episodes_for_deletion(slug, eligible_ids)
                try:
                    busy = set(active['processing']) | set(active['uploading'])
                    ready_ids = [episode_id for episode_id in eligible_ids
                                 if episode_id not in busy]
                    skipped += len(busy)
                    # Local feeds retain their only original and delete processed output only.
                    local_feed = is_local_feed(podcast)
                    reset, freed = db.delete_episodes(
                        slug, ready_ids, storage, keep_original=local_feed)
                    queued += reset
                    freed_mb += freed
                    if local_feed and reset:
                        from local_feed_builder import rebuild_local_feed
                        rebuild_local_feed(slug)
                    if reset:
                        from recents_feed import rebuild_recents_feed
                        rebuild_recents_feed()
                finally:
                    db.clear_episode_deletion_requests(slug, eligible_ids)
            except Exception as e:
                logger.error(f"Bulk delete error for {slug}: {e}")
                errors.append('bulk delete failed')

    # Enqueue episodes into auto_process_queue so the background processor
    # picks them up sequentially. Previously this called start_background_processing()
    # with no arguments (a TypeError silently swallowed by `except Exception: pass`),
    # which meant episodes were marked pending in the DB but never actually processed.
    if action in ('process', 'reprocess', 'reprocess_full', 'reprocess_llm') and enqueue_ids:
        for episode_id in enqueue_ids:
            try:
                ep = episodes_by_id.get(episode_id)
                if ep:
                    # Bulk work enqueues at base + fresh only. Stamping a
                    # whole backlog with the manual boost starved JIT plays
                    # and single reprocesses for days (93 two-year-old
                    # episodes at +20 once pinned a fresh episode 94th).
                    priority = compute_queue_priority(
                        podcast.get('queue_priority'), ep.get('published_at'), bulk=True)
                    db.upsert_episode_for_processing(
                        slug, episode_id,
                        ep.get('original_url', ''),
                        ep.get('title'),
                        ep.get('published_at'),
                        ep.get('description'),
                        priority=priority,
                    )
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Could not enqueue for processing: {e}")

    logger.info(f"Bulk {action} on {slug}: {queued} queued, {skipped} skipped, {freed_mb:.1f} MB freed")

    response = {
        'queued': queued,
        'skipped': skipped,
        'freedMb': round(freed_mb, 2),
        'errors': errors,
        'skippedEpisodes': skipped_episodes,
    }
    # Bulk enqueues never start processing synchronously (unlike the single
    # reprocess endpoint), so the authoritative state is 'queued' whenever
    # anything was queued, else 'idle'. Not applicable to 'delete'.
    if action in ('process', 'reprocess', 'reprocess_full', 'reprocess_llm'):
        response['jobState'] = common._job_state(EpisodeStatus.PENDING.value,
                                                 'queued' if queued else None)

    return json_response(response)


@api.route('/feeds/<slug>/episodes/passthrough', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def set_episodes_passthrough(slug):
    """Set or clear the per-episode pass-through override (#746) for one or
    more episodes. An episode flagged this way runs pass-through
    (download + relay, no transcription/detection/LLM) even when its feed
    is in a non-passthrough mode.

    Enabling enqueues a normal reprocess for each affected episode so the
    resolver's now-flagged episode takes the pass-through branch this run;
    disabling only clears the flag and so applies to future processing.
    An episode a run already owns is rejected when enabling, because that
    run has already resolved its mode, and accepted when disabling.
    """
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('Request body must be a JSON object', 400)

    episode_ids = data.get('episodeIds')
    enabled = data.get('enabled')

    if not isinstance(episode_ids, list) or not episode_ids:
        return error_response('episodeIds is required and must be a non-empty array', 400)
    if not all(isinstance(episode_id, str) and episode_id for episode_id in episode_ids):
        return error_response('episodeIds must contain only non-empty strings', 400)
    if len(episode_ids) > 500:
        return error_response('Maximum 500 episodes per bulk action', 400)
    if not isinstance(enabled, bool):
        return error_response('enabled is required and must be a boolean', 400)

    # Dedup in request order so a repeated id yields one result entry.
    requested = list(dict.fromkeys(episode_ids))
    episodes_by_id = {ep['episode_id']: ep for ep in db.get_episodes_by_ids(slug, requested)}
    job_states = db.get_episode_job_states(requested)

    accepted = []
    rejected = []
    for episode_id in requested:
        episode = episodes_by_id.get(episode_id)
        if not episode:
            rejected.append({'episodeId': episode_id, 'reason': 'not_found'})
            continue
        owned = common._job_state(episode['status'], job_states.get((slug, episode_id)))
        if enabled and owned == 'processing':
            rejected.append({'episodeId': episode_id, 'reason': 'processing'})
            continue
        accepted.append(episode_id)

    updated = 0
    queued_ids = []
    if accepted:
        # Flag and reprocess obligation land together; a pending episode whose
        # queue insert below fails is still picked up as stranded pending work.
        updated = db.set_episodes_passthrough(
            slug, accepted, enabled,
            reprocess_ids=accepted if enabled else None,
            reprocess_requested_at=utc_now_iso() if enabled else None)
    if enabled and accepted:
        for episode_id in accepted:
            try:
                ep = episodes_by_id[episode_id]
                priority = compute_queue_priority(
                    podcast.get('queue_priority'), ep.get('published_at'), bulk=True)
                db.upsert_episode_for_processing(
                    slug, episode_id,
                    ep.get('original_url', ''),
                    ep.get('title'),
                    ep.get('published_at'),
                    ep.get('description'),
                    priority=priority,
                )
            except Exception as e:
                logger.warning(
                    f"[{slug}:{episode_id}] Could not enqueue for pass-through: {e}")
        # Counted from the queue itself, so a swallowed insert failure above
        # cannot be reported as queued work.
        states_after = db.get_episode_job_states(accepted)
        queued_ids = [episode_id for episode_id in accepted
                      if states_after.get((slug, episode_id))]

    logger.info(f"Set passthrough={enabled} on {slug}: {updated} updated, "
                f"{len(queued_ids)} queued, {len(rejected)} rejected")
    response = {
        'updated': updated,
        'queued': len(queued_ids),
        'accepted': accepted,
        'rejected': rejected,
    }
    if enabled:
        response['jobState'] = common._job_state(EpisodeStatus.PENDING.value,
                                          'queued' if queued_ids else None)
    return json_response(response)


@api.route('/feeds/<slug>/episodes/<episode_id>/retry-ad-detection', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def retry_ad_detection(slug, episode_id):
    """Retry ad detection for an episode using existing transcript."""
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)
    # Per-episode override included (#746): an episode marked pass-through
    # makes no LLM call even when its feed is on the standard mode.
    episode = db.get_episode(slug, episode_id)
    mode = resolve_processing_mode(podcast, episode)
    if mode in (PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION,
                PROCESSING_MODE_CUE_ONLY):
        return error_response(
            f'Processing mode is {mode}; LLM ad detection is disabled '
            f'for this episode', 409)

    if not episode:
        return error_response('Episode not found', 404)

    # Get transcript
    transcript = storage.get_transcript(slug, episode_id)
    if not transcript:
        return error_response('No transcript available - full reprocess required', 400)

    ctx = run_context.begin(slug, episode_id)
    try:
        # Parse transcript back into segments
        segments = parse_transcript_segments(transcript)

        if not segments:
            return error_response('Could not parse transcript into segments', 400)

        podcast_name = podcast.get('title', slug)

        # Retry ad detection with token tracking
        start_episode_token_tracking()

        from ad_detector import AdDetector
        ad_detector = AdDetector()
        # Load podcast tags for community-pattern eligibility.
        try:
            _tags_json = podcast.get('tags') if podcast else None
            podcast_tags = set(json.loads(_tags_json)) if _tags_json else None
        except Exception:
            podcast_tags = None
        # No positional_prior_hint here (issue #360): the stored transcript
        # is post-cut for processed episodes, so original-timeline hint
        # times would misdirect the model, the same reason pass 2 skips it.
        ad_result = ad_detector.process_transcript(
            segments, podcast_name, episode.get('title', 'Unknown'), slug, episode_id,
            podcast_id=slug,  # Pass slug as podcast_id for pattern matching
            podcast_tags=podcast_tags,
        )

        ad_detection_status = ad_result.get('status', 'failed')

        if ad_detection_status == 'success':
            storage.save_ads_json(slug, episode_id, ad_result)
            db.upsert_episode(slug, episode_id, ad_detection_status='success')

            ads = ad_result.get('ads', [])
            return json_response({
                'message': 'Ad detection retry successful',
                'episodeId': episode_id,
                'adsFound': len(ads),
                'status': 'success',
                'note': 'Full reprocess required to apply new ad markers to audio'
            })
        else:
            db.upsert_episode(slug, episode_id, ad_detection_status='failed')
            return json_response({
                'message': 'Ad detection retry failed',
                'episodeId': episode_id,
                'error': ad_result.get('error'),
                'retryable': ad_result.get('retryable', False),
                'status': 'failed'
            }, 500)

    except Exception:
        logger.exception(f"Failed to retry ad detection for {slug}:{episode_id}")
        return error_response('Failed to retry ad detection', 500)
    finally:
        token_totals = get_episode_token_totals()
        run_context.end(ctx)
        if token_totals['input_tokens'] > 0:
            db.increment_episode_token_usage(
                podcast['id'], episode_id,
                token_totals['input_tokens'],
                token_totals['output_tokens'],
                token_totals['cost'],
            )


# ========== Processing Queue Endpoints ==========

def _annotate_queue_admission(db, queued: list[dict], pending_rows: list[dict]) -> None:
    """Stamp each waiting entry with why it is not starting.

    Resolves the route snapshot and feature gates once for the page, and
    memoizes each hold marker, so a long backlog costs one pass rather than
    one settings read per row. Never raises: an unexplained entry reports
    unblocked rather than failing the panel.
    """
    from main_app.pipeline.admission import _admission_gates, _resolve_route_snapshot, admission_explanation
    from processing_queue import is_processing_paused
    idle = {'blocked': False, 'phase': None, 'slot': None,
            'reason': None, 'resumesAt': None}
    if not queued:
        return
    try:
        snapshot = _resolve_route_snapshot()
        gates = _admission_gates(db)
        paused = is_processing_paused(db)
    except Exception as exc:
        logger.warning("Could not resolve queue admission state: %s", exc)
        for entry in queued:
            entry['admission'] = dict(idle)
        return
    rows = {(row['podcast_slug'], row['episode_id']): row for row in pending_rows}
    hold_cache: dict = {}
    for entry in queued:
        try:
            entry['admission'] = admission_explanation(
                db, entry['slug'], entry['episodeId'], snapshot=snapshot,
                gates=gates, queue_row=rows.get((entry['slug'], entry['episodeId'])),
                paused=paused, hold_cache=hold_cache)
        except Exception as exc:
            logger.warning("Could not explain admission for %s:%s: %s",
                           entry['slug'], entry['episodeId'], exc)
            entry['admission'] = dict(idle)


@api.route('/episodes/processing', methods=['GET'])
@log_request
def get_processing_episodes():
    """Episodes processing now, then the pending queue in dequeue order.

    Sources: the DB's 'processing' rows plus StatusService.jobs for the
    active jobs; auto_process_queue pending rows plus StatusService's display
    queue for the backlog. Issue #236. The waiting list is paginated with the
    `offset`/`limit` query params (limit default 200, cap 1000); rows carry an
    offset-aware `queuePosition` so the panel can page through a long backlog
    (#696). Each waiting row also carries an `admission` object saying whether
    it is blocked and, if so, by which phase and account.
    """
    db = get_database()
    conn = db.get_connection()

    offset = max(0, request.args.get('offset', 0, type=int))
    limit = min(max(request.args.get('limit', _QUEUE_PAGE_DEFAULT_LIMIT, type=int), 1),
                _QUEUE_PAGE_MAX_LIMIT)

    cursor = conn.execute("""
        SELECT e.episode_id, e.title, p.slug, p.title as podcast
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.status = 'processing'
        ORDER BY e.updated_at DESC
    """)
    episodes = [{
        'episodeId': ep['episode_id'],
        'slug': ep['slug'],
        'title': ep['title'] or 'Unknown',
        'podcast': ep['podcast'] or ep['slug'],
        'startedAt': None,
        'stage': None,
    } for ep in cursor.fetchall()]

    status = get_status_service().get_status()
    for job in status.jobs:
        match = next((e for e in episodes
                      if e['slug'] == job.slug and e['episodeId'] == job.episode_id), None)
        if match:
            match['title'] = job.title or match['title']
            match['podcast'] = job.podcast_name or match['podcast']
            match['startedAt'] = job.started_at
            match['stage'] = job.stage
        else:
            episodes.append({
                'episodeId': job.episode_id,
                'slug': job.slug,
                'title': job.title or 'Unknown',
                'podcast': job.podcast_name or job.slug,
                'startedAt': job.started_at,
                'stage': job.stage,
            })

    # Append the waiting queue after the active job(s). The auto_process_queue
    # rows are the real backlog, so they come first and in dequeue order
    # (priority DESC, created_at ASC, the ORDER BY the worker claims by),
    # sliced to the requested page. StatusService's display queue only holds
    # hand-enqueued entries, so it trails the DB backlog with the rest.
    seen = {(e['slug'], e['episodeId']) for e in episodes}
    queued = []
    pending_rows = db.get_pending_queued_episodes(limit=limit, offset=offset)
    # An empty page means either an empty backlog (offset 0) or a page past
    # its end, and only the latter needs the count query.
    pending_total = (pending_rows[0]['total_pending'] if pending_rows
                     else (db.count_pending_queued_episodes() if offset else 0))
    for row in pending_rows:
        key = (row['podcast_slug'], row['episode_id'])
        if key in seen:
            continue
        seen.add(key)
        queued.append({
            'episodeId': row['episode_id'],
            'slug': row['podcast_slug'],
            'title': row['title'] or 'Unknown',
            'podcast': row['podcast_title'] or row['podcast_slug'],
            'startedAt': None,
            'queuedAt': row['created_at'],
            'priority': row['priority'],
            'stage': 'queued',
        })

    # Extras trail the DB backlog at virtual positions pending_total.., so
    # they dedup against the whole backlog, not just this page: `seen` covers
    # the active job and display-queue repeats, the ownership lookup the rest.
    candidates = []
    for q in status.queued_episodes:
        key = (q['slug'], q['episode_id'])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(q)
    owned_keys = (set(db.get_episode_job_states([q['episode_id'] for q in candidates]))
                  if candidates else set())
    valid_extras = [q for q in candidates
                    if (q['slug'], q['episode_id']) not in owned_keys]
    start = max(0, offset - pending_total)
    page_extras = valid_extras[start:start + limit - len(pending_rows)]
    for q in page_extras:
        queued.append({
            'episodeId': q['episode_id'],
            'slug': q['slug'],
            'title': q.get('title') or 'Unknown',
            'podcast': q.get('podcast_name') or q['slug'],
            'startedAt': None,
            'queuedAt': epoch_to_iso(q.get('queued_at')),
            'priority': None,
            'stage': 'queued',
        })

    _annotate_queue_admission(db, queued, pending_rows)

    # queueTotal is the whole backlog, not the page. Stamped on every entry,
    # active ones included, so a page whose rows all deduped away still
    # reports it instead of collapsing the pager to one page.
    queue_total = pending_total + len(valid_extras)
    for entry in episodes:
        entry['queueTotal'] = queue_total
    for position, entry in enumerate(queued, start=offset + 1):
        entry['queuePosition'] = position
        entry['queueTotal'] = queue_total

    return json_response(episodes + queued)


@api.route('/feeds/<slug>/episodes/<episode_id>/cancel', methods=['POST'])
@log_request
def cancel_episode_processing(slug, episode_id):
    """Cancel an episode that is processing OR queued."""
    db = get_database()
    status_service = get_status_service()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] != EpisodeStatus.PROCESSING:
        # Queued (waiting on the lock): close the DB queue row first so the
        # background worker stops seeing it as pending, then drop from the
        # display queue. Reversing this order would leave a window between
        # the display-queue write and the DB write in which the worker could
        # pick up the row and start processing it after the user clicked
        # Cancel.
        closed_db_rows = db.close_queue_rows_for_episode(slug, episode_id)
        removed_from_display = status_service.remove_queued_episode(slug, episode_id)
        if not removed_from_display and not closed_db_rows:
            return error_response(
                f"Episode is not processing or queued (status: {episode['status']})",
                400
            )
        logger.info(f"Removed queued episode from queue: {slug}:{episode_id}")
        return json_response({
            'message': 'Episode removed from queue',
            'episodeId': episode_id,
            'slug': slug
        })

    run_id = request_cancellation(slug, episode_id)
    if not run_id:
        return error_response('Could not record cancellation; run remains active', 503)

    # Belt-and-suspenders: clear any stale display-queue / auto_process_queue
    # entry for this episode so a follow-up enqueue starts from a clean slate.
    status_service.remove_queued_episode(slug, episode_id)
    db.close_queue_rows_for_episode(slug, episode_id)

    if not wait_for_cancellation(run_id, timeout=2.0):
        logger.info("Cancellation pending: %s:%s (%s)", slug, episode_id, run_id)
        return json_response({
            'message': 'Cancellation requested; processing is stopping',
            'episodeId': episode_id,
            'slug': slug,
        }, 202)

    logger.info(f"Canceled processing: {slug}:{episode_id}")
    return json_response({
        'message': 'Episode canceled and reset to pending',
        'episodeId': episode_id,
        'slug': slug
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/queue-priority', methods=['POST'])
@log_request
def set_episode_queue_priority(slug, episode_id):
    """Change a queued episode's priority (#696).

    Body: {"priority": int} to set an absolute value, or {"delta": int} to
    nudge the stored one (applied in SQL, so a stepper cannot lose a click
    to a stale read). Either way the write can raise or lower, unlike the
    monotonic MAX() rule on re-enqueue, and a later feed-level queuePriority
    change restamps the row.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('Body must be a JSON object', 400)
    priority, delta = data.get('priority'), data.get('delta')
    if (priority is None) == (delta is None):
        return error_response('Provide exactly one of priority or delta', 400)
    given = priority if delta is None else delta
    if not isinstance(given, int) or isinstance(given, bool):
        return error_response('priority and delta must be integers', 400)
    if not QUEUE_PRIORITY_MIN <= given <= QUEUE_PRIORITY_MAX:
        return error_response(
            f'Value must be between {QUEUE_PRIORITY_MIN} and {QUEUE_PRIORITY_MAX}', 400)

    db = get_database()
    if not db.get_podcast_by_slug(slug):
        return error_response('Feed not found', 404)
    stored = db.set_queue_row_priority(slug, episode_id, priority=priority, delta=delta)
    if stored is None:
        return error_response('No pending queue row for this episode', 404)
    logger.info(f"Queue priority set to {stored} for {slug}:{episode_id}")
    return json_response({
        'message': 'Queue priority updated',
        'episodeId': episode_id,
        'slug': slug,
        'priority': stored,
    })


def _recut_handled_by_own_run(row) -> bool:
    """Whether a pending-recut row's episode has its own run coming.

    Such a run reads the recorded decisions itself, and upserting a recut
    over it would downgrade a queued full/llm rerun. An episode left
    'pending' with no queue row (a cleared queue) has no run coming, so a
    recut is the only path for its decisions. Shared by the apply loop and
    the readiness flags so the button never counts rows apply would skip.
    """
    return bool(
        row['status'] == EpisodeStatus.PROCESSING.value
        or (row['status'] == EpisodeStatus.PENDING.value and row['has_queue_row']))


def _start_chapter_rebuilds(db, rows):
    """Rebuild the chapters of the chapters-only episodes on one daemon thread.

    Each rebuild is a full-file remux, so the apply request must not wait on
    them; one thread walks them in turn rather than starting an ffmpeg per
    episode. A stamp is cleared only once its episode is rebuilt, so a failure
    is retried by the next apply.
    """
    if not rows:
        return
    from main_app.pipeline.publish import rebuild_ad_chapters

    def _run():
        for slug, episode_id, stamp, episode, markers in rows:
            try:
                rebuild_ad_chapters(slug, episode_id, markers, episode=episode)
                db.clear_episode_pending_recut(slug, episode_id, before=stamp)
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Chapter rebuild failed "
                               f"during apply; leaving it stamped: {e}")

    logger.info(f"Apply pending recuts: rebuilding chapters on {len(rows)} "
                f"episode(s) with no audio change")
    threading.Thread(target=_run, name='ad-chapters-apply', daemon=True).start()


@api.route('/episodes/pending-recuts', methods=['GET'])
@log_request
def get_pending_recuts():
    """Episodes carrying review decisions that are not in the audio yet.

    Review is bulk work, so decisions are recorded as they are made and cut
    in one pass per episode when the operator applies them.
    """
    db = get_database()
    slug = request.args.get('slug') or None
    episodes = db.get_episodes_pending_recut(slug=slug)
    storage = get_storage()

    def chapters_only(e):
        """Whether an apply would rebuild this row's chapters instead of
        recutting. Only asked of rows a recut cannot take, so the common
        row costs no extra reads on this polled endpoint."""
        slug, episode_id = e['podcast_slug'], e['episode_id']
        episode = db.get_episode(slug, episode_id) or {}
        return _apply_needs_chapters_only(db, slug, episode_id, episode,
                                          common._markers_from_row(episode) or [])

    def entry(e):
        running = _recut_handled_by_own_run(e)
        # Mirrors _check_recut_preconditions, so the UI can say which rows
        # an apply will rebuild now, which are mid-run, and which wait for
        # a full reprocess. A chapters-only row needs none of those inputs.
        data_ok = bool(
            (e['has_segments'] or not e['has_transcript']) and e['has_markers']
            and storage.get_original_path(
                e['podcast_slug'], e['episode_id']).exists())
        if not data_ok and not running:
            data_ok = chapters_only(e)
        return {
            'slug': e['podcast_slug'],
            'episodeId': e['episode_id'],
            'title': e['title'],
            'podcast': e['podcast_title'],
            'pendingSince': e['pending_recut_at'],
            'recutReady': data_ok and not running,
            'inFlight': running,
        }

    return json_response({
        'count': len(episodes),
        'episodes': [entry(e) for e in episodes],
    })


@api.route('/episodes/pending-recuts/apply', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def apply_pending_recuts():
    """Recut every episode holding unapplied review decisions, once each.

    Same recut path and preconditions as the per-feed segment re-render, so
    there is still one recut queue. An episode failing them is left stamped
    rather than silently cleared, so its decisions are not lost.
    """
    db = get_database()
    from main_app.processing import start_background_processing

    payload = request.get_json(silent=True)
    scope = (payload.get('slug') or None) if isinstance(payload, dict) else None
    queued, skipped = 0, 0
    chapters_only = []
    for row in db.get_episodes_pending_recut(slug=scope):
        slug, episode_id = row['podcast_slug'], row['episode_id']
        episode = db.get_episode(slug, episode_id)
        podcast = db.get_podcast_by_slug(slug)
        if not episode or not podcast:
            skipped += 1
            continue
        if _recut_handled_by_own_run(row):
            skipped += 1
            continue
        # Chapters-only decisions are settled here, before the recut
        # preconditions: they need neither the retained original nor the
        # saved transcript, so they must not be reported as blocked.
        markers = common._markers_from_row(episode) or []
        if _apply_needs_chapters_only(db, slug, episode_id, episode, markers):
            chapters_only.append((slug, episode_id, row['pending_recut_at'],
                                  episode, markers))
            continue
        if _check_recut_preconditions(db, slug, episode_id, episode) is not None:
            skipped += 1
            continue
        try:
            db.upsert_episode(
                slug, episode_id,
                status=EpisodeStatus.PENDING.value,
                reprocess_mode='recut',
                reprocess_requested_at=utc_now_iso(),
                retry_count=0,
                error_message=None,
            )
            started, _reason = start_background_processing(
                slug, episode_id, episode.get('original_url'),
                episode.get('title', 'Unknown'), podcast.get('title', slug),
                episode.get('description'), None, episode.get('published_at'),
            )
            if not started:
                priority = compute_queue_priority(
                    podcast.get('queue_priority'), episode.get('published_at'),
                    manual=True)
                db.upsert_episode_for_processing(
                    slug, episode_id, episode.get('original_url'),
                    episode.get('title', 'Unknown'),
                    episode.get('published_at'), episode.get('description'),
                    priority=priority,
                )
                get_status_service().queue_episode(
                    slug, episode_id, episode.get('title', 'Unknown'),
                    podcast.get('title', slug))
            queued += 1
        except Exception:
            logger.exception(
                f"[{slug}:{episode_id}] Failed to queue pending-recut apply")
            skipped += 1

    logger.info(f"Apply pending recuts: {queued} queued, "
                f"{len(chapters_only)} chapters-only, {skipped} skipped")
    _start_chapter_rebuilds(db, chapters_only)
    return json_response({'queued': queued, 'skipped': skipped,
                          'chaptersRebuilding': len(chapters_only)})


# ========== Episode Reprocessing Endpoint ==========

@api.route('/episodes/<slug>/<episode_id>/reprocess', methods=['POST'])
@log_request
def reprocess_episode_with_mode(slug, episode_id):
    """Reprocess an episode with specified mode.

    Modes:
    - reprocess (default): Use pattern DB + Claude (leverages learned patterns)
    - full: Skip pattern DB entirely, Claude does fresh analysis without learned patterns
    - llm: Re-run ad detection and re-cut using the SAVED transcript, skipping
      re-transcription (issue #349). Requires an existing transcript.
    - recut: Re-cut the retained original audio from the CURRENT ad detections
      (applying the user's edits) and re-time the saved transcript. No
      transcription or LLM (issue #422). Requires the retained original audio,
      saved segments, and existing ad markers.
    """
    return _reprocess_episode_with_mode(slug, episode_id)


def _reprocess_episode_with_mode(slug, episode_id, legacy=False):
    db = get_database()

    # Legacy clients post no body or a non-JSON one; both mean the default mode.
    data = request.get_json(silent=legacy)
    if legacy and data is None:
        data = {}
    if not isinstance(data, dict):
        return error_response('Request body must be a JSON object', 400)
    mode = data.get('mode', 'reprocess')

    if not _mode_allowed(mode, 'single'):
        return error_response('Invalid mode. Use "reprocess", "full", "llm", or "recut"', 400)
    mode_spec = REPROCESS_MODE_SPECS[mode]

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == EpisodeStatus.PROCESSING:
        # Duplicate submission: report the current job state rather than a
        # bare error, so the client reconciles from jobState alone.
        return json_response({
            'error': 'Episode is currently processing',
            'status': 409,
            'jobState': common._episode_job_state(db, slug, episode_id, episode['status']),
        }, 409)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Podcast not found', 404)

    # LLM-only reprocess reuses the saved transcript to skip re-transcription;
    # it cannot run without one. Mirror retry-ad-detection's guard.
    if REPROCESS_MODE_NEEDS_TRANSCRIPT[mode] and not db.has_transcript(slug, episode_id):
        return error_response(
            'No transcript available for LLM-only reprocess. '
            'Use "reprocess" or "full" to re-transcribe first.', 400)

    if mode_spec['preconditions']:
        err = mode_spec['preconditions'](db, slug, episode_id, episode)
        if err is not None:
            return err

    try:
        # Mode, user-request mark, and the per-mode clear. The processed audio
        # stays until the new version is durable (orchestration-5).
        reset_episode_for_reprocess(db, slug, episode_id, mode)

        # Get episode metadata for processing
        episode_url = episode.get('original_url')
        episode_title = episode.get('title', 'Unknown')
        podcast_name = podcast.get('title', slug)
        episode_description = episode.get('description')
        episode_published_at = episode.get('published_at')

        # 5. Start background processing (non-blocking)
        from main_app.processing import start_background_processing
        logger.info(f"[{slug}:{episode_id}] Starting {mode} reprocess (async)")

        started, reason = start_background_processing(
            slug, episode_id, episode_url, episode_title,
            podcast_name, episode_description, None, episode_published_at
        )

        if started:
            return json_response({
                'message': ('Episode reprocess started' if legacy and mode == 'reprocess'
                            else f'Episode {mode} reprocess started'),
                'episodeId': episode_id,
                'mode': mode,
                'status': 'processing',
                'jobState': common._episode_job_state(db, slug, episode_id,
                                               EpisodeStatus.PROCESSING.value),
            }, 202)  # 202 Accepted
        else:
            priority = compute_queue_priority(
                podcast.get('queue_priority'), episode_published_at, manual=True)
            db.upsert_episode_for_processing(
                slug, episode_id, episode_url, episode_title,
                episode_published_at, episode_description, priority=priority
            )
            get_status_service().queue_episode(slug, episode_id, episode_title, podcast_name)
            logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), added to processing queue")
            return json_response({
                'message': ('Episode queued for reprocess' if legacy and mode == 'reprocess'
                            else f'Episode queued for {mode} reprocess'),
                'episodeId': episode_id,
                'mode': mode,
                'status': 'queued',
                'reason': reason,
                'jobState': common._episode_job_state(db, slug, episode_id,
                                               EpisodeStatus.PENDING.value),
            }, 202)

    except Exception:
        logger.exception(f"[{slug}:{episode_id}] {mode} reprocess failed")
        return error_response('Reprocess failed', 500)

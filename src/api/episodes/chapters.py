"""Chapter regeneration routes and helpers."""

import logging
import re
import threading
import time
import uuid
from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage,
)
from ad_chapters import (
    merge_ad_chapters, public_chapters, refresh_keep_like_markers, resolve_ad_chapter_config,
)
from audio_processor import get_replacement_duration
from chapters_generator import ChaptersGenerator
from embedded_chapters import embed_chapters
from llm_client import (
    ProviderRateLimitedError, start_episode_token_tracking, get_episode_token_totals,
)
import run_context
from llm_route import resolve_route
from llm_capabilities import chapters_capability_error
from rate_limit_hold import (
    get_active_hold, hold_message, hold_queue_for_provider_limit,
    resolve_hold_scope,
)
from utils.constants import EpisodeStatus
from utils.text import truncate
from api.episodes import common

logger = logging.getLogger('podcast.api')


@api.route('/feeds/<slug>/episodes/<episode_id>/regenerate-chapters', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def regenerate_chapters(slug, episode_id):
    """Start chapter regeneration in a background thread; the episode row carries its state."""
    db = get_database()

    episode = db.get_episode_state(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    if not episode['has_transcript_vtt']:
        return error_response('No VTT transcript available - full reprocess required', 400)
    chapters_route = None
    try:
        chapters_route = resolve_route('chapters')
        chapters_provider = chapters_route.provider_key
        chapters_slot = chapters_route.credential_slot
    except Exception:
        # Resolution failed (e.g. no model configured yet): fall back to the
        # legacy unscoped check, a safe superset of any real provider hold.
        chapters_provider = None
        chapters_slot = 'primary'
    if chapters_route is not None:
        chapters_error = chapters_capability_error(
            db, chapters_route.provider_key, chapters_route.model_id)
    else:
        chapters_error = chapters_capability_error(db)
    if chapters_error:
        return error_response('Chapter regeneration requires a supported chat provider and model; '
                              'System One does not support chapters', 400)
    hold_until, _ = get_active_hold(db, chapters_provider, chapters_slot)
    if hold_until:
        return error_response(
            f'LLM provider is rate limited; new runs are held until {hold_until}', 409)
    stamp = db.claim_chapters_regen(slug, episode_id)
    if not stamp:
        return error_response(
            'Chapters are already being regenerated, or the episode is processing', 409)

    try:
        threading.Thread(target=_regenerate_chapters_job, args=(slug, episode_id, stamp),
                         name=f"chapters-regen-{episode_id}", daemon=True).start()
    except Exception:
        logger.exception(f"[{slug}:{episode_id}] Could not start the regeneration thread")
        db.finish_chapters_regen(slug, episode_id, stamp,
                                 error='Could not start regeneration thread')
        return error_response('Failed to start chapter regeneration', 500)
    logger.info(f"[{slug}:{episode_id}] Chapter regeneration started")
    return json_response({
        'message': 'Chapter regeneration started',
        'episodeId': episode_id,
        'status': 'started',
    }, 202)


def _regenerate_chapters_job(slug, episode_id, stamp):
    db = get_database()
    error = None
    podcast_name = slug
    episode = None
    ctx = run_context.begin(slug, episode_id, run_id=uuid.uuid4().hex)
    from main_app.pipeline.finalize import _end_run_log, _record_history_row, _start_run_log
    recorder = _start_run_log(slug, episode_id)
    started = time.perf_counter()
    start_episode_token_tracking()
    logger.info(f"[{slug}:{episode_id}] Chapter regeneration run started ({ctx.run_id})")
    try:
        episode = db.get_episode(slug, episode_id)
        if not episode:
            raise RuntimeError('Episode not found')
        podcast = db.get_podcast_by_slug(slug) or {}
        podcast_name = podcast.get('title') or slug
        _regenerate_chapters(db, get_storage(), slug, episode_id, episode,
                             podcast, podcast_name, stamp)
    except ProviderRateLimitedError as exc:
        error = truncate(str(exc), 500) or 'Chapter regeneration failed'
        try:
            hold_until = hold_queue_for_provider_limit(
                db, exc, slug=slug, episode_id=episode_id,
                podcast_name=podcast_name, phase='chapters')
            error = hold_message(hold_until, exc)
        except Exception:
            logger.exception(f"Failed to record the rate-limit hold for {slug}:{episode_id}")
    except Exception as exc:
        logger.exception(f"Failed to regenerate chapters for {slug}:{episode_id}")
        error = truncate(str(exc), 500) or 'Chapter regeneration failed'
    finally:
        token_totals = get_episode_token_totals()
        try:
            _record_history_row(
                db, slug, episode_id,
                (episode or {}).get('title') or 'Unknown', podcast_name,
                status='failed' if error else 'completed',
                processing_time=time.perf_counter() - started,
                ads_detected=0, token_totals=token_totals,
                error_message=error, run_stats={'mode': 'chapters'},
            )
        except Exception:
            logger.exception(f"Failed to record chapter regeneration history for {slug}:{episode_id}")
        finally:
            try:
                _end_run_log(recorder)
            finally:
                run_context.end(ctx)
                db.finish_chapters_regen(slug, episode_id, stamp, error=error)


def _regenerate_chapters(db, storage, slug, episode_id, episode, podcast, podcast_name, stamp):
    """Regenerate chapters from the VTT, save them, and embed them in the served MP3.

    VTT segments are already ad-adjusted, so ad boundaries are not applied again.
    """
    segments = parse_vtt_to_segments(episode['transcript_vtt'])
    if not segments:
        raise RuntimeError('Failed to parse VTT transcript')

    episode_description = episode.get('description', '')
    episode_title = episode.get('title', 'Unknown')

    # Segment markers and applied cuts (both persisted from the run that
    # produced this VTT) let chapter regen give the topic detector the same
    # ad/segment-position hints the pipeline gets. Missing either just falls
    # back to no hints.
    segment_markers = common._markers_from_row(episode)
    marker_cuts = storage.get_applied_cuts(slug, episode_id)

    # Reuse the job context so route, usage, and logs share one run ID.
    from main_app.pipeline.admission import _resolve_or_load_route_snapshot
    ctx = run_context.current()
    if ctx is None or not ctx.run_id:
        raise RuntimeError('Chapter regeneration has no run context')
    route_snapshot = _resolve_or_load_route_snapshot(ctx.run_id)
    if route_snapshot is not None:
        ctx.set_route_snapshot(route_snapshot)
    chapters_gen = ChaptersGenerator()

    # VTT segments are already ad-adjusted; omit ads_removed so
    # generate_chapters doesn't double-adjust. marker_cuts still maps
    # segment_markers onto the processed timeline for topic hints.
    try:
        with ctx.timing.measure('chapters'):
            chapters = chapters_gen.generate_chapters(
                segments,
                episode_description=episode_description,
                podcast_name=podcast_name,
                episode_title=episode_title,
                episode_id=episode_id,
                replacement_duration=get_replacement_duration(),
                segment_markers=segment_markers,
                marker_cuts=marker_cuts,
                slug=slug,
            )
    except ProviderRateLimitedError as exc:
        exc.provider_key, exc.credential_slot = resolve_hold_scope(exc, 'chapters')
        raise
    if getattr(chapters_gen, 'chapters_degraded', False) is True:
        raise RuntimeError(
            'Chapter generation was incomplete. Existing chapters were kept. '
            'Check Settings > AI Models and try again after recovery.')

    # A reprocess finishing during the LLM call above rewrote the transcript
    # these chapters came from, so they no longer describe the served audio.
    current = db.get_episode(slug, episode_id)
    if (current is None or current['status'] == EpisodeStatus.PROCESSING
            or current['processed_version'] != episode.get('processed_version')
            or current['processed_at'] != episode.get('processed_at')):
        raise RuntimeError(
            'Episode was reprocessed during chapter regeneration; chapters not saved')
    # This run outlived its claim window and another took the stamp over.
    if current['chapters_regen_started_at'] != stamp:
        raise RuntimeError(
            'Chapter regeneration was taken over by a newer run; chapters not saved')

    # Markers and cuts are re-read here, not reused from entry: a correction
    # applied during the LLM pass must not be reverted by the merge.
    current_markers = common._markers_from_row(current)
    current_cuts = storage.get_applied_cuts(slug, episode_id)
    # current_cuts None means no authoritative cuts persisted, not "no cuts":
    # skip the ad merge rather than place spans at original offsets.
    if current_cuts is None:
        logger.info(f"[{slug}:{episode_id}] No authoritative applied cuts "
                    f"persisted; skipping ad chapters")
    else:
        ad_config = resolve_ad_chapter_config(db, podcast)
        topic = (chapters or {}).get('chapters') or []
        # A category-action change since the marker was stamped applies only
        # to this rebuild; it does not rewrite ad_markers_json.
        refreshed_markers = (refresh_keep_like_markers(current_markers, ad_config.actions)
                             if ad_config else current_markers)
        merged = merge_ad_chapters(topic, refreshed_markers, current_cuts,
                                   segments[-1].get('end') if segments else None,
                                   get_replacement_duration(), ad_config)
        if merged:
            chapters = {**(chapters or {'version': '1.2.0'}), 'chapters': merged}

    if not chapters or not chapters.get('chapters'):
        raise RuntimeError('Failed to generate chapters')

    storage.save_chapters_json(slug, episode_id, chapters)
    logger.info(f"[{slug}:{episode_id}] Regenerated {len(chapters['chapters'])} chapters from VTT")
    # Also refresh ID3 chapters for players that ignore podcast:chapters.
    processed_path = storage.get_episode_path(
        slug, episode_id, version=current['processed_version'])
    if processed_path.exists():
        embed_chapters(str(processed_path), public_chapters(chapters['chapters']))
    # Refresh the served feed if it includes chapters.
    from main_app.pipeline.common import _refresh_rss_for_slug
    _refresh_rss_for_slug(slug, episode_id)


def parse_vtt_to_segments(vtt_content: str) -> list:
    """Parse VTT content back to segment list."""
    segments = []

    # VTT format: HH:MM:SS.mmm --> HH:MM:SS.mmm or MM:SS.mmm --> MM:SS.mmm
    pattern = r'(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*\n(.+?)(?=\n\n|\n\d|\Z)'

    for match in re.finditer(pattern, vtt_content, re.DOTALL):
        start_str, end_str, text = match.groups()

        # Parse timestamp to seconds
        def parse_vtt_time(time_str):
            parts = time_str.split(':')
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            else:
                m, s = parts
                return int(m) * 60 + float(s)

        segments.append({
            'start': parse_vtt_time(start_str),
            'end': parse_vtt_time(end_str),
            'text': text.strip()
        })

    return segments

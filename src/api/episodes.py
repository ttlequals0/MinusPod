"""Episode routes: episode listing, details, reprocessing, bulk actions."""
import json
import logging
import re
import threading

from flask import Response, redirect, request, send_file, abort, url_for
from werkzeug.utils import secure_filename

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, get_feed_auth_key,
    extract_transcript_segment, get_status_service,
    _resolve_original_audio,
)
from config import (
    is_pending_review, normalize_segment_category, resolve_chapters_in_notes,
    resolve_feed_processing_mode, DEFAULT_SEGMENT_ACTION,
    PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION, PROCESSING_MODE_CUE_ONLY,
)
from ad_chapters import (
    merge_ad_chapters, public_chapters, resolve_ad_chapter_config,
)
from ad_yield import latest_completed_run, low_ad_yield
from audio_peaks import compute_peaks, PeaksError
from audio_processor import AudioProcessor, get_replacement_duration
from cancel import request_cancellation, wait_for_cancellation
from chapters_generator import ChaptersGenerator
from database.podcasts import is_local_feed, is_recents_feed, recents_cutoff
from database.queue import (
    compute_queue_priority, PENDING_QUEUE_LIMIT,
    QUEUE_PRIORITY_MAX, QUEUE_PRIORITY_MIN,
)
from embedded_chapters import embed_chapters
from llm_client import (
    ProviderRateLimitedError, start_episode_token_tracking, get_episode_token_totals,
)
import run_context
from rate_limit_hold import get_active_hold, hold_message, hold_queue_for_provider_limit
from reprocess_modes import (
    REPROCESS_MODE_NEEDS_TRANSCRIPT, batch_clear_episodes_for_mode,
    clear_episode_for_mode, reset_episode_for_reprocess,
)
from split_planning import build_split_candidates, build_split_pieces
from chapter_notes import format_chapter_block
from utils.constants import EpisodeStatus
from utils.episode_paths import episode_public_url
from utils.text import (
    extract_timed_spans_in_range, parse_transcript_segments, truncate,
)
from utils.time import epoch_to_iso, utc_now_iso

logger = logging.getLogger('podcast.api')

# Terminal statuses a user can act on in bulk (reprocess/delete).
# 'deferred' episodes (#482) are included so a stuck offline queue
# can be force-retried or cleaned up by hand.
REPROCESSABLE_STATUSES = ('processed', 'failed', 'permanently_failed', 'deferred')


def _float_arg(name, default=None):
    """Float query arg: missing or empty returns default, junk aborts 400."""
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default
    try:
        return float(raw)
    except ValueError:
        abort(400, description=f"{name} must be a number")


def _check_recut_preconditions(db, slug, episode_id, episode):
    """Recut cuts the retained original from the saved detections and re-times
    the saved segments; it cannot run if any of those inputs are missing. Fail
    with an actionable message rather than silently falling back to an LLM
    reprocess. Returns an error_response or None."""
    if not get_storage().get_original_path(slug, episode_id).exists():
        return error_response(
            'Original audio was not retained for this episode, so it cannot '
            'be re-cut. Use "reprocess" or "full" to rebuild it from source.', 409)
    if not db.get_original_segments(slug, episode_id):
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

    Conservative: anything not plainly kept, rejected, or awaiting review
    counts as wanted, so an unclear marker costs a recut rather than a
    silently dropped decision.
    """
    if action == 'keep':
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
                            actions=None, false_positives=(), confirmed=()):
    """True when cutting the current markers reproduces the applied cuts.

    The decisions then changed only which ad chapters the audio should carry,
    so an apply can rebuild those instead of re-rendering the file. `actions`
    is the feed's resolved category action map, so a recategorized marker is
    judged the way the recut would judge it. Unknown inputs (no persisted cut
    list, no known duration) answer False: a recut is the safe fallback.
    """
    if applied_cuts is None or not original_duration:
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
    wanted = AudioProcessor().compute_applied_cuts(
        [dict(m, beep=(action == 'beep')) for m, action in resolved
         if _marker_wants_cut(m, action, false_positives, confirmed)],
        original_duration,
        cut_barriers=[m for m, action in resolved if action == 'keep'],
    )
    return (len(wanted) == len(applied_cuts)
            and all(_same_cut(w, a)
                    for w, a in zip(wanted, applied_cuts, strict=True)))


def _apply_needs_chapters_only(db, slug, episode_id, episode, markers) -> bool:
    """chapters_only_decisions for one pending-recut episode."""
    return chapters_only_decisions(
        markers, db.get_applied_cuts(slug, episode_id),
        episode.get('original_duration'),
        actions=db.resolve_segment_actions(slug),
        false_positives=db.get_false_positive_corrections(
            episode['podcast_id'], episode_id),
        confirmed=db.get_confirmed_corrections(
            episode['podcast_id'], episode_id))


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


def _processed_url(slug: str, episode_id: str, version: int,
                   key: str = None) -> str:
    """Build the same-origin processed-audio URL the UI player loads.

    Relative (no host) so the browser fetches it from the UI's own origin, not
    the public feed domain, which sits behind anti-scraper edge rules that
    break in-app playback. Matches the transcript/chapters URLs below. Carries
    a version suffix when set and the feed auth ``key`` so the public route
    does not 401 while authenticated feeds is enabled.
    """
    return episode_public_url("", slug, episode_id, version, key=key)


def _episode_token_fields(runs) -> dict:
    """API token fields from the most recent completed run (or empty dict).
    Derived from the processingRuns list already queried for the response,
    replacing a second (unindexed) processing_history lookup."""
    run = latest_completed_run(runs)
    if not run:
        return {}
    return {
        'inputTokens': run['inputTokens'],
        'outputTokens': run['outputTokens'],
        'llmCost': run['llmCost'],
    }


# ========== Episode Endpoints ==========

@api.route('/feeds/<slug>/episodes', methods=['GET'])
@log_request
def list_episodes(slug):
    """List episodes for a podcast."""
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    # Hoisted once for the whole list (not re-derived per episode in the
    # loop below): the local-artwork fallback needs to know whether this
    # feed is local.
    is_local = is_local_feed(podcast)

    # Get query params
    status = request.args.get('status', 'all')
    # The API emits 'completed' as the alias for processed episodes (see the
    # output mapping below), so accept that alias as a filter value too;
    # otherwise filtering by the status the client was handed returns nothing.
    status = EpisodeStatus.from_api(status)
    limit = min(max(1, request.args.get('limit', 25, type=int)), 500)
    offset = max(0, request.args.get('offset', 0, type=int))
    sort_by = request.args.get('sort_by', 'published_at')
    sort_dir = request.args.get('sort_dir', 'desc')

    if is_recents_feed(podcast):
        # Membership rows belong to other feeds; each carries its source.
        cutoff = recents_cutoff(podcast)
        if status in (None, 'all', 'processed'):
            episodes = db.get_recent_processed_episodes(cutoff, limit=limit, offset=offset,
                                                        sort_by=sort_by, sort_dir=sort_dir)
            total = db.count_recent_processed_episodes(cutoff)
        else:
            episodes, total = [], 0
    else:
        episodes, total = db.get_episodes(slug, status=status, limit=limit, offset=offset,
                                          sort_by=sort_by, sort_dir=sort_dir)

    episode_list = []
    for ep in episodes:
        source_slug = ep.get('source_slug')
        item = _episode_base_json(
            ep, slug=source_slug or slug,
            is_local=(ep.get('source_feed_type') == 'local') if source_slug else is_local,
            storage=storage)
        item['ad_count'] = ep['ads_removed']
        item['episodeNumber'] = ep.get('episode_number')
        if source_slug:
            item['feedSlug'] = source_slug
            item['feedTitle'] = ep['source_title']
        episode_list.append(item)

    return json_response({
        'episodes': episode_list,
        'total': total,
        'limit': limit,
        'offset': offset
    })


def _secure_artwork_url(url):
    """Publisher episode cover, or None when it is not http(s).

    The client turns this into a call to the episode artwork proxy rather
    than rendering it, and the proxy fetches server-side, so a plain-http
    cover is no longer a mixed-content problem worth dropping the image
    over: the browser only ever talks to MinusPod on the page's own scheme.
    """
    return url if (url or '').startswith(('https://', 'http://')) else None


def _local_artwork_fallback_url(ep, *, is_local, storage, slug):
    """The admin artwork proxy route, or None.

    Local episodes (uploaded/imported) never get an ``artwork_url`` column
    value -- that column is only ever populated from an upstream RSS item's
    image, and a local feed has no upstream. When the episode has a cached
    cover (embedded-art extraction or an explicit upload) but no
    ``artwork_url``, this falls back to the same-shaped internal proxy route
    every other consumer of this field gets (``_secure_artwork_url`` returns
    a plain http(s) URL the client fetches through its own artwork proxy;
    this returns the analogous same-origin route directly) -- NOT the
    public, feed-key-gated route local_feed_builder.py emits in the served
    RSS, which would leak the feed auth key into an ordinary API response.
    Subscribed-feed episodes are untouched: a missing artwork_url there
    legitimately means no cover.
    """
    if not is_local or storage is None:
        return None
    if not storage.has_episode_artwork(slug, ep['episode_id']):
        return None
    return f"/api/v1/feeds/{slug}/episodes/{ep['episode_id']}/artwork"


def _episode_base_json(ep, *, slug=None, is_local=False, storage=None):
    """Shared camelCase fields for the episode list and detail serializers.

    Status is mapped for frontend compatibility: 'processed' -> 'completed';
    discovered/permanently_failed pass through.

    ``slug``/``is_local``/``storage`` are only needed for the local-episode
    artworkUrl fallback (see _local_artwork_fallback_url) -- omitted, this
    behaves exactly as before (artworkUrl from the column only). Callers
    pass a hoisted ``is_local``/``storage`` rather than re-deriving them per
    episode, so a list response doesn't re-query the podcast row once per
    row.
    """
    time_saved = 0
    if ep.get('original_duration') and ep.get('new_duration'):
        time_saved = ep['original_duration'] - ep['new_duration']

    artwork_url = _secure_artwork_url(ep.get('artwork_url'))
    if artwork_url is None and slug is not None:
        artwork_url = _local_artwork_fallback_url(
            ep, is_local=is_local, storage=storage, slug=slug)

    return {
        'id': ep['episode_id'],
        'episodeId': ep['episode_id'],
        'title': ep['title'],
        'description': ep.get('description'),
        'status': EpisodeStatus.to_api(ep['status']),
        'published': ep.get('published_at') or ep['created_at'],
        'createdAt': ep['created_at'],
        'processedAt': ep['processed_at'],
        'duration': ep['original_duration'],
        'originalDuration': ep['original_duration'],
        'newDuration': ep['new_duration'],
        'adsRemoved': ep['ads_removed'],
        'timeSaved': time_saved,
        # True when this episode's original audio is still on disk (required
        # to mark cue templates or replay original audio) (#350).
        'hasOriginalAudio': bool(ep.get('original_file')),
        'error': ep.get('error_message'),
        'artworkUrl': artwork_url,
        'pendingReviewCount': ep.get('pending_review_count', 0),
    }


def _run_stats_to_api(stats):
    """Rename the pipeline's snake_case stats blob to API casing (or None)."""
    if not stats:
        return None
    stage_hits = stats.get('stage_hits')
    markers = stats.get('markers')
    return {
        'mode': stats.get('mode'),
        'detectionSkipped': stats.get('detection_skipped'),
        'verificationSkipped': stats.get('verification_skipped'),
        'cueOnly': stats.get('cue_only'),
        'transcriptionSkipped': stats.get('transcription_skipped'),
        'downloadedDuration': stats.get('downloaded_duration'),
        'transcriptSegments': stats.get('transcript_segments'),
        'windows': stats.get('windows'),
        'verificationWindows': stats.get('verification_windows'),
        'stageHits': {
            'fingerprint': stage_hits.get('fingerprint', 0),
            'textPattern': stage_hits.get('text_pattern', 0),
            'differential': stage_hits.get('differential', 0),
            'llm': stage_hits.get('llm', 0),
        } if stage_hits else None,
        'detected': stats.get('detected'),
        'markers': {
            'cut': markers.get('cut', 0),
            'held': markers.get('held', 0),
            'notCut': markers.get('not_cut', 0),
        } if markers else None,
        'verificationAdsCut': stats.get('verification_ads_cut'),
        'secondsRemoved': stats.get('seconds_removed'),
    }


def _processing_runs(db, episode):
    """Per-run history rows for the episode page's Processing stats section
    (#519). ``stats`` is the pipeline's per-run JSON blob; null for runs
    recorded before 2.53.0 and for recuts."""
    runs = []
    for row in db.get_episode_processing_runs(episode['podcast_id'],
                                              episode['episode_id']):
        stats = None
        if row.get('processing_stats_json'):
            try:
                stats = json.loads(row['processing_stats_json'])
            except (json.JSONDecodeError, TypeError):
                pass
        runs.append({
            'runNumber': row.get('reprocess_number'),
            'processedAt': row.get('processed_at'),
            'status': row.get('status'),
            'adsDetected': row.get('ads_detected'),
            'processingDurationSeconds': row.get('processing_duration_seconds'),
            'errorMessage': row.get('error_message'),
            'inputTokens': row.get('input_tokens') or 0,
            'outputTokens': row.get('output_tokens') or 0,
            'llmCost': round(row.get('llm_cost') or 0.0, 6),
            'hasLog': bool(row.get('log_file')),
            'stats': _run_stats_to_api(stats),
        })
    return runs


def _partial_detection(episode, runs):
    """Degraded pass-1 completion (episodes.detection_degraded). Window
    counts come from the run that produced the served audio (the latest
    completed one, same lookup low_ad_yield uses), when available."""
    reason = episode.get('detection_degraded')
    if not reason:
        return None
    latest_completed = latest_completed_run(runs) if runs else None
    latest_stats = ((latest_completed or {}).get('stats')) or {}
    windows = latest_stats.get('windows') or {}
    return {
        'reason': reason,
        'windowsFailed': windows.get('failed'),
        'windowsTotal': windows.get('total'),
    }


def _incomplete_coverage(runs):
    """Windows the latest completed run lost, per pass. Independent of
    detection_degraded: a run that answered most windows still completes, so
    the skipped stretches were never examined for ads."""
    latest_stats = ((latest_completed_run(runs) if runs else None) or {}).get('stats') or {}
    coverage = {}
    for key, pass_name in (('windows', 'detection'),
                           ('verificationWindows', 'verification')):
        counts = latest_stats.get(key) or {}
        failed = counts.get('failed') or 0
        if failed > 0:
            coverage[pass_name] = {'failed': failed, 'total': counts.get('total')}
    return coverage or None


@api.route('/feeds/<slug>/episodes/<episode_id>', methods=['GET'])
@log_request
def get_episode(slug, episode_id):
    """Get detailed episode information including transcript and ad markers."""
    db = get_database()
    storage = get_storage()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    podcast = db.get_podcast_by_slug(slug)
    is_local = is_local_feed(podcast)

    feed_auth_key = get_feed_auth_key(db)
    key_suffix = f"?key={feed_auth_key}" if feed_auth_key else ""

    # Parse ad markers if present, separating into four buckets:
    #   pendingReviewMarkers: held_for_review=True and not was_cut (checked FIRST)
    #   keptMarkers:          action_applied == 'keep' and not held (deliberate
    #                         per-category keep; keep resolution clears holds
    #                         upstream, so this never overlaps pendingReviewMarkers)
    #   rejectedAdMarkers:    REJECT decision or not was_cut (and not held/kept)
    #   adMarkers:            everything else (accepted cuts)
    ad_markers = []
    rejected_ad_markers = []
    pending_review_markers = []
    kept_markers = []
    if episode.get('ad_markers_json'):
        try:
            all_markers = json.loads(episode['ad_markers_json'])
            for marker in all_markers:
                decision = marker.get('validation', {}).get('decision', 'ACCEPT')
                # Markers persisted by a failed run were never cut.
                was_cut = marker.get('was_cut', episode.get('status') == EpisodeStatus.PROCESSED)
                # Absent stays absent. Defaulting to 'sponsor' here meant the
                # UI could not tell a real sponsor read from a marker no stage
                # ever classified.
                if marker.get('category') is None:
                    marker.pop('category', None)
                marker['actionApplied'] = marker.get('action_applied')
                if is_pending_review(marker):
                    pending_review_markers.append(marker)
                elif marker.get('action_applied') == 'keep':
                    kept_markers.append(marker)
                elif decision == 'REJECT' or not was_cut:
                    rejected_ad_markers.append(marker)
                else:
                    ad_markers.append(marker)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    # Cross-fetch differential result (Layer 3); null until a differential
    # run has stored one.
    dai_differential = None
    if episode.get('dai_differential_json'):
        try:
            dai_differential = json.loads(episode['dai_differential_json'])
        except (json.JSONDecodeError, TypeError):
            dai_differential = None

    base = _episode_base_json(episode, slug=slug, is_local=is_local, storage=storage)
    # Separate from description: the local-episode editor round-trips that
    # field, and the block must never be written back (#720).
    base['chapterNotes'] = (format_chapter_block(episode.get('chapters_json'))
                            if resolve_chapters_in_notes(db, podcast) else '')
    status = base['status']

    # Get file size and Podcasting 2.0 asset availability if processed
    file_size = None

    if status == EpisodeStatus.COMPLETED:
        file_path = storage.get_episode_path(slug, episode_id)
        if file_path.exists():
            file_size = file_path.stat().st_size

    # Check for Podcasting 2.0 assets (stored in database now)
    transcript_vtt_available = bool(episode.get('transcript_vtt'))
    chapters_available = bool(episode.get('chapters_json'))

    # Get corrections for this episode
    corrections = db.get_episode_corrections(episode['podcast_id'], episode_id)

    # Per-cue detection telemetry (advisory: template matches with score, how
    # detection used each cue, and the user's verdict). Empty for episodes
    # processed before cue templates existed.
    cue_detections = db.list_cue_detections_for_episode(
        episode['podcast_id'], episode_id)

    processing_runs = _processing_runs(db, episode)

    return json_response({
        **base,
        # Local-feed season/episode numbers (absent/None on a subscribed
        # feed's episodes). upload_local_episode/patch_local_episode already
        # echo these back; this GET handler had not, so EpisodeDetail.tsx's
        # edit form fell back to parsing them out of the episode id -- stale
        # the moment a season/episode edit changed the DB value without
        # renaming the id (#625 Task 13 review).
        'episodeNumber': episode.get('episode_number'),
        'seasonNumber': episode.get('season_number'),
        'originalUrl': episode['original_url'],
        'processedUrl': _processed_url(slug, episode_id,
                                       episode.get('processed_version') or 0,
                                       key=feed_auth_key),
        'originalAudioUrl': f"/api/v1/feeds/{slug}/episodes/{episode_id}/original.mp3",
        'adsRemovedFirstPass': episode.get('ads_removed_firstpass', 0),
        'adsRemovedVerification': episode.get('ads_removed_secondpass', 0),
        'fileSize': file_size,
        'adMarkers': ad_markers,
        'rejectedAdMarkers': rejected_ad_markers,
        'pendingReviewMarkers': pending_review_markers,
        'keptMarkers': kept_markers,
        'corrections': corrections,
        'cueDetections': cue_detections,
        'adDetectionStatus': episode.get('ad_detection_status'),
        'partialDetection': _partial_detection(episode, processing_runs),
        'incompleteCoverage': _incomplete_coverage(processing_runs),
        'daiDifferential': dai_differential,
        'transcript': episode.get('transcript_text'),
        'transcriptAvailable': bool(episode.get('transcript_text')),
        'originalTranscriptAvailable': bool(episode.get('has_original_transcript')),
        'transcriptVttAvailable': transcript_vtt_available,
        'chaptersRegenerating': bool(episode.get('chapters_regen_active')),
        'chaptersRegenError': episode.get('chapters_regen_error'),
        'transcriptVttUrl': (f"/episodes/{slug}/{episode_id}.vtt{key_suffix}"
                             if transcript_vtt_available else None),
        'chaptersAvailable': chapters_available,
        'chaptersUrl': (f"/episodes/{slug}/{episode_id}/chapters.json{key_suffix}"
                        if chapters_available else None),
        'firstPassPrompt': episode.get('first_pass_prompt'),
        'firstPassResponse': episode.get('first_pass_response'),
        'verificationPrompt': episode.get('second_pass_prompt'),
        'verificationResponse': episode.get('second_pass_response'),
        'rssDuration': episode.get('rss_duration'),
        'processingRuns': processing_runs,
        'lowAdYield': low_ad_yield(db, episode, processing_runs),
        'navigation': db.get_episode_neighbors(slug, episode_id),
        **_episode_token_fields(processing_runs),
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/artwork', methods=['GET'])
@log_request
def get_episode_artwork(slug, episode_id):
    """Serve an episode cover through MinusPod instead of hot-linking it.

    Publishers put hotlink protection in front of their images that rejects
    any request carrying a cross-site Referer, which is exactly what a
    browser sends for a hot-linked ``<img>``; the reader gets a 403 and a
    grey placeholder (issue #617). Fetching server-side sidesteps that
    whatever form the block takes, and keeps the reader's IP out of the
    publisher's logs.

    The URL comes from the episode row, never from the caller, so this
    cannot be aimed at an arbitrary host.
    """
    storage = get_storage()
    db = get_database()

    artwork = storage.get_episode_artwork(slug, episode_id)
    if not artwork:
        episode = db.get_episode(slug, episode_id)
        artwork_url = (episode or {}).get('artwork_url')
        if artwork_url and storage.download_episode_artwork(
                slug, episode_id, artwork_url):
            artwork = storage.get_episode_artwork(slug, episode_id)

    if not artwork:
        # No episode cover to be had: hand back the show cover so the row
        # keeps an image rather than collapsing to the grey placeholder.
        return redirect(url_for('api.get_artwork', slug=slug))

    image_data, content_type = artwork
    # content_type came from the magic-number check on write; forbid sniffing
    # and deny any script loading from this response.
    response = Response(image_data, mimetype=content_type)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'"
    return response


@api.route('/feeds/<slug>/episodes/<episode_id>/transcript', methods=['GET'])
@log_request
def get_transcript(slug, episode_id):
    """Get episode transcript."""
    storage = get_storage()

    transcript = storage.get_transcript(slug, episode_id)
    if not transcript:
        return error_response('Transcript not found', 404)

    return json_response({
        'episodeId': episode_id,
        'transcript': transcript
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/original-transcript', methods=['GET'])
@log_request
def get_original_transcript(slug, episode_id):
    """Get original (pre-cut) transcript for an episode."""
    db = get_database()

    transcript = db.get_original_transcript(slug, episode_id)
    if not transcript:
        return error_response('Original transcript not found', 404)

    return json_response({
        'episodeId': episode_id,
        'originalTranscript': transcript
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/original-segments', methods=['GET'])
@log_request
def get_original_segments(slug, episode_id):
    """Get original (pre-cut) Whisper segments JSON for an episode."""
    db = get_database()

    segments = db.get_original_segments(slug, episode_id)
    if segments is None:
        return error_response('Original segments not found', 404)

    return json_response({
        'episodeId': episode_id,
        'segments': segments
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/final-segments', methods=['GET'])
@log_request
def get_final_segments(slug, episode_id):
    """Get final (post-cut) segments JSON for an episode."""
    db = get_database()

    segments = db.get_final_segments(slug, episode_id)
    if segments is None:
        return error_response('Final segments not found', 404)

    return json_response({
        'episodeId': episode_id,
        'segments': segments
    })


def _wants_download():
    return request.args.get('download') == '1'


def _download_name(title, episode_id, suffix):
    """`<Title_words>-<suffix>.mp3`, title capped so the name stays under
    filesystem limits; falls back to the episode id."""
    return f"{secure_filename(title or '')[:120] or episode_id}-{suffix}.mp3"


def _audio_response(path, download_name=None):
    """Stream an mp3, or send it as an attachment when a name is given."""
    response = send_file(path, mimetype='audio/mpeg', conditional=True,
                         as_attachment=download_name is not None,
                         download_name=download_name)
    # Advertise byte-range support so the wavesurfer-based AdEditor can seek
    # without re-downloading the file. Without this header some clients
    # download serially and refuse to seek past the buffered tail.
    response.headers['Accept-Ranges'] = 'bytes'
    return response


@api.route('/feeds/<slug>/episodes/<episode_id>/original.mp3', methods=['GET'])
@log_request
def serve_original_audio(slug, episode_id):
    """Serve the retained pre-cut audio for ad-editor Review mode.

    Returns 404 when the episode has no original retained (global setting
    off, old episode processed before the feature, or retention expired).
    """
    # Blueprint url_value_preprocessor validated `slug` and `episode_id`.
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    path, err = _resolve_original_audio(db, get_storage(), slug, episode_id,
                                        self_heal=True, episode=episode)
    if err is not None:
        return err
    name = _download_name(episode.get('title'), episode_id, 'original') if _wants_download() else None
    return _audio_response(path, name)


@api.route('/feeds/<slug>/episodes/<episode_id>/processed.mp3', methods=['GET'])
@log_request
def serve_processed_audio(slug, episode_id):
    """Serve the current cut under session auth; keyed on the file, not the
    status, so the last cut stays downloadable during a reprocess."""
    episode = get_database().get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    path = get_storage().get_episode_path(
        slug, episode_id, version=episode.get('processed_version') or 0)
    if not path.exists():
        return error_response('Processed audio not available for this episode', 404)
    name = _download_name(episode.get('title'), episode_id, 'cut') if _wants_download() else None
    return _audio_response(path, name)


# Straight from the stdlib so warning/critical aliases cannot drift from what
# the recorder writes.
RUN_LOG_LEVELS = {name.lower(): value
                  for name, value in logging.getLevelNamesMapping().items()}
# A level name the map does not know ranks above every filter, so a custom or
# future level is never silently hidden.
UNKNOWN_RUN_LOG_LEVEL = 100


def _run_log_row(db, slug, episode_id, run_number):
    """The history row for this run, or (None, error_response)."""
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return None, error_response('Episode not found', 404)
    for row in db.get_episode_processing_runs(episode['podcast_id'],
                                              episode['episode_id']):
        if row.get('reprocess_number') == run_number:
            return row, None
    return None, error_response('Processing run not found', 404)


def _missing_log_response(code, message):
    """404 that says whether the log was never stored or has been pruned."""
    return json_response({'error': message, 'status': 404, 'code': code}, 404)


@api.route('/feeds/<slug>/episodes/<episode_id>/runs/<int:run_number>/log',
           methods=['GET'])
@log_request
def get_episode_run_log(slug, episode_id, run_number):
    """Return one processing run's pipeline log (#660).

    Query params:
        format (json|raw, default json) - raw downloads the JSONL file
        level  (debug|info|warning|error) - minimum level, json only
    """
    from run_log import TRUNCATION_MARKER, resolve_stored_log_path
    from storage import PathContainmentError

    db = get_database()
    row, err = _run_log_row(db, slug, episode_id, run_number)
    if err is not None:
        return err
    if not row.get('log_file'):
        return _missing_log_response(
            'log_not_stored', 'No log was stored for this run')

    try:
        path = resolve_stored_log_path(get_storage().data_dir, row['log_file'])
    except PathContainmentError:
        logger.warning(f"Run log pointer escapes the data dir: {row['log_file']}")
        return _missing_log_response('log_pruned', 'Run log is no longer available')
    if not path.exists():
        return _missing_log_response('log_pruned', 'Run log is no longer available')

    if request.args.get('format') == 'raw':
        filename = f"{slug}-{episode_id}-run{run_number}.jsonl"
        try:
            response = send_file(path, mimetype='text/plain', as_attachment=True,
                                 download_name=filename, conditional=True)
        except OSError:
            # The sweep can land between the check above and this read.
            return _missing_log_response('log_pruned', 'Run log is no longer available')
        # Quoted filename: werkzeug leaves a bare token unquoted, and the
        # documented header shape is the quoted one.
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        # Log text is attacker-influenced (episode titles, LLM output), so the
        # browser must neither sniff it nor run anything from it.
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        return response

    level_arg = (request.args.get('level') or '').lower()
    if level_arg and level_arg not in RUN_LOG_LEVELS:
        return error_response(
            f"level must be one of: {', '.join(sorted(RUN_LOG_LEVELS))}", 400)
    # Server-side filter kept alongside the viewer's client-side chips: the
    # API is usable on its own, the viewer filters without a refetch per chip.
    minimum = RUN_LOG_LEVELS.get(level_arg, 0)

    try:
        text = path.read_text(encoding='utf-8', errors='replace')
        size = path.stat().st_size
    except OSError:
        return _missing_log_response('log_pruned', 'Run log is no longer available')

    lines = []
    truncated = False
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except (json.JSONDecodeError, TypeError):
            continue
        if entry.get('msg') == TRUNCATION_MARKER:
            truncated = True
        rank = RUN_LOG_LEVELS.get(str(entry.get('level', '')).lower(),
                                  UNKNOWN_RUN_LOG_LEVEL)
        if rank < minimum:
            continue
        lines.append(entry)

    return json_response({
        'runNumber': run_number,
        'lines': lines,
        'truncated': truncated,
        'bytes': size,
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/peaks', methods=['GET'])
@log_request
def get_episode_peaks(slug, episode_id):
    """Return waveform peaks for the requested window of an episode.

    Query params:
        start    (float, default 0)         - window start in seconds
        end      (float, default duration)  - window end in seconds
        resolution_ms (int, default 50)     - peak bucket width

    Used by the AdEditor's wavesurfer view to render a waveform scoped to
    the current ad selection plus a user-selectable context window.
    """
    db = get_database()
    storage = get_storage()
    path, err = _resolve_original_audio(db, storage, slug, episode_id, self_heal=True)
    if err is not None:
        return err

    def _i(name, default):
        raw = request.args.get(name)
        if raw is None or raw == '':
            return default
        try:
            return int(raw)
        except ValueError:
            abort(400, description=f"{name} must be an integer")

    start_seconds = _float_arg('start', 0.0) or 0.0
    end_seconds = _float_arg('end')
    resolution_ms = _i('resolution_ms', 50)

    try:
        peaks, effective_resolution_ms = compute_peaks(
            path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            resolution_ms=resolution_ms,
        )
    except PeaksError as e:
        return error_response(str(e), 400)

    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        # Echo the *effective* resolution. Server auto-coarsens for very
        # long windows so the JSON payload stays bounded; callers should
        # render based on this value, not the one they requested.
        'resolutionMs': effective_resolution_ms,
        'peaks': peaks,
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/transcript-span', methods=['GET'])
@log_request
def get_episode_transcript_span(slug, episode_id):
    """Return the transcript text spanning a [start, end] window.

    Used by the AdEditor create mode to auto-populate the text_template
    field from the currently selected window of the episode's VTT.
    """
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    start_seconds = _float_arg('start', 0.0) or 0.0
    end_seconds = _float_arg('end')
    if end_seconds is None:
        return error_response('end is required', 400)
    if start_seconds < 0 or end_seconds <= start_seconds:
        return error_response('require 0 <= start < end', 400)
    duration = episode.get('original_duration') or 0
    if duration and end_seconds > duration + 1:
        return error_response(
            f'end ({end_seconds}) exceeds episode duration ({duration})', 400
        )

    transcript = db.get_transcript_for_timestamps(slug, episode_id)
    if not transcript:
        return error_response('Transcript not available for this episode', 404)

    text = extract_transcript_segment(transcript, start_seconds, end_seconds)
    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        'text': text or '',
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/split-candidates', methods=['GET'])
@log_request
def get_episode_split_candidates(slug, episode_id):
    """Propose divider points for a marker spanning several sponsors.

    A span with no transition phrase returns one piece and no candidates,
    a valid answer rather than an error (issue #563).
    """
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    start_seconds = _float_arg('start')
    end_seconds = _float_arg('end')
    if start_seconds is None or end_seconds is None:
        return error_response('start and end are required', 400)
    if start_seconds < 0 or end_seconds <= start_seconds:
        return error_response('require 0 <= start < end', 400)

    transcript = db.get_transcript_for_timestamps(slug, episode_id)
    spans = extract_timed_spans_in_range(
        transcript or '', start_seconds, end_seconds)
    candidates = build_split_candidates(spans, start_seconds, end_seconds)
    pieces = build_split_pieces(
        spans, start_seconds, end_seconds, [c['time'] for c in candidates])
    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        'candidates': candidates,
        'pieces': pieces,
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/reprocess', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def reprocess_episode(slug, episode_id):
    """Force reprocess an episode by deleting cached data and reprocessing.

    NOTE: This is the legacy endpoint. Prefer /episodes/<slug>/<episode_id>/reprocess
    which supports reprocess modes (reprocess vs full).
    """
    db = get_database()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == EpisodeStatus.PROCESSING:
        return error_response('Episode is currently processing', 409)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Podcast not found', 404)

    try:
        # Keep existing audio: reprocessing writes a new versioned file and
        # prunes the old one only after it's durable (orchestration-5).
        db.clear_episode_details(slug, episode_id)

        # Mark as user-initiated so the background drainer honors it
        # even on auto-process-disabled feeds.
        db.upsert_episode(
            slug, episode_id,
            status=EpisodeStatus.PENDING.value,
            reprocess_requested_at=utc_now_iso(),
            retry_count=0,
            error_message=None,
            deferred_at=None,
            deferred_service=None,
        )

        episode_url = episode.get('original_url')
        episode_title = episode.get('title', 'Unknown')
        podcast_name = podcast.get('title', slug)
        episode_description = episode.get('description')
        episode_published_at = episode.get('published_at')

        from main_app.processing import start_background_processing
        logger.info(f"[{slug}:{episode_id}] Starting reprocess (async)")

        started, reason = start_background_processing(
            slug, episode_id, episode_url, episode_title,
            podcast_name, episode_description, None, episode_published_at
        )

        if started:
            return json_response({
                'message': 'Episode reprocess started',
                'episodeId': episode_id,
                'status': 'processing'
            }, 202)
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
                'message': 'Episode queued for reprocess',
                'episodeId': episode_id,
                'status': 'queued',
                'reason': reason
            }, 202)

    except Exception:
        logger.exception(f"Failed to reprocess episode {slug}:{episode_id}")
        return error_response('Failed to reprocess', 500)


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
    hold_until, _ = get_active_hold(db)
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
                db, exc, slug=slug, episode_id=episode_id, podcast_name=podcast_name)
            error = hold_message(hold_until, exc)
        except Exception:
            logger.exception(f"Failed to record the rate-limit hold for {slug}:{episode_id}")
    except Exception as exc:
        logger.exception(f"Failed to regenerate chapters for {slug}:{episode_id}")
        error = truncate(str(exc), 500) or 'Chapter regeneration failed'
    finally:
        db.finish_chapters_regen(slug, episode_id, stamp, error=error)


def _markers_from_row(episode):
    """Parsed ad markers from an episode row; None when absent or unreadable."""
    if not episode.get('ad_markers_json'):
        return None
    try:
        return json.loads(episode['ad_markers_json'])
    except (json.JSONDecodeError, TypeError):
        return None


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
    segment_markers = _markers_from_row(episode)
    marker_cuts = storage.get_applied_cuts(slug, episode_id)

    ctx = run_context.begin(slug, episode_id)
    try:
        start_episode_token_tracking()
        chapters_gen = ChaptersGenerator()

        # VTT segments are already ad-adjusted; omit ads_removed so
        # generate_chapters doesn't double-adjust. marker_cuts still maps
        # segment_markers onto the processed timeline for topic hints.
        chapters = chapters_gen.generate_chapters(
            segments,
            episode_description=episode_description,
            podcast_name=podcast_name,
            episode_title=episode_title,
            episode_id=episode_id,
            replacement_duration=get_replacement_duration(),
            segment_markers=segment_markers,
            marker_cuts=marker_cuts,
        )

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
        current_markers = _markers_from_row(current)
        current_cuts = storage.get_applied_cuts(slug, episode_id)
        # current_cuts None means no authoritative cuts persisted, not "no cuts":
        # skip the ad merge rather than place spans at original offsets.
        if current_cuts is None:
            logger.info(f"[{slug}:{episode_id}] No authoritative applied cuts "
                        f"persisted; skipping ad chapters")
        else:
            ad_config = resolve_ad_chapter_config(db, podcast, slug=slug)
            topic = (chapters or {}).get('chapters') or []
            merged = merge_ad_chapters(topic, current_markers, current_cuts,
                                       segments[-1].get('end') if segments else None,
                                       get_replacement_duration(), ad_config)
            if merged:
                chapters = {**(chapters or {'version': '1.2.0'}), 'chapters': merged}

        if not chapters or not chapters.get('chapters'):
            raise RuntimeError('Failed to generate chapters')

        storage.save_chapters_json(slug, episode_id, chapters)
        logger.info(f"[{slug}:{episode_id}] Regenerated {len(chapters['chapters'])} chapters from VTT")
        # Also refresh the ID3 chapters in the served MP3 so players that
        # ignore podcast:chapters see the new set (issue #523).
        processed_path = storage.get_episode_path(
            slug, episode_id, version=current['processed_version'])
        if processed_path.exists():
            embed_chapters(str(processed_path), public_chapters(chapters['chapters']))
        # Same seam a finished run uses, so the served feed (which may
        # list the chapters, #720) picks up the new set.
        from main_app.processing import _refresh_rss_for_slug
        _refresh_rss_for_slug(slug, episode_id)
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
    eligible_ids = []

    # Batch-fetch all episodes upfront to avoid N+1 queries
    all_episodes = db.get_episodes_by_ids(slug, episode_ids)
    episodes_by_id = {ep['episode_id']: ep for ep in all_episodes}

    if action == 'process':
        # Collect eligible discovered episode IDs and batch-update
        eligible_ids = []
        for episode_id in episode_ids:
            episode = episodes_by_id.get(episode_id)
            if not episode or episode.get('status') != 'discovered':
                skipped += 1
                continue
            eligible_ids.append(episode_id)
        if eligible_ids:
            # reprocess_requested_at marks the row as user-initiated so the
            # background drainer's auto-process-disabled gate bypasses it.
            queued = db.batch_set_episodes_pending(slug, eligible_ids,
                                                    reprocess_requested_at=utc_now_iso())
            skipped += len(eligible_ids) - queued

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
    if action in ('process', 'reprocess', 'reprocess_full', 'reprocess_llm') and queued > 0:
        for episode_id in eligible_ids:
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

    return json_response({
        'queued': queued,
        'skipped': skipped,
        'freedMb': round(freed_mb, 2),
        'errors': errors,
    })


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
    mode = resolve_feed_processing_mode(podcast)
    if mode in (PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION,
                PROCESSING_MODE_CUE_ONLY):
        return error_response(
            f'Feed processing mode is {mode}; LLM ad detection is disabled '
            f'for this feed', 409)

    episode = db.get_episode(slug, episode_id)
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

@api.route('/episodes/processing', methods=['GET'])
@log_request
def get_processing_episodes():
    """Episodes processing now, then the pending queue in dequeue order.

    Sources: the DB's 'processing' rows plus StatusService.jobs for the
    active jobs; auto_process_queue pending rows plus StatusService's display
    queue for the backlog. Issue #236. The waiting list is paginated with the
    `offset`/`limit` query params (limit default 200, cap 1000); rows carry an
    offset-aware `queuePosition` so the panel can page through a long backlog
    (#696).
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
    # the active job and display-queue repeats, get_pending_queue_keys the rest.
    candidates = []
    for q in status.queued_episodes:
        key = (q['slug'], q['episode_id'])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(q)
    pending_keys = (db.get_pending_queue_keys([q['episode_id'] for q in candidates])
                    if candidates else set())
    valid_extras = [q for q in candidates
                    if (q['slug'], q['episode_id']) not in pending_keys]
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
    from main_app.processing import rebuild_ad_chapters

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
                                          _markers_from_row(episode) or [])

    def entry(e):
        running = _recut_handled_by_own_run(e)
        # Mirrors _check_recut_preconditions, so the UI can say which rows
        # an apply will rebuild now, which are mid-run, and which wait for
        # a full reprocess. A chapters-only row needs none of those inputs.
        data_ok = bool(
            e['has_segments'] and e['has_markers']
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
        markers = _markers_from_row(episode) or []
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
    db = get_database()

    data = request.get_json() or {}
    mode = data.get('mode', 'reprocess')

    if not _mode_allowed(mode, 'single'):
        return error_response('Invalid mode. Use "reprocess", "full", "llm", or "recut"', 400)
    mode_spec = REPROCESS_MODE_SPECS[mode]

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == EpisodeStatus.PROCESSING:
        return error_response('Episode is currently processing', 409)

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
                'message': f'Episode {mode} reprocess started',
                'mode': mode,
                'status': 'processing'
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
                'message': f'Episode queued for {mode} reprocess',
                'mode': mode,
                'status': 'queued',
                'reason': reason
            }, 202)

    except Exception:
        logger.exception(f"[{slug}:{episode_id}] {mode} reprocess failed")
        return error_response('Reprocess failed', 500)

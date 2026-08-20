"""Episode routes: episode listing, details, reprocessing, bulk actions."""
import json
import logging
import re

from flask import Response, redirect, request, send_file, abort, url_for

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, get_feed_auth_key,
    extract_transcript_segment, get_status_service,
    _resolve_original_audio,
)
from config import (
    is_pending_review, resolve_feed_processing_mode,
    PROCESSING_MODE_PASSTHROUGH, PROCESSING_MODE_SKIP_DETECTION, PROCESSING_MODE_CUE_ONLY,
)
from ad_yield import latest_completed_run, low_ad_yield
from audio_peaks import compute_peaks, PeaksError
from audio_processor import get_replacement_duration
from chapters_generator import ChaptersGenerator
from database.queue import compute_queue_priority
from embedded_chapters import embed_chapters
from llm_client import start_episode_token_tracking, get_episode_token_totals
from processing_queue import ProcessingQueue
from reprocess_modes import REPROCESS_MODE_CLEAR, clear_episode_for_mode
from split_planning import build_split_candidates, build_split_pieces
from utils.constants import EpisodeStatus
from utils.episode_paths import episode_public_url
from utils.text import (
    extract_timed_spans_in_range, parse_transcript_segments,
)
from utils.time import utc_now_iso

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


# Reprocess-mode rules shared by the three reprocess endpoints
# (reprocess_all_episodes = 'batch', bulk_episode_action = 'bulk',
# reprocess_episode_with_mode = 'single'). Fields:
#   contexts:         endpoints that may request the mode. recut stays
#                     single-episode-only by design.
#   needs_transcript: mode reuses the saved transcript to skip re-transcription
#                     and cannot run without one (issue #349).
#   preconditions:    extra per-episode input checks, single-episode-only
#                     (returns an error_response or None).
# What each mode wipes before requeueing lives in reprocess_modes, which the
# pipeline's automatic reruns import as well.
REPROCESS_MODE_SPECS = {
    'reprocess': {
        'contexts': ('batch', 'bulk', 'single'),
        'needs_transcript': False,
        'preconditions': None,
    },
    'full': {
        'contexts': ('batch', 'bulk', 'single'),
        'needs_transcript': False,
        'preconditions': None,
    },
    'llm': {
        'contexts': ('batch', 'bulk', 'single'),
        'needs_transcript': True,
        'preconditions': None,
    },
    'recut': {
        'contexts': ('single',),
        'needs_transcript': False,
        'preconditions': _check_recut_preconditions,
    },
}


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

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

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

    episodes, total = db.get_episodes(slug, status=status, limit=limit, offset=offset,
                                      sort_by=sort_by, sort_dir=sort_dir)

    episode_list = []
    for ep in episodes:
        item = _episode_base_json(ep)
        item['ad_count'] = ep['ads_removed']
        item['episodeNumber'] = ep.get('episode_number')
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


def _episode_base_json(ep):
    """Shared camelCase fields for the episode list and detail serializers.

    Status is mapped for frontend compatibility: 'processed' -> 'completed';
    discovered/permanently_failed pass through.
    """
    time_saved = 0
    if ep.get('original_duration') and ep.get('new_duration'):
        time_saved = ep['original_duration'] - ep['new_duration']

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
        'artworkUrl': _secure_artwork_url(ep.get('artwork_url')),
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


@api.route('/feeds/<slug>/episodes/<episode_id>', methods=['GET'])
@log_request
def get_episode(slug, episode_id):
    """Get detailed episode information including transcript and ad markers."""
    db = get_database()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

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

    base = _episode_base_json(episode)
    status = base['status']

    # Get file size and Podcasting 2.0 asset availability if processed
    file_size = None
    storage = get_storage()

    if status == EpisodeStatus.COMPLETED:
        file_path = storage.get_episode_path(slug, episode_id)
        if file_path.exists():
            file_size = file_path.stat().st_size

    # Check for Podcasting 2.0 assets (stored in database now)
    transcript_vtt_available = bool(episode.get('transcript_vtt'))
    chapters_available = bool(episode.get('chapters_json'))

    # Get corrections for this episode
    corrections = db.get_episode_corrections(episode_id)

    # Per-cue detection telemetry (advisory: template matches with score, how
    # detection used each cue, and the user's verdict). Empty for episodes
    # processed before cue templates existed.
    cue_detections = db.list_cue_detections_for_episode(
        episode['podcast_id'], episode_id)

    processing_runs = _processing_runs(db, episode)

    return json_response({
        **base,
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
        'daiDifferential': dai_differential,
        'transcript': episode.get('transcript_text'),
        'transcriptAvailable': bool(episode.get('transcript_text')),
        'originalTranscriptAvailable': bool(episode.get('has_original_transcript')),
        'transcriptVttAvailable': transcript_vtt_available,
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


@api.route('/feeds/<slug>/episodes/<episode_id>/original.mp3', methods=['GET'])
@log_request
def serve_original_audio(slug, episode_id):
    """Serve the retained pre-cut audio for ad-editor Review mode.

    Returns 404 when the episode has no original retained (global setting
    off, old episode processed before the feature, or retention expired).
    """
    # Blueprint url_value_preprocessor validated `slug` and `episode_id`.
    db = get_database()
    storage = get_storage()
    path, err = _resolve_original_audio(db, storage, slug, episode_id, self_heal=True)
    if err is not None:
        return err
    response = send_file(path, mimetype='audio/mpeg', conditional=True)
    # Advertise byte-range support so the wavesurfer-based AdEditor can seek
    # without re-downloading the file. Without this header some clients
    # download serially and refuse to seek past the buffered tail.
    response.headers['Accept-Ranges'] = 'bytes'
    return response


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
    """Regenerate chapters for an episode without full reprocessing.

    Uses existing VTT transcript to regenerate chapters with AI topic detection.
    VTT segments are already adjusted (ads removed), so we don't use ad boundaries.
    """
    db = get_database()
    storage = get_storage()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    # Get VTT transcript
    vtt_content = storage.get_transcript_vtt(slug, episode_id)
    if not vtt_content:
        return error_response('No VTT transcript available - full reprocess required', 400)

    # Parse VTT back to segments
    segments = parse_vtt_to_segments(vtt_content)
    if not segments:
        return error_response('Failed to parse VTT transcript', 500)

    # Get episode info
    episode_description = episode.get('description', '')
    podcast = db.get_podcast_by_slug(slug)
    podcast_name = podcast.get('title', slug) if podcast else slug
    episode_title = episode.get('title', 'Unknown')

    # Segment markers and applied cuts (both persisted from the run that
    # produced this VTT) let chapter regen give the topic detector the same
    # ad/segment-position hints the pipeline gets. Missing either just falls
    # back to no hints.
    segment_markers = None
    if episode.get('ad_markers_json'):
        try:
            segment_markers = json.loads(episode['ad_markers_json'])
        except (json.JSONDecodeError, TypeError):
            segment_markers = None
    marker_cuts = storage.get_applied_cuts(slug, episode_id)

    try:
        start_episode_token_tracking()
        chapters_gen = ChaptersGenerator()

        try:
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
        finally:
            token_totals = get_episode_token_totals()
            if token_totals['input_tokens'] > 0:
                db.increment_episode_token_usage(
                    episode_id,
                    token_totals['input_tokens'],
                    token_totals['output_tokens'],
                    token_totals['cost'],
                )

        if chapters and chapters.get('chapters'):
            storage.save_chapters_json(slug, episode_id, chapters)
            logger.info(f"[{slug}:{episode_id}] Regenerated {len(chapters['chapters'])} chapters from VTT")
            # Also refresh the ID3 chapters in the served MP3 so players that
            # ignore podcast:chapters see the new set (issue #523). Re-fetch
            # the row: a reprocess finishing during the LLM call above may
            # have bumped processed_version, and we must embed into the file
            # that is actually served now, not the stale version read at entry.
            embedded = False
            current = db.get_episode(slug, episode_id) or episode
            processed_path = storage.get_episode_path(
                slug, episode_id, version=current.get('processed_version'))
            if processed_path.exists():
                embedded = embed_chapters(str(processed_path), chapters['chapters'])
            return json_response({
                'message': 'Chapters regenerated',
                'episodeId': episode_id,
                'chapterCount': len(chapters['chapters']),
                'chapters': chapters['chapters'],
                'embedded': embedded
            })
        else:
            return error_response('Failed to generate chapters', 500)

    except Exception:
        logger.exception(f"Failed to regenerate chapters for {slug}:{episode_id}")
        return error_response('Failed to regenerate chapters', 500)


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
    mode_spec = REPROCESS_MODE_SPECS[mode]

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
        if mode_spec['needs_transcript'] and not db.has_transcript(slug, episode_id):
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
        mode_spec = REPROCESS_MODE_SPECS[mode]
        eligible_ids = []
        for episode_id in episode_ids:
            try:
                episode = episodes_by_id.get(episode_id)
                if not episode or episode.get('status') not in REPROCESSABLE_STATUSES:
                    skipped += 1
                    continue
                # LLM-only reprocess needs a saved transcript; skip episodes without one.
                if mode_spec['needs_transcript'] and not db.has_transcript(slug, episode_id):
                    skipped += 1
                    continue
                # Keep existing audio until the new version is durable (orchestration-5).
                eligible_ids.append(episode_id)
            except Exception as e:
                logger.error(f"Bulk action error for {slug}:{episode_id}: {e}")
                errors.append(f"{episode_id}: bulk action failed")
        if eligible_ids:
            # LLM-only mode preserves the transcript; other modes wipe the row.
            if REPROCESS_MODE_CLEAR[mode] == 'ad_data':
                db.batch_clear_episode_ad_data(slug, eligible_ids)
            else:
                db.batch_clear_episode_details(slug, eligible_ids)
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
                reset, freed = db.delete_episodes(slug, eligible_ids, storage)
                queued += reset
                freed_mb += freed
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
                    priority = compute_queue_priority(
                        podcast.get('queue_priority'), ep.get('published_at'), manual=True)
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
        try:
            # Load podcast tags for community-pattern eligibility.
            try:
                _tags_json = podcast.get('tags') if podcast else None
                podcast_tags = set(json.loads(_tags_json)) if _tags_json else None
            except Exception:
                podcast_tags = None
            # No positional_prior_hint here (issue #360): the stored transcript
            # is post-cut for processed episodes, so original-timeline hint
            # times would misdirect the model -- same reason pass 2 skips it.
            ad_result = ad_detector.process_transcript(
                segments, podcast_name, episode.get('title', 'Unknown'), slug, episode_id,
                podcast_id=slug,  # Pass slug as podcast_id for pattern matching
                podcast_tags=podcast_tags,
            )
        finally:
            token_totals = get_episode_token_totals()
            if token_totals['input_tokens'] > 0:
                db.increment_episode_token_usage(
                    episode_id,
                    token_totals['input_tokens'],
                    token_totals['output_tokens'],
                    token_totals['cost'],
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


# ========== Processing Queue Endpoints ==========

@api.route('/episodes/processing', methods=['GET'])
@log_request
def get_processing_episodes():
    """Episodes processing now (DB + StatusService current_job). Issue #236."""
    db = get_database()
    conn = db.get_connection()

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
    current = status.current_job
    if current:
        match = next((e for e in episodes
                      if e['slug'] == current.slug and e['episodeId'] == current.episode_id), None)
        if match:
            match['title'] = current.title or match['title']
            match['podcast'] = current.podcast_name or match['podcast']
            match['startedAt'] = current.started_at
            match['stage'] = current.stage
        else:
            episodes.append({
                'episodeId': current.episode_id,
                'slug': current.slug,
                'title': current.title or 'Unknown',
                'podcast': current.podcast_name or current.slug,
                'startedAt': current.started_at,
                'stage': current.stage,
            })

    # Append queued episodes (waiting on the lock) so the panel and banner
    # surface "what's next" alongside the active job.
    seen = {(e['slug'], e['episodeId']) for e in episodes}
    for q in status.queued_episodes:
        key = (q['slug'], q['episode_id'])
        if key in seen:
            continue
        seen.add(key)
        episodes.append({
            'episodeId': q['episode_id'],
            'slug': q['slug'],
            'title': q.get('title') or 'Unknown',
            'podcast': q.get('podcast_name') or q['slug'],
            'startedAt': None,
            'queuedAt': q.get('queued_at'),
            'stage': 'queued',
        })

    return json_response(episodes)


@api.route('/feeds/<slug>/episodes/<episode_id>/cancel', methods=['POST'])
@log_request
def cancel_episode_processing(slug, episode_id):
    """Cancel an episode that is processing OR queued."""
    from cancel import cancel_processing

    db = get_database()
    status_service = get_status_service()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        # Episode row gone (e.g. feed deleted mid-processing, issue #525). Still
        # tear down any orphaned job instead of 404-ing so the cancel button works.
        thread_signalled = cancel_processing(slug, episode_id)
        ProcessingQueue().release_if_processing(slug, episode_id)
        status_service.remove_queued_episode(slug, episode_id)
        status_service.clear_if_matches(slug, episode_id)
        logger.info(
            f"Canceled orphaned job (episode row missing): {slug}:{episode_id} "
            f"(thread_signalled={thread_signalled})"
        )
        return json_response({
            'message': 'Processing canceled',
            'episodeId': episode_id,
            'slug': slug
        })

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

    # Signal the processing thread to stop
    thread_signalled = cancel_processing(slug, episode_id)

    if not thread_signalled:
        # No active thread found -- reset DB and release queue directly (stuck episode fallback)
        conn = db.get_connection()
        conn.execute(
            """UPDATE episodes SET status = 'pending', error_message = 'Canceled by user'
               WHERE podcast_id = (SELECT id FROM podcasts WHERE slug = ?)
               AND episode_id = ?""",
            (slug, episode_id)
        )
        conn.commit()

        ProcessingQueue().release_if_processing(slug, episode_id)
    # else: thread will handle DB reset, file cleanup, and queue release

    # Belt-and-suspenders: clear any stale display-queue / auto_process_queue
    # entry for this episode so a follow-up enqueue starts from a clean slate.
    status_service.remove_queued_episode(slug, episode_id)
    db.close_queue_rows_for_episode(slug, episode_id)

    logger.info(f"Canceled processing: {slug}:{episode_id} (thread_signalled={thread_signalled})")
    return json_response({
        'message': 'Episode canceled and reset to pending',
        'episodeId': episode_id,
        'slug': slug
    })


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
    if mode_spec['needs_transcript'] and not db.has_transcript(slug, episode_id):
        return error_response(
            'No transcript available for LLM-only reprocess. '
            'Use "reprocess" or "full" to re-transcribe first.', 400)

    if mode_spec['preconditions']:
        err = mode_spec['preconditions'](db, slug, episode_id, episode)
        if err is not None:
            return err

    try:
        # 1. Set reprocess_mode FIRST so process_episode can read it
        db.upsert_episode(
            slug, episode_id,
            status=EpisodeStatus.PENDING.value,
            reprocess_mode=mode,
            reprocess_requested_at=utc_now_iso(),
            retry_count=0,
            error_message=None,
            deferred_at=None,
            deferred_service=None,
        )

        # 2. Clear cached detection data; keep the processed audio until the new
        # version is durable (orchestration-5).
        clear_episode_for_mode(db, slug, episode_id, mode)

        # 3. Get episode metadata for processing
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

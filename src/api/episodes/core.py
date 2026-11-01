"""Episode listing, detail and episode JSON helpers."""

import json
import logging
from flask import Response, redirect, request, send_file, url_for
from api import (
    api, log_request, json_response, error_response,
    get_database, get_storage, get_feed_auth_key,
)
from config import (
    is_keep_like, is_pending_review, resolve_chapters_in_notes,
    title_matches_skip_patterns, description_matches_skip_patterns,
    duration_outside_feed_range,
)
from ad_chapters import refresh_keep_like_markers
from ad_yield import latest_completed_run, low_ad_yield
from database.podcasts import is_local_feed, is_recents_feed, is_youtube_feed, recents_cutoff
from utils.json_fields import load_json_list
from utils.markers import parse_ad_markers
from chapter_notes import format_chapter_block
from utils.constants import EpisodeStatus
from utils.episode_paths import episode_public_url
import episode_runs
from api.episodes import common

logger = logging.getLogger('podcast.api')


# One more than the 500-episode bulk-action cap enforced in runs.
SELECTION_FETCH_CAP = 501


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
    is_youtube = is_youtube_feed(podcast)

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
    search = request.args.get('search', '').strip()
    selection = request.args.get('selection', '').lower() == 'true'
    query_limit = SELECTION_FETCH_CAP if selection else limit
    if selection:
        offset = 0

    if is_recents_feed(podcast):
        # Membership rows belong to other feeds; each carries its source.
        cutoff = recents_cutoff(podcast)
        if status in (None, 'all', 'processed'):
            episodes = db.get_recent_processed_episodes(cutoff, limit=query_limit, offset=offset,
                                                        sort_by=sort_by, sort_dir=sort_dir, search=search)
            total = db.count_recent_processed_episodes(cutoff, search=search)
        else:
            episodes, total = [], 0
    else:
        episodes, total = db.get_episodes(slug, status=status, limit=query_limit, offset=offset,
                                          sort_by=sort_by, sort_dir=sort_dir, search=search, selection=selection)

    # One batched lookup for the whole page rather than one query per row.
    job_states = db.get_episode_job_states([ep['episode_id'] for ep in episodes])

    episode_list = []
    for ep in episodes:
        source_slug = ep.get('source_slug')
        owner_slug = source_slug or slug
        if selection:
            filters = ({
                'min_duration_seconds': ep.get('source_min_duration_seconds'),
                'max_duration_seconds': ep.get('source_max_duration_seconds'),
            } if source_slug else podcast)
            status_key = EpisodeStatus.to_api(ep['status'])
            episode_list.append({
                'id': ep['episode_id'], 'status': status_key,
                'jobState': common._job_state(status_key, job_states.get((owner_slug, ep['episode_id']))),
                'titleSkipped': title_matches_skip_patterns(
                    ep.get('title'), ep.get('source_title_skip_patterns')
                    if source_slug else podcast.get('title_skip_patterns')),
                'descriptionSkipped': description_matches_skip_patterns(
                    ep.get('description'), ep.get('source_description_skip_patterns')
                    if source_slug else podcast.get('description_skip_patterns')),
                'durationSkipped': duration_outside_feed_range(ep.get('rss_duration'), filters),
            })
            continue
        item = _episode_base_json(
            ep, slug=owner_slug,
            is_local=(ep.get('source_feed_type') == 'local') if source_slug else is_local,
            is_youtube=(ep.get('source_feed_type') == 'youtube') if source_slug else is_youtube,
            storage=storage,
            title_skip_patterns=(ep.get('source_title_skip_patterns')
                                 if source_slug else podcast.get('title_skip_patterns')),
            description_skip_patterns=(ep.get('source_description_skip_patterns')
                                       if source_slug else podcast.get('description_skip_patterns')),
            duration_filters=({
                'min_duration_seconds': ep.get('source_min_duration_seconds'),
                'max_duration_seconds': ep.get('source_max_duration_seconds'),
            } if source_slug else podcast))
        item['ad_count'] = ep['ads_removed']
        item['episodeNumber'] = ep.get('episode_number')
        item['jobState'] = common._job_state(
            item['status'], job_states.get((owner_slug, ep['episode_id'])))
        if source_slug:
            item['feedSlug'] = source_slug
            item['feedTitle'] = ep['source_title']
        episode_list.append(item)

    return json_response({
        'episodes': [] if selection else episode_list,
        **({'selection': episode_list} if selection else {}),
        'total': total,
        'limit': limit,
        'offset': offset,
        **({'truncated': total > len(episode_list)} if selection else {}),
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


def _sponsorblock_json(ep):
    return {
        'state': ep.get('sb_state'),
        'attempts': ep.get('sb_attempts') or 0,
        'nextCheckAt': ep.get('sb_next_check_at'),
        'segmentCount': len(load_json_list(ep.get('sb_segment_uuids'))),
        'note': ep.get('sb_note'),
        'lastError': ep.get('sb_last_error'),
        'submissions': load_json_list(ep.get('sb_submissions')),
    }


def _episode_base_json(ep, *, slug=None, is_local=False, is_youtube=False, storage=None,
                       title_skip_patterns=None, description_skip_patterns=None,
                       duration_filters=None):
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
        'titleSkipped': title_matches_skip_patterns(
            ep.get('title'), title_skip_patterns),
        'descriptionSkipped': description_matches_skip_patterns(
            ep.get('description'), description_skip_patterns),
        'durationSkipped': duration_outside_feed_range(ep.get('rss_duration'), duration_filters),
        'passthroughEnabled': bool(ep.get('passthrough_enabled')),
        # Stable Process/Reprocess eligibility: a completed episode that is
        # queued again reverts to 'pending', so status alone flips the label.
        'hasBeenProcessed': bool(ep.get('has_been_processed')),
        'sponsorBlock': _sponsorblock_json(ep) if is_youtube else None,
        'rating': common.rating_json(ep),
    }


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
    #   keptMarkers:          action_applied keep-like and not held;
    #                         actionApplied tells keep from mark.
    #   rejectedAdMarkers:    REJECT decision or not was_cut (and not held/kept)
    #   adMarkers:            everything else (accepted cuts)
    ad_markers = []
    rejected_ad_markers = []
    pending_review_markers = []
    kept_markers = []
    if episode.get('ad_markers_json'):
        try:
            all_markers = parse_ad_markers(episode['ad_markers_json']) or []
            # Not persisted: a Keep<->Mark switch since these markers were
            # stamped shows up in actionApplied/the bucket here without a recut.
            all_markers = refresh_keep_like_markers(
                all_markers, db.resolve_segment_actions(slug, podcast))
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
                elif is_keep_like(marker.get('action_applied')):
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

    upstream_transcript = episode_runs.upstream_transcript_to_api(
        db.get_episode_upstream_transcript(slug, episode_id))

    splice_calibration = db.get_episode_splice_calibration(slug, episode_id)

    base = _episode_base_json(
        episode, slug=slug, is_local=is_local, is_youtube=is_youtube_feed(podcast), storage=storage,
        title_skip_patterns=podcast.get('title_skip_patterns'),
        description_skip_patterns=podcast.get('description_skip_patterns'),
        duration_filters=podcast)
    if base.get('sponsorBlock') is not None:
        base['sponsorBlock']['submitAllowed'] = common.sponsorblock_block_reason(db, podcast, episode) is None
    # Separate from description: the local-episode editor round-trips that
    # field, and the block must never be written back (#720).
    base['chapterNotes'] = (format_chapter_block(episode.get('chapters_json'))
                            if resolve_chapters_in_notes(db, podcast) else '')
    status = base['status']
    base['jobState'] = common._episode_job_state(db, slug, episode_id, status)

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

    run_totals = db.get_episode_run_usage_totals(episode['podcast_id'], episode_id)
    processing_runs = episode_runs.processing_runs(db, episode, run_totals)
    # Latest attempted run, failures and zero-LLM runs included; the completed
    # run below is a different question (which run produced the served audio).
    latest_run = processing_runs[-1] if processing_runs else None
    latest_run_spend = (episode_runs.run_spend(latest_run, run_totals.get(latest_run['runId']))
                        if latest_run else None)
    active_run_spend = episode_runs.active_run_spend(db, slug, episode_id)
    cumulative_spend = db.get_episode_cumulative_usage(episode['podcast_id'], episode_id)

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
        'partialDetection': episode_runs.partial_detection(episode, processing_runs),
        'incompleteCoverage': episode_runs.incomplete_coverage(processing_runs),
        'daiDifferential': dai_differential,
        'upstreamTranscript': upstream_transcript,
        'spliceCalibration': splice_calibration,
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
        'activeRunSpend': active_run_spend,
        'latestRunSpend': latest_run_spend,
        'cumulativeSpend': cumulative_spend,
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

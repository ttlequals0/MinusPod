"""Episode routes: episode listing, details, reprocessing, bulk actions."""
import json
import logging
import os
import re
from datetime import datetime, timezone

from flask import request

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage,
)
from utils.time import parse_timestamp, utc_now_iso

logger = logging.getLogger('podcast.api')


def _get_episode_token_fields(db, episode_id: str) -> dict:
    """Look up per-episode token usage and return API fields (or empty dict)."""
    usage = db.get_episode_token_usage(episode_id)
    if not usage:
        return {}
    return {
        'inputTokens': usage['input_tokens'],
        'outputTokens': usage['output_tokens'],
        'llmCost': round(usage['llm_cost'], 6),
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
    limit = min(int(request.args.get('limit', 25)), 500)
    offset = int(request.args.get('offset', 0))
    sort_by = request.args.get('sort_by', 'published_at')
    sort_dir = request.args.get('sort_dir', 'desc')

    episodes, total = db.get_episodes(slug, status=status, limit=limit, offset=offset,
                                      sort_by=sort_by, sort_dir=sort_dir)

    episode_list = []
    for ep in episodes:
        time_saved = 0
        if ep.get('original_duration') and ep.get('new_duration'):
            time_saved = ep['original_duration'] - ep['new_duration']

        # Map status for frontend compatibility
        # 'processed' -> 'completed'; discovered/permanently_failed pass through
        status = ep['status']
        if status == 'processed':
            status = 'completed'

        episode_list.append({
            # Frontend expected fields
            'id': ep['episode_id'],
            'title': ep['title'],
            'description': ep.get('description'),
            'status': status,
            'published': ep.get('published_at') or ep['created_at'],
            'duration': ep['original_duration'],
            'ad_count': ep['ads_removed'],
            # Additional fields for backward compatibility
            'episodeId': ep['episode_id'],
            'createdAt': ep['created_at'],
            'processedAt': ep['processed_at'],
            'originalDuration': ep['original_duration'],
            'newDuration': ep['new_duration'],
            'adsRemoved': ep['ads_removed'],
            'timeSaved': time_saved,
            'error': ep.get('error_message'),
            'artworkUrl': ep.get('artwork_url'),
            'episodeNumber': ep.get('episode_number')
        })

    return json_response({
        'episodes': episode_list,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@api.route('/feeds/<slug>/episodes/<episode_id>', methods=['GET'])
@log_request
def get_episode(slug, episode_id):
    """Get detailed episode information including transcript and ad markers."""
    db = get_database()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    base_url = os.environ.get('BASE_URL', 'http://localhost:8000')

    # Parse ad markers if present, separating by validation decision
    ad_markers = []
    rejected_ad_markers = []
    if episode.get('ad_markers_json'):
        try:
            all_markers = json.loads(episode['ad_markers_json'])
            # Separate by validation decision and cut status
            # Only actually-removed ads go in adMarkers; everything else is rejected
            for marker in all_markers:
                decision = marker.get('validation', {}).get('decision', 'ACCEPT')
                was_cut = marker.get('was_cut', True)
                if decision == 'REJECT' or not was_cut:
                    rejected_ad_markers.append(marker)
                else:
                    ad_markers.append(marker)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    time_saved = 0
    if episode.get('original_duration') and episode.get('new_duration'):
        time_saved = episode['original_duration'] - episode['new_duration']

    # Map status for frontend compatibility
    status = episode['status']
    if status == 'processed':
        status = 'completed'

    # Get file size and Podcasting 2.0 asset availability if processed
    file_size = None
    storage = get_storage()

    if status == 'completed':
        file_path = storage.get_episode_path(slug, episode_id)
        if file_path.exists():
            file_size = file_path.stat().st_size

    # Check for Podcasting 2.0 assets (stored in database now)
    transcript_vtt_available = bool(episode.get('transcript_vtt'))
    chapters_available = bool(episode.get('chapters_json'))

    # Get corrections for this episode
    corrections = db.get_episode_corrections(episode_id)

    return json_response({
        'id': episode['episode_id'],
        'episodeId': episode['episode_id'],
        'title': episode['title'],
        'description': episode.get('description'),
        'status': status,
        'published': episode.get('published_at') or episode['created_at'],
        'createdAt': episode['created_at'],
        'processedAt': episode['processed_at'],
        'duration': episode['original_duration'],
        'originalDuration': episode['original_duration'],
        'newDuration': episode['new_duration'],
        'originalUrl': episode['original_url'],
        'processedUrl': f"{base_url}/episodes/{slug}/{episode_id}.mp3",
        'adsRemoved': episode['ads_removed'],
        'adsRemovedFirstPass': episode.get('ads_removed_firstpass', 0),
        'adsRemovedVerification': episode.get('ads_removed_secondpass', 0),
        'timeSaved': time_saved,
        'fileSize': file_size,
        'adMarkers': ad_markers,
        'rejectedAdMarkers': rejected_ad_markers,
        'corrections': corrections,
        'adDetectionStatus': episode.get('ad_detection_status'),
        'transcript': episode.get('transcript_text'),
        'transcriptAvailable': bool(episode.get('transcript_text')),
        'originalTranscriptAvailable': bool(episode.get('has_original_transcript')),
        'transcriptVttAvailable': transcript_vtt_available,
        'transcriptVttUrl': f"/episodes/{slug}/{episode_id}.vtt" if transcript_vtt_available else None,
        'chaptersAvailable': chapters_available,
        'chaptersUrl': f"/episodes/{slug}/{episode_id}/chapters.json" if chapters_available else None,
        'error': episode.get('error_message'),
        'firstPassPrompt': episode.get('first_pass_prompt'),
        'firstPassResponse': episode.get('first_pass_response'),
        'verificationPrompt': episode.get('second_pass_prompt'),
        'verificationResponse': episode.get('second_pass_response'),
        'artworkUrl': episode.get('artwork_url'),
        **_get_episode_token_fields(db, episode_id),
    })


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


@api.route('/feeds/<slug>/episodes/<episode_id>/reprocess', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def reprocess_episode(slug, episode_id):
    """Force reprocess an episode by deleting cached data and reprocessing.

    NOTE: This is the legacy endpoint. Prefer /episodes/<slug>/<episode_id>/reprocess
    which supports reprocess modes (reprocess vs full).
    """
    db = get_database()
    storage = get_storage()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == 'processing':
        return error_response('Episode is currently processing', 409)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Podcast not found', 404)

    try:
        # 1. Delete processed audio file
        storage.delete_processed_file(slug, episode_id)

        # 2. Clear episode details from database (transcript, ads, etc.)
        db.clear_episode_details(slug, episode_id)

        # 3. Reset episode status to pending
        db.reset_episode_status(slug, episode_id)

        # 4. Get episode metadata for processing
        episode_url = episode.get('original_url')
        episode_title = episode.get('title', 'Unknown')
        podcast_name = podcast.get('title', slug)
        episode_description = episode.get('description')
        episode_published_at = episode.get('published_at')

        # 5. Start background processing (non-blocking, uses ProcessingQueue lock)
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
            }, 202)  # 202 Accepted - processing started asynchronously
        else:
            # Queue is busy - add to processing queue so background processor picks it up
            db.queue_episode_for_processing(
                slug, episode_id, episode_url, episode_title,
                episode_published_at, episode_description
            )
            logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), added to processing queue")
            return json_response({
                'message': 'Episode queued for reprocess',
                'episodeId': episode_id,
                'status': 'queued',
                'reason': reason
            }, 202)

    except Exception as e:
        logger.error(f"Failed to reprocess episode {slug}:{episode_id}: {e}")
        return error_response(f'Failed to reprocess: {str(e)}', 500)


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
    segments = _parse_vtt_to_segments(vtt_content)
    if not segments:
        return error_response('Failed to parse VTT transcript', 500)

    # Get episode info
    episode_description = episode.get('description', '')
    podcast = db.get_podcast_by_slug(slug)
    podcast_name = podcast.get('title', slug) if podcast else slug
    episode_title = episode.get('title', 'Unknown')

    try:
        from chapters_generator import ChaptersGenerator
        from llm_client import start_episode_token_tracking, get_episode_token_totals

        start_episode_token_tracking()
        chapters_gen = ChaptersGenerator()

        try:
            # VTT segments are ALREADY adjusted (ads removed), so pass empty ads_removed
            # This prevents double-adjustment of timestamps
            # The AI topic detection will find natural chapter points in the content
            chapters = chapters_gen.generate_chapters_from_vtt(
                segments, episode_description, podcast_name, episode_title
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
            return json_response({
                'message': 'Chapters regenerated',
                'episodeId': episode_id,
                'chapterCount': len(chapters['chapters']),
                'chapters': chapters['chapters']
            })
        else:
            return error_response('Failed to generate chapters', 500)

    except Exception as e:
        logger.error(f"Failed to regenerate chapters for {slug}:{episode_id}: {e}")
        return error_response(f'Failed to regenerate chapters: {str(e)}', 500)


def _parse_vtt_to_segments(vtt_content: str) -> list:
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
    """
    db = get_database()
    storage = get_storage()

    # Get mode from request body
    data = request.get_json() or {}
    mode = data.get('mode', 'reprocess')

    if mode not in ('reprocess', 'full'):
        return error_response('Invalid mode. Use "reprocess" or "full"', 400)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    # Get all episodes that have been processed
    episodes, _ = db.get_episodes(slug, status='processed')

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
        if episode.get('status') == 'processing':
            skipped.append({'episodeId': episode_id, 'reason': 'Already processing'})
            continue

        try:
            # Delete processed audio file
            storage.delete_processed_file(slug, episode_id)

            # Clear episode details from database
            db.clear_episode_details(slug, episode_id)

            # Reset status to pending with reprocess mode for priority queue
            db.upsert_episode(
                slug, episode_id,
                status='pending',
                reprocess_mode=mode,
                reprocess_requested_at=utc_now_iso(),
                retry_count=0,
                error_message=None
            )

            queued.append({'episodeId': episode_id, 'title': episode.get('title', '')})
            logger.info(f"Queued for reprocessing: {slug}:{episode_id}")

        except Exception as e:
            logger.error(f"Failed to queue {slug}:{episode_id} for reprocessing: {e}")
            skipped.append({'episodeId': episode_id, 'reason': str(e)})

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
    """Bulk actions on episodes: process, reprocess, reprocess_full, delete."""
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
    if action not in ('process', 'reprocess', 'reprocess_full', 'delete'):
        return error_response('Invalid action. Use: process, reprocess, reprocess_full, delete', 400)

    queued = 0
    skipped = 0
    freed_mb = 0.0
    errors = []

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
            queued = db.batch_set_episodes_pending(slug, eligible_ids)
            skipped += len(eligible_ids) - queued

    elif action in ('reprocess', 'reprocess_full'):
        # File cleanup must be per-episode, but DB updates are batched
        mode = 'full' if action == 'reprocess_full' else 'reprocess'
        eligible_ids = []
        for episode_id in episode_ids:
            try:
                episode = episodes_by_id.get(episode_id)
                if not episode or episode.get('status') not in ('processed', 'failed', 'permanently_failed'):
                    skipped += 1
                    continue
                storage.delete_processed_file(slug, episode_id)
                eligible_ids.append(episode_id)
            except Exception as e:
                logger.error(f"Bulk action error for {slug}:{episode_id}: {e}")
                errors.append(f"{episode_id}: {str(e)}")
        if eligible_ids:
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
            if not episode or episode.get('status') not in ('processed', 'failed', 'permanently_failed'):
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
                errors.append(str(e))

    # Trigger background processing for process/reprocess actions
    if action in ('process', 'reprocess', 'reprocess_full') and queued > 0:
        try:
            from main_app.processing import start_background_processing
            start_background_processing()
        except Exception:
            pass

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

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    # Get transcript
    transcript = storage.get_transcript(slug, episode_id)
    if not transcript:
        return error_response('No transcript available - full reprocess required', 400)

    try:
        from llm_client import start_episode_token_tracking, get_episode_token_totals

        # Parse transcript back into segments
        segments = []
        for line in transcript.split('\n'):
            if line.strip() and line.startswith('['):
                try:
                    # Parse format: [HH:MM:SS.mmm --> HH:MM:SS.mmm] text
                    time_part, text_part = line.split('] ', 1)
                    time_range = time_part.strip('[')
                    start_str, end_str = time_range.split(' --> ')

                    # Uses utils.time.parse_timestamp imported at module level
                    segments.append({
                        'start': parse_timestamp(start_str),
                        'end': parse_timestamp(end_str),
                        'text': text_part
                    })
                except Exception:
                    continue

        if not segments:
            return error_response('Could not parse transcript into segments', 400)

        # Get podcast info
        podcast = db.get_podcast_by_slug(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # Retry ad detection with token tracking
        start_episode_token_tracking()

        from ad_detector import AdDetector
        ad_detector = AdDetector()
        try:
            ad_result = ad_detector.process_transcript(
                segments, podcast_name, episode.get('title', 'Unknown'), slug, episode_id,
                podcast_id=slug  # Pass slug as podcast_id for pattern matching
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

    except Exception as e:
        logger.error(f"Failed to retry ad detection for {slug}:{episode_id}: {e}")
        return error_response(f'Failed to retry ad detection: {str(e)}', 500)


# ========== Processing Queue Endpoints ==========

@api.route('/episodes/processing', methods=['GET'])
@log_request
def get_processing_episodes():
    """Get all episodes currently in processing status."""
    db = get_database()
    conn = db.get_connection()

    cursor = conn.execute("""
        SELECT e.episode_id, e.title, p.slug, p.title as podcast
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.status = 'processing'
        ORDER BY e.updated_at DESC
    """)
    episodes = cursor.fetchall()

    return json_response([{
        'episodeId': ep['episode_id'],
        'slug': ep['slug'],
        'title': ep['title'] or 'Unknown',
        'podcast': ep['podcast'] or ep['slug'],
        'startedAt': None  # Could add timestamp tracking later
    } for ep in episodes])


@api.route('/feeds/<slug>/episodes/<episode_id>/cancel', methods=['POST'])
@log_request
def cancel_episode_processing(slug, episode_id):
    """Cancel/reset an episode stuck in processing status."""
    from cancel import cancel_processing

    db = get_database()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] != 'processing':
        return error_response(
            f"Episode is not processing (status: {episode['status']})",
            400
        )

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

        try:
            from processing_queue import ProcessingQueue
            queue = ProcessingQueue()
            if queue.is_processing(slug, episode_id):
                queue.release()
        except Exception as e:
            logger.warning(f"Could not release processing queue: {e}")
    # else: thread will handle DB reset, file cleanup, and queue release

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
    """
    db = get_database()
    storage = get_storage()

    data = request.get_json() or {}
    mode = data.get('mode', 'reprocess')

    if mode not in ('reprocess', 'full'):
        return error_response('Invalid mode. Use "reprocess" or "full"', 400)

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == 'processing':
        return error_response('Episode is currently processing', 409)

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Podcast not found', 404)

    try:
        # 1. Set reprocess_mode FIRST so process_episode can read it
        db.upsert_episode(
            slug, episode_id,
            status='pending',
            reprocess_mode=mode,
            reprocess_requested_at=utc_now_iso(),
            retry_count=0,
            error_message=None
        )

        # 2. Clear cached data
        storage.delete_processed_file(slug, episode_id)
        db.clear_episode_details(slug, episode_id)

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
            # Queue is busy - add to processing queue so background processor picks it up
            db.queue_episode_for_processing(
                slug, episode_id, episode_url, episode_title,
                episode_published_at, episode_description
            )
            logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), added to processing queue")
            return json_response({
                'message': f'Episode queued for {mode} reprocess',
                'mode': mode,
                'status': 'queued',
                'reason': reason
            }, 202)

    except Exception as e:
        logger.error(f"[{slug}:{episode_id}] {mode} reprocess failed: {e}")
        return error_response(f'Reprocess failed: {str(e)}', 500)

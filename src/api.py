"""REST API for podcast server web UI."""
import logging
import os
import time
from datetime import datetime
from flask import Blueprint, jsonify, request, send_file, Response
from functools import wraps

logger = logging.getLogger('podcast.api')

# Track server start time for uptime calculation
_start_time = time.time()

api = Blueprint('api', __name__, url_prefix='/api/v1')


def get_storage():
    """Get storage instance."""
    from storage import Storage
    return Storage()


def get_database():
    """Get database instance."""
    from database import Database
    return Database()


def log_request(f):
    """Decorator to log API requests with detailed info (IP, user-agent, response time)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        start_time = time.time()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_agent = request.headers.get('User-Agent', 'Unknown')[:100]

        try:
            result = f(*args, **kwargs)
            elapsed = (time.time() - start_time) * 1000  # ms
            status = result.status_code if hasattr(result, 'status_code') else 200
            logger.info(f"{request.method} {request.path} {status} {elapsed:.0f}ms [{client_ip}] [{user_agent}]")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"{request.method} {request.path} ERROR {elapsed:.0f}ms [{client_ip}] - {e}")
            raise
    return decorated


def json_response(data, status=200):
    """Create JSON response with proper headers."""
    response = jsonify(data)
    response.status_code = status
    return response


def error_response(message, status=400, details=None):
    """Create error response."""
    data = {'error': message, 'status': status}
    if details:
        data['details'] = details
    return json_response(data, status)


# ========== Feed Endpoints ==========

@api.route('/feeds', methods=['GET'])
@log_request
def list_feeds():
    """List all podcast feeds with metadata."""
    db = get_database()
    storage = get_storage()

    podcasts = db.get_all_podcasts()

    feeds = []
    for podcast in podcasts:
        # Build feed URL
        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
        feed_url = f"{base_url}/{podcast['slug']}"

        feeds.append({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'sourceUrl': podcast['source_url'],
            'feedUrl': feed_url,
            'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
            'episodeCount': podcast.get('episode_count', 0),
            'processedCount': podcast.get('processed_count', 0),
            'lastRefreshed': podcast.get('last_checked_at'),
            'createdAt': podcast.get('created_at'),
            'lastEpisodeDate': podcast.get('last_episode_date')
        })

    return json_response({'feeds': feeds})


@api.route('/feeds', methods=['POST'])
@log_request
def add_feed():
    """Add a new podcast feed."""
    data = request.get_json()

    # Debug logging for request data
    logger.debug(f"Add feed request data: {data}")

    if not data or 'sourceUrl' not in data:
        logger.warning(f"Missing sourceUrl in request. Data received: {data}")
        return error_response('sourceUrl is required', 400)

    source_url = data['sourceUrl'].strip()
    if not source_url:
        return error_response('sourceUrl cannot be empty', 400)

    # Generate slug from podcast name or use provided slug
    slug = data.get('slug', '').strip()
    if not slug:
        from slugify import slugify as make_slug
        from rss_parser import RSSParser

        # Fetch RSS to get podcast name for slug
        rss_parser = RSSParser()
        feed_content = rss_parser.fetch_feed(source_url)
        if feed_content:
            parsed_feed = rss_parser.parse_feed(feed_content)
            if parsed_feed and parsed_feed.feed:
                title = parsed_feed.feed.get('title', '')
                if title:
                    slug = make_slug(title)

        # Fallback to URL-based slug if name not available
        if not slug:
            from urllib.parse import urlparse
            parsed = urlparse(source_url)
            slug_base = parsed.path.strip('/').split('/')[-1] or parsed.netloc
            slug_base = slug_base.replace('.xml', '').replace('.rss', '')
            # Skip common generic path segments
            if slug_base.lower() in ('rss', 'feed', 'podcast', 'audio'):
                parts = parsed.path.strip('/').split('/')
                slug_base = parts[-2] if len(parts) > 1 else parsed.netloc
            slug = make_slug(slug_base)

    if not slug:
        return error_response('Could not generate valid slug', 400)

    db = get_database()

    # Check if slug already exists
    existing = db.get_podcast_by_slug(slug)
    if existing:
        return error_response(f'Feed with slug "{slug}" already exists', 409)

    # Create podcast
    try:
        podcast_id = db.create_podcast(slug, source_url)
        logger.info(f"Created new feed: {slug} -> {source_url}")

        # Trigger initial refresh in background
        try:
            from main import refresh_rss_feed
            refresh_rss_feed(slug, source_url)
        except Exception as e:
            logger.warning(f"Initial refresh failed for {slug}: {e}")

        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')

        return json_response({
            'slug': slug,
            'sourceUrl': source_url,
            'feedUrl': f"{base_url}/{slug}",
            'message': 'Feed added successfully'
        }, 201)

    except Exception as e:
        logger.error(f"Failed to add feed: {e}")
        return error_response(f'Failed to add feed: {str(e)}', 500)


@api.route('/feeds/<slug>', methods=['GET'])
@log_request
def get_feed(slug):
    """Get a single podcast feed by slug."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
    feed_url = f"{base_url}/{slug}"

    return json_response({
        'slug': podcast['slug'],
        'title': podcast['title'] or podcast['slug'],
        'sourceUrl': podcast['source_url'],
        'feedUrl': feed_url,
        'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
        'episodeCount': podcast.get('episode_count', 0),
        'processedCount': podcast.get('processed_count', 0),
        'lastRefreshed': podcast.get('last_checked_at'),
        'createdAt': podcast.get('created_at')
    })


@api.route('/feeds/<slug>', methods=['DELETE'])
@log_request
def delete_feed(slug):
    """Delete a podcast feed and all associated data."""
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    try:
        # Delete from database (cascade deletes episodes)
        db.delete_podcast(slug)

        # Delete files
        storage.cleanup_podcast_dir(slug)

        logger.info(f"Deleted feed: {slug}")
        return json_response({'message': 'Feed deleted', 'slug': slug})

    except Exception as e:
        logger.error(f"Failed to delete feed {slug}: {e}")
        return error_response(f'Failed to delete feed: {str(e)}', 500)


@api.route('/feeds/<slug>/refresh', methods=['POST'])
@log_request
def refresh_feed(slug):
    """Refresh a single podcast feed."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    if not podcast.get('source_url'):
        return error_response('Feed has no source URL', 400)

    try:
        from main import refresh_rss_feed
        refresh_rss_feed(slug, podcast['source_url'])

        # Get updated info
        podcast = db.get_podcast_by_slug(slug)
        episodes, total = db.get_episodes(slug)

        logger.info(f"Refreshed feed: {slug}")
        return json_response({
            'slug': slug,
            'message': 'Feed refreshed',
            'episodeCount': total,
            'lastRefreshed': podcast.get('last_checked_at')
        })

    except Exception as e:
        logger.error(f"Failed to refresh feed {slug}: {e}")
        return error_response(f'Failed to refresh feed: {str(e)}', 500)


@api.route('/feeds/refresh', methods=['POST'])
@log_request
def refresh_all_feeds():
    """Refresh all podcast feeds."""
    try:
        from main import refresh_all_feeds as do_refresh
        do_refresh()

        db = get_database()
        podcasts = db.get_all_podcasts()

        logger.info("Refreshed all feeds")
        return json_response({
            'message': 'All feeds refreshed',
            'feedCount': len(podcasts)
        })

    except Exception as e:
        logger.error(f"Failed to refresh all feeds: {e}")
        return error_response(f'Failed to refresh feeds: {str(e)}', 500)


@api.route('/feeds/<slug>/artwork', methods=['GET'])
@log_request
def get_artwork(slug):
    """Get cached artwork for a podcast."""
    storage = get_storage()

    artwork = storage.get_artwork(slug)
    if not artwork:
        # Try to get from database and download
        db = get_database()
        podcast = db.get_podcast_by_slug(slug)
        if podcast and podcast.get('artwork_url'):
            storage.download_artwork(slug, podcast['artwork_url'])
            artwork = storage.get_artwork(slug)

    if not artwork:
        return error_response('Artwork not found', 404)

    image_data, content_type = artwork
    return Response(image_data, mimetype=content_type)


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
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    episodes, total = db.get_episodes(slug, status=status, limit=limit, offset=offset)

    episode_list = []
    for ep in episodes:
        time_saved = 0
        if ep.get('original_duration') and ep.get('new_duration'):
            time_saved = ep['original_duration'] - ep['new_duration']

        # Map status for frontend compatibility
        status = ep['status']
        if status == 'processed':
            status = 'completed'

        episode_list.append({
            # Frontend expected fields
            'id': ep['episode_id'],
            'title': ep['title'],
            'description': ep.get('description'),
            'status': status,
            'published': ep['created_at'],
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
            'artworkUrl': ep.get('artwork_url')
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
            import json
            all_markers = json.loads(episode['ad_markers_json'])
            # Separate by validation decision - REJECT ads stayed in audio
            for marker in all_markers:
                decision = marker.get('validation', {}).get('decision', 'ACCEPT')
                if decision == 'REJECT':
                    rejected_ad_markers.append(marker)
                else:
                    ad_markers.append(marker)
        except:
            pass

    time_saved = 0
    if episode.get('original_duration') and episode.get('new_duration'):
        time_saved = episode['original_duration'] - episode['new_duration']

    # Map status for frontend compatibility
    status = episode['status']
    if status == 'processed':
        status = 'completed'

    # Get file size if processed
    file_size = None
    if status == 'completed':
        storage = get_storage()
        file_path = storage.get_episode_path(slug, episode_id)
        if file_path.exists():
            file_size = file_path.stat().st_size

    return json_response({
        'id': episode['episode_id'],
        'episodeId': episode['episode_id'],
        'title': episode['title'],
        'description': episode.get('description'),
        'status': status,
        'published': episode['created_at'],
        'createdAt': episode['created_at'],
        'processedAt': episode['processed_at'],
        'duration': episode['original_duration'],
        'originalDuration': episode['original_duration'],
        'newDuration': episode['new_duration'],
        'originalUrl': episode['original_url'],
        'processedUrl': f"{base_url}/episodes/{slug}/{episode_id}.mp3",
        'adsRemoved': episode['ads_removed'],
        'adsRemovedFirstPass': episode.get('ads_removed_firstpass', 0),
        'adsRemovedSecondPass': episode.get('ads_removed_secondpass', 0),
        'timeSaved': time_saved,
        'fileSize': file_size,
        'adMarkers': ad_markers,
        'rejectedAdMarkers': rejected_ad_markers,
        'adDetectionStatus': episode.get('ad_detection_status'),
        'transcript': episode.get('transcript_text'),
        'transcriptAvailable': bool(episode.get('transcript_text')),
        'error': episode.get('error_message'),
        'firstPassPrompt': episode.get('first_pass_prompt'),
        'firstPassResponse': episode.get('first_pass_response'),
        'secondPassPrompt': episode.get('second_pass_prompt'),
        'secondPassResponse': episode.get('second_pass_response'),
        'artworkUrl': episode.get('artwork_url')
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


@api.route('/feeds/<slug>/episodes/<episode_id>/reprocess', methods=['POST'])
@log_request
def reprocess_episode(slug, episode_id):
    """Force reprocess an episode by deleting cached data and reprocessing immediately."""
    db = get_database()
    storage = get_storage()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] == 'processing':
        return error_response('Episode is currently processing', 409)

    try:
        # 1. Delete processed audio file
        storage.delete_processed_file(slug, episode_id)

        # 2. Clear episode details from database (transcript, ads, etc.)
        db.clear_episode_details(slug, episode_id)

        # 3. Reset episode status to pending
        db.reset_episode_status(slug, episode_id)

        # 4. Trigger immediate reprocessing
        from main import process_episode
        from rss_parser import RSSParser

        episode_url = episode.get('original_url')
        episode_title = episode.get('title', 'Unknown')

        podcast = db.get_podcast_by_slug(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # Fetch episode description from RSS if available
        episode_description = None
        if podcast and podcast.get('source_url'):
            try:
                rss_parser = RSSParser()
                feed_content = rss_parser.fetch_feed(podcast['source_url'])
                if feed_content:
                    episodes = rss_parser.extract_episodes(feed_content)
                    for ep in episodes:
                        if ep['id'] == episode_id:
                            episode_description = ep.get('description')
                            break
            except Exception as e:
                logger.warning(f"Could not fetch episode description: {e}")

        logger.info(f"Starting reprocess: {slug}:{episode_id}")
        success = process_episode(slug, episode_id, episode_url, episode_title, podcast_name, episode_description)

        if success:
            return json_response({
                'message': 'Episode reprocessed successfully',
                'episodeId': episode_id,
                'status': 'completed'
            })
        else:
            return json_response({
                'message': 'Episode reprocessing failed',
                'episodeId': episode_id,
                'status': 'failed'
            }, 500)

    except Exception as e:
        logger.error(f"Failed to reprocess episode {slug}:{episode_id}: {e}")
        return error_response(f'Failed to reprocess: {str(e)}', 500)


@api.route('/feeds/<slug>/episodes/<episode_id>/retry-ad-detection', methods=['POST'])
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
        # Parse transcript back into segments
        segments = []
        for line in transcript.split('\n'):
            if line.strip() and line.startswith('['):
                try:
                    # Parse format: [HH:MM:SS.mmm --> HH:MM:SS.mmm] text
                    time_part, text_part = line.split('] ', 1)
                    time_range = time_part.strip('[')
                    start_str, end_str = time_range.split(' --> ')

                    def parse_timestamp(ts):
                        parts = ts.replace(',', '.').split(':')
                        if len(parts) == 3:
                            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                        elif len(parts) == 2:
                            return float(parts[0]) * 60 + float(parts[1])
                        else:
                            return float(parts[0])

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

        # Retry ad detection
        from ad_detector import AdDetector
        ad_detector = AdDetector()
        ad_result = ad_detector.process_transcript(
            segments, podcast_name, episode.get('title', 'Unknown'), slug, episode_id
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
    db = get_database()

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    if episode['status'] != 'processing':
        return error_response(
            f"Episode is not processing (status: {episode['status']})",
            400
        )

    # Reset status to pending - use podcast_id join to find by slug
    conn = db.get_connection()
    conn.execute(
        """UPDATE episodes SET status = 'pending', error_message = 'Canceled by user'
           WHERE podcast_id = (SELECT id FROM podcasts WHERE slug = ?)
           AND episode_id = ?""",
        (slug, episode_id)
    )
    conn.commit()

    # Release from processing queue if held
    try:
        from processing_queue import ProcessingQueue
        queue = ProcessingQueue()
        if queue.is_processing(slug, episode_id):
            queue.release()
    except Exception as e:
        logger.warning(f"Could not release processing queue: {e}")

    logger.info(f"Canceled processing: {slug}:{episode_id}")
    return json_response({
        'message': 'Episode canceled and reset to pending',
        'episodeId': episode_id,
        'slug': slug
    })


# ========== Settings Endpoints ==========

@api.route('/settings', methods=['GET'])
@log_request
def get_settings():
    """Get all settings."""
    db = get_database()
    from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_SECOND_PASS_PROMPT
    from ad_detector import AdDetector, DEFAULT_MODEL

    settings = db.get_all_settings()

    # Get current model settings
    current_model = settings.get('claude_model', {}).get('value', DEFAULT_MODEL)
    second_pass_model = settings.get('second_pass_model', {}).get('value', DEFAULT_MODEL)

    # Get multi-pass setting (defaults to false)
    multi_pass_value = settings.get('multi_pass_enabled', {}).get('value', 'false')
    multi_pass_enabled = multi_pass_value.lower() in ('true', '1', 'yes')

    # Get whisper model setting (defaults to env var or 'small')
    default_whisper_model = os.environ.get('WHISPER_MODEL', 'small')
    whisper_model = settings.get('whisper_model', {}).get('value', default_whisper_model)

    # Get audio analysis setting (defaults to false)
    audio_analysis_value = settings.get('audio_analysis_enabled', {}).get('value', 'false')
    audio_analysis_enabled = audio_analysis_value.lower() in ('true', '1', 'yes')

    return json_response({
        'systemPrompt': {
            'value': settings.get('system_prompt', {}).get('value', DEFAULT_SYSTEM_PROMPT),
            'isDefault': settings.get('system_prompt', {}).get('is_default', True)
        },
        'secondPassPrompt': {
            'value': settings.get('second_pass_prompt', {}).get('value', DEFAULT_SECOND_PASS_PROMPT),
            'isDefault': settings.get('second_pass_prompt', {}).get('is_default', True)
        },
        'claudeModel': {
            'value': current_model,
            'isDefault': settings.get('claude_model', {}).get('is_default', True)
        },
        'secondPassModel': {
            'value': second_pass_model,
            'isDefault': settings.get('second_pass_model', {}).get('is_default', True)
        },
        'multiPassEnabled': {
            'value': multi_pass_enabled,
            'isDefault': settings.get('multi_pass_enabled', {}).get('is_default', True)
        },
        'whisperModel': {
            'value': whisper_model,
            'isDefault': settings.get('whisper_model', {}).get('is_default', True)
        },
        'audioAnalysisEnabled': {
            'value': audio_analysis_enabled,
            'isDefault': settings.get('audio_analysis_enabled', {}).get('is_default', True)
        },
        'retentionPeriodMinutes': int(os.environ.get('RETENTION_PERIOD') or settings.get('retention_period_minutes', {}).get('value', '1440')),
        'defaults': {
            'systemPrompt': DEFAULT_SYSTEM_PROMPT,
            'secondPassPrompt': DEFAULT_SECOND_PASS_PROMPT,
            'claudeModel': DEFAULT_MODEL,
            'secondPassModel': DEFAULT_MODEL,
            'multiPassEnabled': False,
            'whisperModel': default_whisper_model,
            'audioAnalysisEnabled': False
        }
    })


@api.route('/settings/ad-detection', methods=['PUT'])
@log_request
def update_ad_detection_settings():
    """Update ad detection settings."""
    data = request.get_json()

    if not data:
        return error_response('Request body required', 400)

    db = get_database()

    if 'systemPrompt' in data:
        db.set_setting('system_prompt', data['systemPrompt'], is_default=False)
        logger.info("Updated system prompt")

    if 'secondPassPrompt' in data:
        db.set_setting('second_pass_prompt', data['secondPassPrompt'], is_default=False)
        logger.info("Updated second pass prompt")

    if 'claudeModel' in data:
        db.set_setting('claude_model', data['claudeModel'], is_default=False)
        logger.info(f"Updated Claude model to: {data['claudeModel']}")

    if 'secondPassModel' in data:
        db.set_setting('second_pass_model', data['secondPassModel'], is_default=False)
        logger.info(f"Updated second pass model to: {data['secondPassModel']}")

    if 'multiPassEnabled' in data:
        value = 'true' if data['multiPassEnabled'] else 'false'
        db.set_setting('multi_pass_enabled', value, is_default=False)
        logger.info(f"Updated multi-pass detection to: {value}")

    if 'whisperModel' in data:
        db.set_setting('whisper_model', data['whisperModel'], is_default=False)
        logger.info(f"Updated Whisper model to: {data['whisperModel']}")
        # Trigger model reload on next transcription
        try:
            from transcriber import WhisperModelSingleton
            WhisperModelSingleton.mark_for_reload()
        except Exception as e:
            logger.warning(f"Could not mark model for reload: {e}")

    if 'audioAnalysisEnabled' in data:
        value = 'true' if data['audioAnalysisEnabled'] else 'false'
        db.set_setting('audio_analysis_enabled', value, is_default=False)
        logger.info(f"Updated audio analysis to: {value}")

    return json_response({'message': 'Settings updated'})


@api.route('/settings/ad-detection/reset', methods=['POST'])
@log_request
def reset_ad_detection_settings():
    """Reset ad detection settings to defaults."""
    db = get_database()

    db.reset_setting('system_prompt')
    db.reset_setting('second_pass_prompt')
    db.reset_setting('claude_model')
    db.reset_setting('second_pass_model')
    db.reset_setting('multi_pass_enabled')
    db.reset_setting('whisper_model')
    db.reset_setting('audio_analysis_enabled')

    # Mark whisper model for reload
    try:
        from transcriber import WhisperModelSingleton
        WhisperModelSingleton.mark_for_reload()
    except Exception as e:
        logger.warning(f"Could not mark model for reload: {e}")

    logger.info("Reset ad detection settings to defaults")
    return json_response({'message': 'Settings reset to defaults'})


@api.route('/settings/models', methods=['GET'])
@log_request
def get_available_models():
    """Get list of available Claude models."""
    from ad_detector import AdDetector

    ad_detector = AdDetector()
    models = ad_detector.get_available_models()

    return json_response({'models': models})


@api.route('/settings/whisper-models', methods=['GET'])
@log_request
def get_whisper_models():
    """Get list of available Whisper models with resource requirements."""
    models = [
        {
            'id': 'tiny',
            'name': 'Tiny',
            'vram': '~1GB',
            'speed': '~1 min/60min',
            'quality': 'Basic'
        },
        {
            'id': 'base',
            'name': 'Base',
            'vram': '~1GB',
            'speed': '~1.5 min/60min',
            'quality': 'Good'
        },
        {
            'id': 'small',
            'name': 'Small (Default)',
            'vram': '~2GB',
            'speed': '~2-3 min/60min',
            'quality': 'Better'
        },
        {
            'id': 'medium',
            'name': 'Medium',
            'vram': '~5GB',
            'speed': '~4-5 min/60min',
            'quality': '~15% better than Small'
        },
        {
            'id': 'large-v3',
            'name': 'Large v3',
            'vram': '~10GB',
            'speed': '~6-8 min/60min',
            'quality': '~25% better than Small'
        }
    ]
    return json_response({'models': models})


# ========== System Endpoints ==========

@api.route('/system/status', methods=['GET'])
@log_request
def get_system_status():
    """Get system status and statistics."""
    db = get_database()
    storage = get_storage()

    stats = db.get_stats()
    storage_stats = storage.get_storage_stats()

    # Get retention setting - env var takes precedence
    retention = int(os.environ.get('RETENTION_PERIOD') or
                    db.get_setting('retention_period_minutes') or '1440')

    return json_response({
        'status': 'running',
        'version': _get_version(),
        'uptime': int(time.time() - _start_time),
        'feeds': {
            'total': stats['podcast_count']
        },
        'episodes': {
            'total': stats['episode_count'],
            'byStatus': stats['episodes_by_status']
        },
        'storage': {
            'usedMb': storage_stats['total_size_mb'],
            'fileCount': storage_stats['file_count']
        },
        'settings': {
            'retentionPeriodMinutes': retention,
            'whisperModel': os.environ.get('WHISPER_MODEL', 'small'),
            'whisperDevice': os.environ.get('WHISPER_DEVICE', 'cuda'),
            'baseUrl': os.environ.get('BASE_URL', 'http://localhost:8000')
        },
        'stats': {
            'totalTimeSaved': db.get_total_time_saved()
        }
    })


@api.route('/system/cleanup', methods=['POST'])
@log_request
def trigger_cleanup():
    """Delete ALL processed episodes immediately (ignores retention period)."""
    db = get_database()

    deleted_count, freed_mb = db.cleanup_old_episodes(force_all=True)

    logger.info(f"Manual cleanup: {deleted_count} episodes deleted, {freed_mb:.1f} MB freed")
    return json_response({
        'message': 'All episodes deleted',
        'episodesRemoved': deleted_count,
        'spaceFreedMb': round(freed_mb, 2)
    })


def _get_version():
    """Get application version."""
    try:
        import sys
        from pathlib import Path
        # Add parent directory to path for version module
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from version import __version__
        return __version__
    except ImportError:
        return 'unknown'

"""REST API for podcast server web UI."""
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional
from flask import Blueprint, jsonify, request, send_file, Response, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger('podcast.api')

# Track server start time for uptime calculation
_start_time = time.time()

api = Blueprint('api', __name__, url_prefix='/api/v1')

# Rate limiter - will be initialized when blueprint is registered with app
# Default limits: 200 requests per minute, 1000 per hour
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri="memory://",
)


def init_limiter(app):
    """Initialize rate limiter with Flask app."""
    limiter.init_app(app)
    logger.info("Rate limiter initialized: 200/min, 1000/hr default limits")


# Paths that don't require authentication
AUTH_EXEMPT_PATHS = {
    '/api/v1/health',
    '/api/v1/auth/status',
    '/api/v1/auth/login',
    '/api/v1/auth/logout',
}

# Path prefixes that don't require authentication
AUTH_EXEMPT_PREFIXES = (
    '/api/v1/auth/',
    '/api/v1/status/stream',  # SSE stream - EventSource can't handle 401 gracefully
)


@api.before_request
def check_auth():
    """Check authentication before each request.

    Exempt paths:
    - /health - health check endpoint
    - /auth/* - authentication endpoints
    - /feeds/<slug>/rss - RSS feed endpoints (for podcast apps)
    - /feeds/<slug>/episodes/<id>/audio - audio files (for podcast apps)
    """
    path = request.path

    # Check exact path exemptions
    if path in AUTH_EXEMPT_PATHS:
        return None

    # Check prefix exemptions
    for prefix in AUTH_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return None

    # Allow RSS feeds without auth (for podcast apps)
    if '/rss' in path:
        return None

    # Allow audio files without auth (for podcast apps)
    if '/audio' in path:
        return None

    # Allow artwork without auth (img tags don't redirect on 401)
    if '/artwork' in path:
        return None

    # Check if password is set
    db = get_database()
    password_hash = db.get_setting('app_password')
    if not password_hash or password_hash == '':
        return None  # No password set, allow access

    # Check session
    if not session.get('authenticated', False):
        return error_response('Authentication required', 401)

    return None


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


def extract_transcript_segment(transcript: str, start: float, end: float) -> str:
    """Extract text from transcript between timestamps.

    Transcript format (VTT-like):
    [00:00:00.000 --> 00:00:27.580] Text content here...
    [00:00:28.940 --> 00:00:35.120] More text...
    """
    import re

    if not transcript:
        return ''

    def parse_timestamp(ts: str) -> float:
        """Convert HH:MM:SS.mmm to seconds."""
        parts = ts.split(':')
        hours = int(parts[0])
        mins = int(parts[1])
        secs = float(parts[2])
        return hours * 3600 + mins * 60 + secs

    # Pattern: [HH:MM:SS.mmm --> HH:MM:SS.mmm] text
    pattern = r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*([^\[]+)'
    segments = []

    for match in re.finditer(pattern, transcript):
        seg_start = parse_timestamp(match.group(1))
        seg_end = parse_timestamp(match.group(2))
        text = match.group(3).strip()

        # Include segment if it overlaps with requested range
        if seg_end >= start and seg_start <= end:
            segments.append(text)

    return ' '.join(segments)


def extract_sponsor_from_text(ad_text: str) -> str:
    """Extract sponsor name from ad text by looking for URLs and common patterns.

    Looks for:
    - Domain names (e.g., hex.ai, thisisnewjersey.com)
    - Common sponsor phrases (e.g., "brought to you by X", "sponsored by X")
    """
    import re

    if not ad_text:
        return None

    # Look for URLs/domains mentioned in the text
    # Match patterns like: example.com, example.ai, visit example dot com
    domain_pattern = r'(?:visit\s+)?(?:www\.)?([a-zA-Z0-9-]+)\.(?:com|ai|io|org|net|co|gov)(?:/[^\s]*)?'
    domains = re.findall(domain_pattern, ad_text.lower())

    # Filter out common non-sponsor domains
    ignore_domains = {'example', 'website', 'podcast', 'episode', 'click', 'link'}
    domains = [d for d in domains if d not in ignore_domains]

    if domains:
        # Return the first meaningful domain as sponsor
        sponsor = domains[0].replace('-', ' ').title()
        return sponsor

    # Look for "brought to you by X" or "sponsored by X" patterns
    sponsor_patterns = [
        r'brought to you by\s+([A-Z][a-zA-Z0-9\s]+?)(?:\.|,|!|\s+is|\s+where|\s+the)',
        r'sponsored by\s+([A-Z][a-zA-Z0-9\s]+?)(?:\.|,|!|\s+is|\s+where|\s+the)',
        r'thanks to\s+([A-Z][a-zA-Z0-9\s]+?)(?:\s+for|\.|,|!)',
    ]

    for pattern in sponsor_patterns:
        match = re.search(pattern, ad_text, re.IGNORECASE)
        if match:
            sponsor = match.group(1).strip()
            if len(sponsor) < 50:  # Sanity check
                return sponsor

    return None


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
            'lastEpisodeDate': podcast.get('last_episode_date'),
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform')
        })

    return json_response({'feeds': feeds})


@api.route('/feeds', methods=['POST'])
@limiter.limit("10 per minute")
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

        # Invalidate feed cache since we added a new feed
        from main import invalidate_feed_cache
        invalidate_feed_cache()

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


@api.route('/feeds/import-opml', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def import_opml():
    """Import podcast feeds from an OPML file.

    Accepts a multipart form upload with an 'opml' file field.
    Returns counts of successfully imported and failed feeds.
    """
    import xml.etree.ElementTree as ET

    if 'opml' not in request.files:
        return error_response('No OPML file provided', 400)

    opml_file = request.files['opml']
    if not opml_file.filename:
        return error_response('Empty file name', 400)

    # Check file extension
    if not opml_file.filename.lower().endswith(('.opml', '.xml')):
        return error_response('File must be .opml or .xml', 400)

    try:
        content = opml_file.read().decode('utf-8')
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.error(f"OPML parse error: {e}")
        return error_response('Invalid OPML file format', 400)
    except UnicodeDecodeError as e:
        logger.error(f"OPML encoding error: {e}")
        return error_response('File must be UTF-8 encoded', 400)

    # Find all outline elements with xmlUrl (RSS feeds)
    feeds_found = []
    for outline in root.iter('outline'):
        xml_url = outline.get('xmlUrl')
        if xml_url:
            title = outline.get('text') or outline.get('title') or ''
            feeds_found.append({'url': xml_url, 'title': title})

    if not feeds_found:
        return error_response('No RSS feeds found in OPML file', 400)

    # Import feeds
    db = get_database()
    from slugify import slugify as make_slug
    from rss_parser import RSSParser

    imported = []
    failed = []
    skipped = []

    for feed_info in feeds_found:
        source_url = feed_info['url'].strip()
        title = feed_info['title'].strip()

        # Generate slug
        slug = make_slug(title) if title else None

        # If no title, try to fetch from RSS
        if not slug:
            rss_parser = RSSParser()
            try:
                feed_content = rss_parser.fetch_feed(source_url)
                if feed_content:
                    parsed_feed = rss_parser.parse_feed(feed_content)
                    if parsed_feed and parsed_feed.feed:
                        fetched_title = parsed_feed.feed.get('title', '')
                        if fetched_title:
                            slug = make_slug(fetched_title)
            except Exception:
                pass

        # Fallback to URL-based slug
        if not slug:
            from urllib.parse import urlparse
            parsed = urlparse(source_url)
            slug_base = parsed.path.strip('/').split('/')[-1] or parsed.netloc
            slug_base = slug_base.replace('.xml', '').replace('.rss', '')
            slug = make_slug(slug_base) if slug_base else None

        if not slug:
            failed.append({'url': source_url, 'error': 'Could not generate slug'})
            continue

        # Check if slug already exists
        existing = db.get_podcast_by_slug(slug)
        if existing:
            skipped.append({'url': source_url, 'slug': slug, 'reason': 'Already exists'})
            continue

        # Create podcast
        try:
            db.create_podcast(slug, source_url, title or None)
            imported.append({'url': source_url, 'slug': slug})
            logger.info(f"OPML import: Created feed {slug}")
        except Exception as e:
            failed.append({'url': source_url, 'error': str(e)})
            logger.error(f"OPML import failed for {source_url}: {e}")

    # Invalidate feed cache
    if imported:
        from main import invalidate_feed_cache
        invalidate_feed_cache()

        # Trigger refresh for imported feeds
        try:
            from main import refresh_rss_feed
            for feed in imported[:5]:  # Limit to first 5 to avoid overload
                podcast = db.get_podcast_by_slug(feed['slug'])
                if podcast:
                    refresh_rss_feed(feed['slug'], podcast['source_url'])
        except Exception as e:
            logger.warning(f"OPML import: Failed to trigger refreshes: {e}")

    logger.info(
        f"OPML import complete: {len(imported)} imported, "
        f"{len(skipped)} skipped, {len(failed)} failed"
    )

    return json_response({
        'imported': len(imported),
        'skipped': len(skipped),
        'failed': len(failed),
        'feeds': {
            'imported': imported,
            'skipped': skipped,
            'failed': failed
        }
    }, 201 if imported else 200)


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

    # Convert audio_analysis_override from string to boolean/null
    audio_override_value = podcast.get('audio_analysis_override')
    audio_override_result = None
    if audio_override_value == 'true':
        audio_override_result = True
    elif audio_override_value == 'false':
        audio_override_result = False

    # Convert auto_process_override from string to boolean/null
    auto_process_override_value = podcast.get('auto_process_override')
    auto_process_override_result = None
    if auto_process_override_value == 'true':
        auto_process_override_result = True
    elif auto_process_override_value == 'false':
        auto_process_override_result = False

    return json_response({
        'slug': podcast['slug'],
        'title': podcast['title'] or podcast['slug'],
        'sourceUrl': podcast['source_url'],
        'feedUrl': feed_url,
        'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
        'episodeCount': podcast.get('episode_count', 0),
        'processedCount': podcast.get('processed_count', 0),
        'lastRefreshed': podcast.get('last_checked_at'),
        'createdAt': podcast.get('created_at'),
        'networkId': podcast.get('network_id'),
        'daiPlatform': podcast.get('dai_platform'),
        'networkIdOverride': podcast.get('network_id_override'),
        'audioAnalysisOverride': audio_override_result,
        'autoProcessOverride': auto_process_override_result,
        'skipSecondPass': bool(podcast.get('skip_second_pass', 0))
    })


@api.route('/feeds/<slug>', methods=['PATCH'])
@log_request
def update_feed(slug):
    """Update podcast feed settings (network, DAI platform, etc.)."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    # Map API field names to database field names
    field_map = {
        'networkId': 'network_id',
        'daiPlatform': 'dai_platform',
        'networkIdOverride': 'network_id_override',
        'title': 'title',
        'description': 'description'
    }

    updates = {}
    for api_field, db_field in field_map.items():
        if api_field in data:
            updates[db_field] = data[api_field]

    # Handle audio analysis override specially (can be null, true, or false)
    if 'audioAnalysisOverride' in data:
        override_value = data['audioAnalysisOverride']
        if override_value is None:
            updates['audio_analysis_override'] = None
        elif override_value is True:
            updates['audio_analysis_override'] = 'true'
        elif override_value is False:
            updates['audio_analysis_override'] = 'false'

    # Handle auto-process override specially (can be null, true, or false)
    if 'autoProcessOverride' in data:
        override_value = data['autoProcessOverride']
        if override_value is None:
            updates['auto_process_override'] = None
        elif override_value is True:
            updates['auto_process_override'] = 'true'
        elif override_value is False:
            updates['auto_process_override'] = 'false'

    # Handle skip second pass (boolean stored as integer)
    if 'skipSecondPass' in data:
        updates['skip_second_pass'] = 1 if data['skipSecondPass'] else 0

    if not updates:
        return error_response('No valid fields to update', 400)

    try:
        db.update_podcast(slug, **updates)
        logger.info(f"Updated feed {slug}: {updates}")

        # Invalidate feed cache since we modified a feed
        from main import invalidate_feed_cache
        invalidate_feed_cache()

        # Return updated feed data
        podcast = db.get_podcast_by_slug(slug)
        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')

        # Convert audio_analysis_override from string to boolean/null
        audio_override_value = podcast.get('audio_analysis_override')
        audio_override_result = None
        if audio_override_value == 'true':
            audio_override_result = True
        elif audio_override_value == 'false':
            audio_override_result = False

        return json_response({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform'),
            'networkIdOverride': podcast.get('network_id_override'),
            'audioAnalysisOverride': audio_override_result,
            'skipSecondPass': bool(podcast.get('skip_second_pass', 0)),
            'feedUrl': f"{base_url}/{slug}"
        })
    except Exception as e:
        logger.error(f"Failed to update feed {slug}: {e}")
        return error_response(f'Failed to update feed: {str(e)}', 500)


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

        # Invalidate feed cache since we deleted a feed
        from main import invalidate_feed_cache
        invalidate_feed_cache()

        # Delete files
        storage.cleanup_podcast_dir(slug)

        logger.info(f"Deleted feed: {slug}")
        return json_response({'message': 'Feed deleted', 'slug': slug})

    except Exception as e:
        logger.error(f"Failed to delete feed {slug}: {e}")
        return error_response(f'Failed to delete feed: {str(e)}', 500)


@api.route('/feeds/<slug>/refresh', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def refresh_feed(slug):
    """Refresh a single podcast feed.

    Optional request body:
    {
        "force": true  // Force full refresh, bypassing conditional GET (304)
    }
    """
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    if not podcast.get('source_url'):
        return error_response('Feed has no source URL', 400)

    # Check for force parameter
    force = False
    data = request.get_json(silent=True)
    if data and data.get('force'):
        force = True
        # Clear ETag to force non-conditional fetch
        db.update_podcast_etag(slug, None, None)
        logger.info(f"Force refresh requested for {slug}, cleared ETag")

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
@limiter.limit("2 per minute")
@log_request
def refresh_all_feeds():
    """Refresh all podcast feeds.

    Optional request body:
    {
        "force": true  // Force full refresh for all feeds, bypassing conditional GET (304)
    }
    """
    try:
        db = get_database()

        # Check for force parameter
        data = request.get_json(silent=True)
        if data and data.get('force'):
            # Clear all ETags to force non-conditional fetch
            podcasts = db.get_all_podcasts()
            for podcast in podcasts:
                db.update_podcast_etag(podcast['slug'], None, None)
            logger.info(f"Force refresh requested, cleared ETags for {len(podcasts)} feeds")

        from main import refresh_all_feeds as do_refresh
        do_refresh()

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
        'adsRemovedSecondPass': episode.get('ads_removed_secondpass', 0),
        'timeSaved': time_saved,
        'fileSize': file_size,
        'adMarkers': ad_markers,
        'rejectedAdMarkers': rejected_ad_markers,
        'corrections': corrections,
        'adDetectionStatus': episode.get('ad_detection_status'),
        'transcript': episode.get('transcript_text'),
        'transcriptAvailable': bool(episode.get('transcript_text')),
        'transcriptVttAvailable': transcript_vtt_available,
        'transcriptVttUrl': f"/episodes/{slug}/{episode_id}.vtt" if transcript_vtt_available else None,
        'chaptersAvailable': chapters_available,
        'chaptersUrl': f"/episodes/{slug}/{episode_id}/chapters.json" if chapters_available else None,
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
@limiter.limit("5 per minute")
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

        # Fetch episode description and published date from RSS if available
        episode_description = None
        episode_published_at = None
        if podcast and podcast.get('source_url'):
            try:
                rss_parser = RSSParser()
                feed_content = rss_parser.fetch_feed(podcast['source_url'])
                if feed_content:
                    episodes = rss_parser.extract_episodes(feed_content)
                    for ep in episodes:
                        if ep['id'] == episode_id:
                            episode_description = ep.get('description')
                            # Get published date from RSS and convert to ISO format
                            published_str = ep.get('published', '')
                            if published_str:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    parsed_pub = parsedate_to_datetime(published_str)
                                    episode_published_at = parsed_pub.strftime('%Y-%m-%dT%H:%M:%SZ')
                                except (ValueError, TypeError):
                                    pass
                            break
            except Exception as e:
                logger.warning(f"Could not fetch episode metadata: {e}")

        logger.info(f"Starting reprocess: {slug}:{episode_id}")
        success = process_episode(slug, episode_id, episode_url, episode_title, podcast_name, episode_description, None, episode_published_at)

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

        chapters_gen = ChaptersGenerator()

        # VTT segments are ALREADY adjusted (ads removed), so pass empty ads_removed
        # This prevents double-adjustment of timestamps
        # The AI topic detection will find natural chapter points in the content
        chapters = chapters_gen.generate_chapters_from_vtt(
            segments, episode_description, podcast_name, episode_title
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
    import re
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

            # If full mode, clear existing ad data (fresh slate for Claude)
            if mode == 'full':
                try:
                    storage.delete_ads_json(slug, episode_id)
                    logger.info(f"[{slug}:{episode_id}] Cleared existing ad data for full reprocess")
                except Exception as e:
                    logger.warning(f"[{slug}:{episode_id}] Could not clear ad data: {e}")

            # Reset status to pending with reprocess mode for priority queue
            db.upsert_episode(
                slug, episode_id,
                status='pending',
                reprocess_mode=mode,
                reprocess_requested_at=datetime.utcnow().isoformat() + 'Z',
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


# ========== Processing History Endpoints ==========

@api.route('/history', methods=['GET'])
@log_request
def get_processing_history():
    """Get processing history with pagination and filtering."""
    db = get_database()

    # Parse query params
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status_filter = request.args.get('status')  # 'completed' or 'failed'
    podcast_slug = request.args.get('podcast')
    sort_by = request.args.get('sort_by', 'processed_at')
    sort_dir = request.args.get('sort_dir', 'desc')

    # Clamp limits
    limit = min(max(1, limit), 100)
    offset = max(0, offset)

    entries, total_count = db.get_processing_history(
        limit=limit,
        offset=offset,
        status_filter=status_filter,
        podcast_slug=podcast_slug,
        sort_by=sort_by,
        sort_dir=sort_dir
    )

    # Transform for API response
    history = []
    for entry in entries:
        history.append({
            'id': entry['id'],
            'podcastSlug': entry['podcast_slug'],
            'podcastTitle': entry['podcast_title'],
            'episodeId': entry['episode_id'],
            'episodeTitle': entry['episode_title'],
            'processedAt': entry['processed_at'],
            'processingDurationSeconds': entry['processing_duration_seconds'],
            'status': entry['status'],
            'adsDetected': entry['ads_detected'],
            'errorMessage': entry['error_message'],
            'reprocessNumber': entry['reprocess_number']
        })

    import math
    return json_response({
        'history': history,
        'total': total_count,
        'totalPages': math.ceil(total_count / limit) if total_count > 0 else 1,
        'limit': limit,
        'offset': offset
    })


@api.route('/history/stats', methods=['GET'])
@log_request
def get_processing_history_stats():
    """Get aggregate statistics from processing history."""
    db = get_database()
    stats = db.get_processing_history_stats()

    return json_response({
        'totalProcessed': stats['total_processed'],
        'completedCount': stats['completed_count'],
        'failedCount': stats['failed_count'],
        'avgProcessingTimeSeconds': stats['avg_processing_time_seconds'],
        'totalAdsDetected': stats['total_ads_detected'],
        'reprocessCount': stats['reprocess_count'],
        'uniqueEpisodes': stats['unique_episodes']
    })


@api.route('/history/export', methods=['GET'])
@log_request
def export_processing_history():
    """Export processing history as CSV or JSON."""
    import csv
    import io

    db = get_database()

    # Parse query params
    export_format = request.args.get('format', 'json').lower()
    status_filter = request.args.get('status')
    podcast_slug = request.args.get('podcast')

    entries = db.export_processing_history(
        status_filter=status_filter,
        podcast_slug=podcast_slug
    )

    if export_format == 'csv':
        # Generate CSV
        output = io.StringIO()
        if entries:
            fieldnames = ['id', 'podcast_slug', 'podcast_title', 'episode_id',
                         'episode_title', 'processed_at', 'processing_duration_seconds',
                         'status', 'ads_detected', 'error_message', 'reprocess_number']
            writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for entry in entries:
                writer.writerow(entry)

        response = Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=processing_history.csv'}
        )
        return response
    else:
        # JSON format
        history = []
        for entry in entries:
            history.append({
                'id': entry['id'],
                'podcastSlug': entry['podcast_slug'],
                'podcastTitle': entry['podcast_title'],
                'episodeId': entry['episode_id'],
                'episodeTitle': entry['episode_title'],
                'processedAt': entry['processed_at'],
                'processingDurationSeconds': entry['processing_duration_seconds'],
                'status': entry['status'],
                'adsDetected': entry['ads_detected'],
                'errorMessage': entry['error_message'],
                'reprocessNumber': entry['reprocess_number']
            })

        response = Response(
            json.dumps({'history': history}, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=processing_history.json'}
        )
        return response


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

    # Get auto-process setting (defaults to true)
    auto_process_value = settings.get('auto_process_enabled', {}).get('value', 'true')
    auto_process_enabled = auto_process_value.lower() in ('true', '1', 'yes')

    # Get Podcasting 2.0 settings (defaults to true)
    vtt_value = settings.get('vtt_transcripts_enabled', {}).get('value', 'true')
    vtt_enabled = vtt_value.lower() in ('true', '1', 'yes')
    chapters_value = settings.get('chapters_enabled', {}).get('value', 'true')
    chapters_enabled = chapters_value.lower() in ('true', '1', 'yes')

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
        'autoProcessEnabled': {
            'value': auto_process_enabled,
            'isDefault': settings.get('auto_process_enabled', {}).get('is_default', True)
        },
        'vttTranscriptsEnabled': {
            'value': vtt_enabled,
            'isDefault': settings.get('vtt_transcripts_enabled', {}).get('is_default', True)
        },
        'chaptersEnabled': {
            'value': chapters_enabled,
            'isDefault': settings.get('chapters_enabled', {}).get('is_default', True)
        },
        'retentionPeriodMinutes': int(os.environ.get('RETENTION_PERIOD') or settings.get('retention_period_minutes', {}).get('value', '1440')),
        'defaults': {
            'systemPrompt': DEFAULT_SYSTEM_PROMPT,
            'secondPassPrompt': DEFAULT_SECOND_PASS_PROMPT,
            'claudeModel': DEFAULT_MODEL,
            'secondPassModel': DEFAULT_MODEL,
            'multiPassEnabled': False,
            'whisperModel': default_whisper_model,
            'audioAnalysisEnabled': False,
            'autoProcessEnabled': True,
            'vttTranscriptsEnabled': True,
            'chaptersEnabled': True
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

    if 'autoProcessEnabled' in data:
        value = 'true' if data['autoProcessEnabled'] else 'false'
        db.set_setting('auto_process_enabled', value, is_default=False)
        logger.info(f"Updated auto-process to: {value}")

    if 'vttTranscriptsEnabled' in data:
        value = 'true' if data['vttTranscriptsEnabled'] else 'false'
        db.set_setting('vtt_transcripts_enabled', value, is_default=False)
        logger.info(f"Updated VTT transcripts to: {value}")

    if 'chaptersEnabled' in data:
        value = 'true' if data['chaptersEnabled'] else 'false'
        db.set_setting('chapters_enabled', value, is_default=False)
        logger.info(f"Updated chapters generation to: {value}")

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
    db.reset_setting('vtt_transcripts_enabled')
    db.reset_setting('chapters_enabled')

    # Mark whisper model for reload
    try:
        from transcriber import WhisperModelSingleton
        WhisperModelSingleton.mark_for_reload()
    except Exception as e:
        logger.warning(f"Could not mark model for reload: {e}")

    logger.info("Reset all settings to defaults")
    return json_response({'message': 'Settings reset to defaults'})


@api.route('/settings/prompts/reset', methods=['POST'])
@log_request
def reset_prompts_only():
    """Reset only the prompts to defaults (not models or other settings)."""
    db = get_database()

    db.reset_setting('system_prompt')
    db.reset_setting('second_pass_prompt')

    logger.info("Reset prompts to defaults")
    return json_response({'message': 'Prompts reset to defaults'})


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


@api.route('/networks', methods=['GET'])
@log_request
def list_networks():
    """List all known podcast networks for network override selection."""
    from pattern_service import KNOWN_NETWORKS

    networks = [
        {'id': network_id, 'name': network_id.replace('_', ' ').title()}
        for network_id in KNOWN_NETWORKS.keys()
    ]

    return json_response({
        'networks': sorted(networks, key=lambda x: x['name'])
    })


# ========== System Endpoints ==========

@api.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring.

    Returns 200 if healthy, 503 if unhealthy.
    Does not require authentication.
    """
    db = get_database()
    storage = get_storage()

    checks = {}

    # Database check
    try:
        conn = db.get_connection()
        conn.execute('SELECT 1')
        checks['database'] = True
    except Exception:
        checks['database'] = False

    # Storage check - verify data directory is writable
    try:
        storage_path = storage.data_dir
        checks['storage'] = os.access(storage_path, os.W_OK)
    except Exception:
        checks['storage'] = False

    # Processing queue check
    try:
        from processing_queue import ProcessingQueue
        queue = ProcessingQueue()
        checks['queue_available'] = not queue.is_busy()
    except Exception:
        checks['queue_available'] = False

    # Determine overall status - database and storage are critical
    critical_checks = [checks['database'], checks['storage']]
    status = 'healthy' if all(critical_checks) else 'unhealthy'

    response_data = {
        'status': status,
        'checks': checks,
        'version': _get_version()
    }

    return jsonify(response_data), 200 if status == 'healthy' else 503


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


@api.route('/system/queue', methods=['GET'])
@log_request
def get_queue_status():
    """Get auto-process queue status."""
    db = get_database()
    queue_stats = db.get_queue_status()

    return json_response({
        'pending': queue_stats.get('pending', 0),
        'processing': queue_stats.get('processing', 0),
        'completed': queue_stats.get('completed', 0),
        'failed': queue_stats.get('failed', 0),
        'total': queue_stats.get('total', 0)
    })


@api.route('/system/queue', methods=['DELETE'])
@log_request
def clear_queue():
    """Clear all pending items from the auto-process queue."""
    db = get_database()
    deleted = db.clear_pending_queue_items()
    logger.info(f"Cleared {deleted} pending items from auto-process queue")
    return json_response({
        'message': f'Cleared {deleted} pending items from queue',
        'deleted': deleted
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


def get_sponsor_service():
    """Get sponsor service instance."""
    from sponsor_service import SponsorService
    return SponsorService(get_database())


# ========== Sponsor Endpoints ==========

@api.route('/sponsors', methods=['GET'])
@log_request
def list_sponsors():
    """List all known sponsors."""
    service = get_sponsor_service()
    include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'

    sponsors = service.db.get_known_sponsors(active_only=not include_inactive)

    # Parse JSON fields
    import json
    result = []
    for s in sponsors:
        sponsor_data = dict(s)
        # Parse aliases from JSON string
        if isinstance(sponsor_data.get('aliases'), str):
            try:
                sponsor_data['aliases'] = json.loads(sponsor_data['aliases'])
            except json.JSONDecodeError:
                sponsor_data['aliases'] = []
        # Parse common_ctas from JSON string
        if isinstance(sponsor_data.get('common_ctas'), str):
            try:
                sponsor_data['common_ctas'] = json.loads(sponsor_data['common_ctas'])
            except json.JSONDecodeError:
                sponsor_data['common_ctas'] = []
        result.append(sponsor_data)

    return json_response({'sponsors': result})


@api.route('/sponsors', methods=['POST'])
@log_request
def add_sponsor():
    """Add a new sponsor."""
    data = request.get_json()
    if not data or not data.get('name'):
        return error_response('Name is required', 400)

    service = get_sponsor_service()

    # Check if sponsor already exists
    existing = service.db.get_known_sponsor_by_name(data['name'])
    if existing:
        return error_response(f"Sponsor '{data['name']}' already exists", 409)

    sponsor_id = service.add_sponsor(
        name=data['name'],
        aliases=data.get('aliases', []),
        category=data.get('category')
    )

    return json_response({
        'message': 'Sponsor created',
        'id': sponsor_id
    }, 201)


@api.route('/sponsors/<int:sponsor_id>', methods=['GET'])
@log_request
def get_sponsor(sponsor_id):
    """Get a single sponsor by ID."""
    db = get_database()
    sponsor = db.get_known_sponsor_by_id(sponsor_id)

    if not sponsor:
        return error_response('Sponsor not found', 404)

    import json
    sponsor_data = dict(sponsor)
    if isinstance(sponsor_data.get('aliases'), str):
        try:
            sponsor_data['aliases'] = json.loads(sponsor_data['aliases'])
        except json.JSONDecodeError:
            sponsor_data['aliases'] = []
    if isinstance(sponsor_data.get('common_ctas'), str):
        try:
            sponsor_data['common_ctas'] = json.loads(sponsor_data['common_ctas'])
        except json.JSONDecodeError:
            sponsor_data['common_ctas'] = []

    return json_response(sponsor_data)


@api.route('/sponsors/<int:sponsor_id>', methods=['PUT'])
@log_request
def update_sponsor(sponsor_id):
    """Update a sponsor."""
    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    service = get_sponsor_service()

    # Check sponsor exists
    existing = service.db.get_known_sponsor_by_id(sponsor_id)
    if not existing:
        return error_response('Sponsor not found', 404)

    success = service.update_sponsor(sponsor_id, **data)

    if success:
        return json_response({'message': 'Sponsor updated'})
    return error_response('No valid fields to update', 400)


@api.route('/sponsors/<int:sponsor_id>', methods=['DELETE'])
@log_request
def delete_sponsor(sponsor_id):
    """Delete (deactivate) a sponsor."""
    service = get_sponsor_service()

    success = service.delete_sponsor(sponsor_id)

    if success:
        return json_response({'message': 'Sponsor deleted'})
    return error_response('Sponsor not found', 404)


# ========== Normalization Endpoints ==========

@api.route('/sponsors/normalizations', methods=['GET'])
@log_request
def list_normalizations():
    """List all sponsor normalizations."""
    service = get_sponsor_service()
    category = request.args.get('category')
    include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'

    normalizations = service.db.get_sponsor_normalizations(
        category=category,
        active_only=not include_inactive
    )

    return json_response({'normalizations': normalizations})


@api.route('/sponsors/normalizations', methods=['POST'])
@log_request
def add_normalization():
    """Add a new normalization."""
    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    required = ['pattern', 'replacement', 'category']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return error_response(f"Missing required fields: {', '.join(missing)}", 400)

    if data['category'] not in ('sponsor', 'url', 'number', 'phrase'):
        return error_response("Category must be one of: sponsor, url, number, phrase", 400)

    # Validate regex pattern
    import re
    try:
        re.compile(data['pattern'])
    except re.error as e:
        return error_response(f"Invalid regex pattern: {e}", 400)

    service = get_sponsor_service()

    norm_id = service.add_normalization(
        pattern=data['pattern'],
        replacement=data['replacement'],
        category=data['category']
    )

    return json_response({
        'message': 'Normalization created',
        'id': norm_id
    }, 201)


@api.route('/sponsors/normalizations/<int:norm_id>', methods=['PUT'])
@log_request
def update_normalization(norm_id):
    """Update a normalization."""
    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    # Validate regex pattern if provided
    if 'pattern' in data:
        import re
        try:
            re.compile(data['pattern'])
        except re.error as e:
            return error_response(f"Invalid regex pattern: {e}", 400)

    if 'category' in data and data['category'] not in ('sponsor', 'url', 'number', 'phrase'):
        return error_response("Category must be one of: sponsor, url, number, phrase", 400)

    service = get_sponsor_service()
    success = service.update_normalization(norm_id, **data)

    if success:
        return json_response({'message': 'Normalization updated'})
    return error_response('Normalization not found or no valid fields', 404)


@api.route('/sponsors/normalizations/<int:norm_id>', methods=['DELETE'])
@log_request
def delete_normalization(norm_id):
    """Delete (deactivate) a normalization."""
    service = get_sponsor_service()

    success = service.delete_normalization(norm_id)

    if success:
        return json_response({'message': 'Normalization deleted'})
    return error_response('Normalization not found', 404)


# ========== Status Stream Endpoint (SSE) ==========

def get_status_service():
    """Get status service instance."""
    from status_service import StatusService
    return StatusService()


@api.route('/status/stream', methods=['GET'])
def status_stream():
    """
    Server-Sent Events stream for real-time processing status updates.

    Returns a continuous event stream with status updates whenever
    processing state changes.
    """
    import json
    import queue

    def generate():
        status_service = get_status_service()
        update_queue = queue.Queue()

        # Subscribe to status updates
        def on_update(status):
            try:
                update_queue.put_nowait(status_service.to_dict())
            except queue.Full:
                pass  # Drop update if queue is full

        unsubscribe = status_service.subscribe(on_update)

        try:
            # Send initial status immediately
            yield f"data: {json.dumps(status_service.to_dict())}\n\n"

            # Stream updates as they occur
            while True:
                try:
                    # Wait for update with timeout (for keepalive)
                    status = update_queue.get(timeout=15)
                    yield f"data: {json.dumps(status)}\n\n"
                except queue.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            unsubscribe()

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering
        }
    )


@api.route('/status', methods=['GET'])
@log_request
def get_status():
    """Get current processing status (one-time fetch, not streaming)."""
    status_service = get_status_service()
    return json_response(status_service.to_dict())


# ========== Pattern & Correction Endpoints ==========

@api.route('/patterns', methods=['GET'])
@log_request
def list_patterns():
    """List all ad patterns with optional filtering."""
    db = get_database()

    scope = request.args.get('scope')
    podcast_id = request.args.get('podcast_id')
    network_id = request.args.get('network_id')
    active_only = request.args.get('active', 'true').lower() == 'true'

    patterns = db.get_ad_patterns(
        scope=scope,
        podcast_id=podcast_id,
        network_id=network_id,
        active_only=active_only
    )

    return json_response({'patterns': patterns})


@api.route('/patterns/stats', methods=['GET'])
@log_request
def get_pattern_stats():
    """Get pattern statistics for audit purposes."""
    from datetime import datetime, timedelta

    db = get_database()
    patterns = db.get_ad_patterns(active_only=False)

    # Calculate stats
    stats = {
        'total': len(patterns),
        'active': 0,
        'inactive': 0,
        'by_scope': {'global': 0, 'network': 0, 'podcast': 0},
        'no_sponsor': 0,
        'never_matched': 0,
        'stale_count': 0,
        'high_false_positive_count': 0,
        'stale_patterns': [],
        'no_sponsor_patterns': [],
        'high_false_positive_patterns': [],
    }

    stale_threshold = datetime.now() - timedelta(days=30)

    for p in patterns:
        # Active/inactive
        if p.get('is_active', True):
            stats['active'] += 1
        else:
            stats['inactive'] += 1

        # By scope
        scope = p.get('scope', 'podcast')
        if scope in stats['by_scope']:
            stats['by_scope'][scope] += 1

        # No sponsor (Unknown)
        if not p.get('sponsor'):
            stats['no_sponsor'] += 1
            stats['no_sponsor_patterns'].append({
                'id': p['id'],
                'scope': p.get('scope'),
                'podcast_name': p.get('podcast_name'),
                'created_at': p.get('created_at'),
                'text_preview': (p.get('text_template') or '')[:100]
            })

        # Never matched
        if p.get('confirmation_count', 0) == 0:
            stats['never_matched'] += 1

        # Stale (not matched in 30+ days)
        last_matched = p.get('last_matched_at')
        if last_matched:
            try:
                last_date = datetime.fromisoformat(last_matched.replace('Z', '+00:00'))
                if last_date.replace(tzinfo=None) < stale_threshold:
                    stats['stale_count'] += 1
                    stats['stale_patterns'].append({
                        'id': p['id'],
                        'sponsor': p.get('sponsor'),
                        'last_matched_at': last_matched,
                        'confirmation_count': p.get('confirmation_count', 0)
                    })
            except (ValueError, TypeError):
                pass

        # High false positives (more FPs than confirmations)
        fp_count = p.get('false_positive_count', 0)
        conf_count = p.get('confirmation_count', 0)
        if fp_count > 0 and fp_count >= conf_count:
            stats['high_false_positive_count'] += 1
            stats['high_false_positive_patterns'].append({
                'id': p['id'],
                'sponsor': p.get('sponsor'),
                'confirmation_count': conf_count,
                'false_positive_count': fp_count
            })

    # Limit list sizes for response
    stats['stale_patterns'] = stats['stale_patterns'][:20]
    stats['no_sponsor_patterns'] = stats['no_sponsor_patterns'][:20]
    stats['high_false_positive_patterns'] = stats['high_false_positive_patterns'][:20]

    return json_response(stats)


@api.route('/patterns/<int:pattern_id>', methods=['GET'])
@log_request
def get_pattern(pattern_id):
    """Get a single pattern by ID."""
    db = get_database()

    pattern = db.get_ad_pattern_by_id(pattern_id)

    if not pattern:
        return error_response('Pattern not found', 404)

    return json_response(pattern)


@api.route('/patterns/<int:pattern_id>', methods=['PUT'])
@log_request
def update_pattern(pattern_id):
    """Update a pattern."""
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('Pattern not found', 404)

    # Allowed fields
    allowed = {'text_template', 'sponsor', 'intro_variants', 'outro_variants',
               'is_active', 'disabled_reason', 'scope'}

    updates = {k: v for k, v in data.items() if k in allowed}

    if updates:
        db.update_ad_pattern(pattern_id, **updates)
        return json_response({'message': 'Pattern updated'})

    return error_response('No valid fields provided', 400)


@api.route('/patterns/<int:pattern_id>', methods=['DELETE'])
@log_request
def delete_pattern(pattern_id):
    """Delete a pattern."""
    db = get_database()

    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('Pattern not found', 404)

    db.delete_ad_pattern(pattern_id)
    return json_response({'message': 'Pattern deleted'})


@api.route('/patterns/deduplicate', methods=['POST'])
@log_request
def deduplicate_patterns():
    """Manually trigger pattern deduplication."""
    db = get_database()

    try:
        removed = db.deduplicate_patterns()
        return json_response({
            'message': f'Removed {removed} duplicate patterns',
            'removed_count': removed
        })
    except Exception as e:
        logger.error(f"Deduplication failed: {e}")
        return error_response(f'Deduplication failed: {str(e)}', 500)


@api.route('/patterns/merge', methods=['POST'])
@log_request
def merge_patterns():
    """Merge multiple patterns into one.

    Request body:
    {
        "keep_id": 123,  // Pattern to keep
        "merge_ids": [124, 125, ...]  // Patterns to merge into keep_id
    }
    """
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    keep_id = data.get('keep_id')
    merge_ids = data.get('merge_ids', [])

    if not keep_id or not merge_ids:
        return error_response('Missing keep_id or merge_ids', 400)

    # Validate patterns exist
    keep_pattern = db.get_ad_pattern_by_id(keep_id)
    if not keep_pattern:
        return error_response(f'Pattern {keep_id} not found', 404)

    for merge_id in merge_ids:
        if merge_id == keep_id:
            continue
        pattern = db.get_ad_pattern_by_id(merge_id)
        if not pattern:
            return error_response(f'Pattern {merge_id} not found', 404)

    try:
        conn = db.get_connection()

        # Sum up confirmation and false positive counts
        total_confirmations = keep_pattern.get('confirmation_count', 0)
        total_false_positives = keep_pattern.get('false_positive_count', 0)

        for merge_id in merge_ids:
            if merge_id == keep_id:
                continue
            pattern = db.get_ad_pattern_by_id(merge_id)
            total_confirmations += pattern.get('confirmation_count', 0)
            total_false_positives += pattern.get('false_positive_count', 0)

        # Update the kept pattern with merged stats
        db.update_ad_pattern(keep_id,
            confirmation_count=total_confirmations,
            false_positive_count=total_false_positives
        )

        # Move corrections to kept pattern
        placeholders = ','.join('?' * len(merge_ids))
        conn.execute(
            f'''UPDATE pattern_corrections
                SET pattern_id = ?
                WHERE pattern_id IN ({placeholders})''',
            [keep_id] + merge_ids
        )

        # Delete merged patterns
        conn.execute(
            f'''DELETE FROM ad_patterns WHERE id IN ({placeholders})''',
            merge_ids
        )
        conn.commit()

        return json_response({
            'message': f'Merged {len(merge_ids)} patterns into pattern {keep_id}',
            'kept_pattern_id': keep_id,
            'merged_count': len(merge_ids),
            'total_confirmations': total_confirmations,
            'total_false_positives': total_false_positives
        })
    except Exception as e:
        logger.error(f"Pattern merge failed: {e}")
        return error_response(f'Merge failed: {str(e)}', 500)


@api.route('/episodes/<slug>/<episode_id>/corrections', methods=['POST'])
@log_request
def submit_correction(slug, episode_id):
    """Submit a correction for a detected ad.

    Correction types:
    - confirm: Ad detection is correct (increases confirmation_count)
    - reject: Not actually an ad (increases false_positive_count)
    - adjust: Correct ad but with adjusted boundaries
    """
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    correction_type = data.get('type')
    if correction_type not in ('confirm', 'reject', 'adjust'):
        return error_response('Invalid correction type', 400)

    original_ad = data.get('original_ad', {})
    original_start = original_ad.get('start')
    original_end = original_ad.get('end')
    pattern_id = original_ad.get('pattern_id')

    if original_start is None or original_end is None:
        return error_response('Missing original ad boundaries', 400)

    # Get pattern service for recording corrections
    from pattern_service import PatternService
    pattern_service = PatternService(db)

    if correction_type == 'confirm':
        logger.info(f"CORRECTION: type=confirm, episode={slug}/{episode_id}, pattern_id={pattern_id}, start={original_start}, end={original_end}")

        # Increment confirmation count on pattern
        if pattern_id:
            pattern_service.record_pattern_match(pattern_id, episode_id)
        else:
            # Create new pattern from Claude detection
            episode = db.get_episode(slug, episode_id)
            if episode:
                transcript = episode.get('transcript_text', '')

                # Extract ad text from transcript using timestamps
                ad_text = extract_transcript_segment(transcript, original_start, original_end)

                if ad_text and len(ad_text) >= 50:  # Minimum for TF-IDF matching
                    # Get podcast info for scope
                    podcast = db.get_podcast_by_slug(slug)
                    podcast_id_str = str(podcast['id']) if podcast else None

                    # Check for existing pattern with same text (deduplication)
                    existing_pattern = db.find_pattern_by_text(ad_text, podcast_id_str)

                    if existing_pattern:
                        # Use existing pattern instead of creating duplicate
                        pattern_id = existing_pattern['id']
                        pattern_service.record_pattern_match(pattern_id, episode_id)
                        logger.info(f"Linked to existing pattern {pattern_id} for confirmed ad in {slug}/{episode_id}")
                    else:
                        # Extract sponsor from original ad or from ad text
                        sponsor = original_ad.get('sponsor')
                        if not sponsor:
                            sponsor = extract_sponsor_from_text(ad_text)

                        # Only create pattern if sponsor is known
                        if sponsor:
                            new_pattern_id = db.create_ad_pattern(
                                scope='podcast',
                                podcast_id=podcast_id_str,
                                text_template=ad_text,
                                sponsor=sponsor,
                                intro_variants=[ad_text[:200]] if len(ad_text) > 200 else [ad_text],
                                outro_variants=[ad_text[-150:]] if len(ad_text) > 150 else [],
                                created_from_episode_id=episode_id
                            )
                            pattern_id = new_pattern_id
                            logger.info(f"Created new pattern {pattern_id} (sponsor: {sponsor}) from confirmed ad in {slug}/{episode_id}")
                        else:
                            # Skip pattern creation - no sponsor detected
                            logger.info(f"Skipped pattern creation (no sponsor detected) for confirmed ad in {slug}/{episode_id}")

        db.create_pattern_correction(
            correction_type='confirm',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            text_snippet=data.get('notes')
        )

        return json_response({'message': 'Correction recorded', 'pattern_id': pattern_id})

    elif correction_type == 'reject':
        logger.info(f"CORRECTION: type=reject, episode={slug}/{episode_id}, pattern_id={pattern_id}, start={original_start}, end={original_end}")

        # Extract transcript text for cross-episode matching
        rejected_text = None
        episode = db.get_episode(slug, episode_id)
        if episode:
            transcript = episode.get('transcript_text', '')
            if transcript:
                rejected_text = extract_transcript_segment(transcript, original_start, original_end)
                if rejected_text:
                    logger.debug(f"Extracted {len(rejected_text)} chars of rejected text for cross-episode matching")

        # Mark as false positive
        if pattern_id:
            pattern = db.get_ad_pattern_by_id(pattern_id)
            if pattern:
                new_count = pattern.get('false_positive_count', 0) + 1
                db.update_ad_pattern(pattern_id, false_positive_count=new_count)
                logger.info(f"Incremented false_positive_count to {new_count} for pattern {pattern_id}")

        db.create_pattern_correction(
            correction_type='false_positive',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            text_snippet=rejected_text  # Store transcript text for cross-episode matching
        )

        return json_response({'message': 'False positive recorded'})

    elif correction_type == 'adjust':
        # Save adjusted boundaries
        adjusted_start = data.get('adjusted_start')
        adjusted_end = data.get('adjusted_end')

        if adjusted_start is None or adjusted_end is None:
            return error_response('Missing adjusted boundaries', 400)

        logger.info(f"CORRECTION: type=adjust, episode={slug}/{episode_id}, pattern_id={pattern_id}, "
                    f"original={original_start:.1f}-{original_end:.1f}, adjusted={adjusted_start:.1f}-{adjusted_end:.1f}")

        # Extract transcript text using ADJUSTED boundaries for pattern learning
        adjusted_text = None
        episode = db.get_episode(slug, episode_id)
        if episode:
            transcript = episode.get('transcript_text', '')
            if transcript:
                adjusted_text = extract_transcript_segment(transcript, adjusted_start, adjusted_end)

        # If we have a pattern, increment confirmation count
        if pattern_id:
            from pattern_service import PatternService
            pattern_service = PatternService(db)
            pattern_service.record_pattern_match(pattern_id, episode_id)
            logger.info(f"Recorded adjustment as confirmation for pattern {pattern_id}")
        elif adjusted_text and len(adjusted_text) >= 50:
            # No pattern exists - create one from adjusted boundaries (like confirm does)
            podcast = db.get_podcast_by_slug(slug)
            podcast_id_str = str(podcast['id']) if podcast else None

            # Check for existing pattern with same text
            existing_pattern = db.find_pattern_by_text(adjusted_text, podcast_id_str)

            if existing_pattern:
                pattern_id = existing_pattern['id']
                from pattern_service import PatternService
                pattern_service = PatternService(db)
                pattern_service.record_pattern_match(pattern_id, episode_id)
                logger.info(f"Linked adjustment to existing pattern {pattern_id}")
            else:
                # Extract sponsor
                sponsor = original_ad.get('sponsor')
                if not sponsor:
                    sponsor = extract_sponsor_from_text(adjusted_text)

                if sponsor:
                    new_pattern_id = db.create_ad_pattern(
                        scope='podcast',
                        podcast_id=podcast_id_str,
                        text_template=adjusted_text,
                        sponsor=sponsor,
                        intro_variants=[adjusted_text[:200]] if len(adjusted_text) > 200 else [adjusted_text],
                        outro_variants=[adjusted_text[-150:]] if len(adjusted_text) > 150 else [],
                        created_from_episode_id=episode_id
                    )
                    pattern_id = new_pattern_id
                    logger.info(f"Created new pattern {pattern_id} (sponsor: {sponsor}) from adjusted ad in {slug}/{episode_id}")
                else:
                    logger.info(f"Skipped pattern creation (no sponsor detected) for adjusted ad in {slug}/{episode_id}")

        # Record the correction with adjusted text for cross-episode learning
        db.create_pattern_correction(
            correction_type='boundary_adjustment',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            corrected_bounds={'start': adjusted_start, 'end': adjusted_end},
            text_snippet=adjusted_text  # Store adjusted text for pattern learning
        )

        return json_response({'message': 'Adjustment recorded', 'pattern_id': pattern_id})


# ========== Episode Reprocessing Endpoint ==========

@api.route('/episodes/<slug>/<episode_id>/reprocess', methods=['POST'])
@log_request
def reprocess_episode_with_mode(slug, episode_id):
    """Reprocess an episode with specified mode.

    Modes:
    - reprocess (default): Use pattern DB + Claude (leverages learned patterns)
    - full: Skip pattern DB entirely, Claude does fresh analysis without learned patterns

    Reprocessed episodes are prioritized in the processing queue.
    """
    db = get_database()

    data = request.get_json() or {}
    mode = data.get('mode', 'reprocess')

    if mode not in ('reprocess', 'full'):
        return error_response('Invalid mode. Use "reprocess" or "full"', 400)

    # Get episode
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    # Get podcast info
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Podcast not found', 404)

    # If full mode, clear existing ad data (fresh slate for Claude)
    if mode == 'full':
        try:
            # Clear any cached ad detection results
            from storage import Storage
            storage = Storage()
            storage.delete_ads_json(slug, episode_id)
            logger.info(f"[{slug}:{episode_id}] Cleared existing ad data for full reprocess")
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] Could not clear ad data: {e}")

    # Reset episode status to pending for reprocessing
    # Store reprocess_mode in the episode so processing can use it
    # Store reprocess_requested_at for priority queue ordering
    # Reset retry_count to allow fresh attempts
    from datetime import datetime
    db.upsert_episode(
        slug, episode_id,
        status='pending',
        reprocess_mode=mode,
        reprocess_requested_at=datetime.utcnow().isoformat() + 'Z',
        retry_count=0,
        error_message=None
    )

    logger.info(f"[{slug}:{episode_id}] Marked for {mode} reprocessing (prioritized)")

    return json_response({
        'message': f'Episode queued for {mode} reprocessing',
        'mode': mode
    })


# ========== Import/Export Endpoints ==========

@api.route('/patterns/export', methods=['GET'])
@log_request
def export_patterns():
    """Export patterns as JSON for backup or sharing.

    Query params:
    - include_disabled: Include disabled patterns (default: false)
    - include_corrections: Include correction history (default: false)
    """
    db = get_database()

    include_disabled = request.args.get('include_disabled', 'false').lower() == 'true'
    include_corrections = request.args.get('include_corrections', 'false').lower() == 'true'

    # Get patterns
    patterns = db.get_ad_patterns(active_only=not include_disabled)

    # Build export data
    export_data = {
        'version': '1.0',
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'pattern_count': len(patterns),
        'patterns': []
    }

    for pattern in patterns:
        pattern_data = {
            'scope': pattern.get('scope'),
            'text_template': pattern.get('text_template'),
            'intro_variants': pattern.get('intro_variants'),
            'outro_variants': pattern.get('outro_variants'),
            'sponsor': pattern.get('sponsor'),
            'confirmation_count': pattern.get('confirmation_count', 0),
            'false_positive_count': pattern.get('false_positive_count', 0),
            'is_active': pattern.get('is_active', True),
            'created_at': pattern.get('created_at'),
        }

        # Include network/podcast IDs for scoped patterns
        if pattern.get('network_id'):
            pattern_data['network_id'] = pattern['network_id']
        if pattern.get('podcast_id'):
            pattern_data['podcast_id'] = pattern['podcast_id']
        if pattern.get('dai_platform'):
            pattern_data['dai_platform'] = pattern['dai_platform']

        # Optionally include corrections
        if include_corrections:
            corrections = db.get_pattern_corrections(pattern_id=pattern['id'])
            if corrections:
                pattern_data['corrections'] = corrections

        export_data['patterns'].append(pattern_data)

    return json_response(export_data)


@api.route('/patterns/import', methods=['POST'])
@log_request
def import_patterns():
    """Import patterns from JSON.

    Body:
    - patterns: Array of pattern objects
    - mode: "merge" (default), "replace", or "supplement"
      - merge: Update existing patterns, add new ones
      - replace: Delete all existing patterns, import all
      - supplement: Only add patterns that don't exist
    """
    db = get_database()

    data = request.get_json()
    if not data or 'patterns' not in data:
        return error_response('No patterns provided', 400)

    patterns = data.get('patterns', [])
    mode = data.get('mode', 'merge')

    if mode not in ('merge', 'replace', 'supplement'):
        return error_response('Invalid mode. Use "merge", "replace", or "supplement"', 400)

    if not patterns:
        return error_response('Empty patterns array', 400)

    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        # Replace mode: delete all existing patterns first
        if mode == 'replace':
            existing = db.get_ad_patterns(active_only=False)
            for p in existing:
                db.delete_ad_pattern(p['id'])
            logger.info(f"Replace mode: deleted {len(existing)} existing patterns")

        for pattern_data in patterns:
            # Validate required fields
            if not pattern_data.get('scope'):
                skipped_count += 1
                continue

            # Check for existing similar pattern
            existing = _find_similar_pattern(db, pattern_data)

            if existing:
                if mode == 'supplement':
                    # Don't update existing patterns
                    skipped_count += 1
                    continue
                elif mode in ('merge', 'replace'):
                    # Update existing pattern
                    updates = {
                        'text_template': pattern_data.get('text_template'),
                        'intro_variants': pattern_data.get('intro_variants'),
                        'outro_variants': pattern_data.get('outro_variants'),
                        'sponsor': pattern_data.get('sponsor'),
                    }
                    updates = {k: v for k, v in updates.items() if v is not None}
                    if updates:
                        db.update_ad_pattern(existing['id'], **updates)
                        updated_count += 1
                    else:
                        skipped_count += 1
                    continue

            # Create new pattern
            db.create_ad_pattern(
                scope=pattern_data.get('scope'),
                text_template=pattern_data.get('text_template'),
                sponsor=pattern_data.get('sponsor'),
                podcast_id=pattern_data.get('podcast_id'),
                network_id=pattern_data.get('network_id'),
                dai_platform=pattern_data.get('dai_platform'),
                intro_variants=pattern_data.get('intro_variants'),
                outro_variants=pattern_data.get('outro_variants')
            )
            imported_count += 1

        logger.info(f"Import complete: {imported_count} imported, {updated_count} updated, {skipped_count} skipped")

        return json_response({
            'message': 'Import complete',
            'imported': imported_count,
            'updated': updated_count,
            'skipped': skipped_count
        })

    except Exception as e:
        logger.error(f"Import failed: {e}")
        return error_response(f'Import failed: {str(e)}', 500)


def _find_similar_pattern(db, pattern_data: dict) -> Optional[dict]:
    """Find an existing pattern similar to the import data."""
    # Look for exact sponsor match in same scope
    sponsor = pattern_data.get('sponsor')
    scope = pattern_data.get('scope')

    if not sponsor:
        return None

    existing = db.get_ad_patterns(scope=scope, active_only=False)
    for p in existing:
        if p.get('sponsor') == sponsor:
            return p

    return None


@api.route('/patterns/backfill-false-positives', methods=['POST'])
@log_request
def backfill_false_positive_texts():
    """Backfill transcript text for existing false positive corrections.

    Populates text_snippet field for corrections that don't have it.
    This enables cross-episode false positive matching.
    """
    db = get_database()
    conn = db.get_connection()

    # Get corrections without text
    cursor = conn.execute('''
        SELECT pc.id, pc.episode_id, pc.original_bounds, p.slug
        FROM pattern_corrections pc
        JOIN episodes e ON pc.episode_id = e.episode_id
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE pc.correction_type = 'false_positive'
        AND (pc.text_snippet IS NULL OR pc.text_snippet = '')
    ''')

    rows = cursor.fetchall()
    logger.info(f"Found {len(rows)} false positive corrections to backfill")

    updated = 0
    skipped = 0
    for row in rows:
        # Get episode transcript
        episode = db.get_episode(row['slug'], row['episode_id'])
        if not episode or not episode.get('transcript_text'):
            skipped += 1
            continue

        bounds_str = row['original_bounds']
        if not bounds_str:
            skipped += 1
            continue

        try:
            bounds = json.loads(bounds_str)
            start, end = bounds.get('start'), bounds.get('end')
            if start is None or end is None:
                skipped += 1
                continue

            # Extract text
            text = extract_transcript_segment(episode['transcript_text'], start, end)
            if text and len(text) >= 50:
                conn.execute(
                    'UPDATE pattern_corrections SET text_snippet = ? WHERE id = ?',
                    (text, row['id'])
                )
                updated += 1
            else:
                skipped += 1
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse bounds for correction {row['id']}: {e}")
            skipped += 1

    conn.commit()
    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped")

    return json_response({
        'message': 'Backfill complete',
        'updated': updated,
        'skipped': skipped
    })


# ========== Authentication Endpoints ==========

@api.route('/auth/status', methods=['GET'])
@log_request
def auth_status():
    """Check authentication status.

    Returns whether password is set and if current session is authenticated.
    This endpoint is always accessible (no auth required).
    """
    db = get_database()
    password_hash = db.get_setting('app_password')
    password_set = password_hash is not None and password_hash != ''

    # If no password is set, everyone is authenticated
    if not password_set:
        authenticated = True
    else:
        authenticated = session.get('authenticated', False)

    return json_response({
        'passwordSet': password_set,
        'authenticated': authenticated
    })


@api.route('/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def auth_login():
    """Login with password.

    Request body:
    {
        "password": "your-password"
    }
    """
    db = get_database()
    stored_hash = db.get_setting('app_password')
    password_set = stored_hash is not None and stored_hash != ''

    if not password_set:
        return json_response({
            'authenticated': True,
            'message': 'No password configured'
        })

    data = request.get_json()
    if not data or 'password' not in data:
        return error_response('Password is required', 400)

    password = data['password']

    if not stored_hash or not check_password_hash(stored_hash, password):
        logger.warning(f"Failed login attempt from {request.remote_addr}")
        return error_response('Invalid password', 401)

    # Set session
    session.permanent = True
    session['authenticated'] = True
    logger.info(f"Successful login from {request.remote_addr}")

    return json_response({
        'authenticated': True,
        'message': 'Login successful'
    })


@api.route('/auth/logout', methods=['POST'])
@log_request
def auth_logout():
    """Logout and clear session."""
    session.clear()
    logger.info(f"Logout from {request.remote_addr}")

    return json_response({
        'authenticated': False,
        'message': 'Logged out successfully'
    })


@api.route('/auth/password', methods=['PUT'])
@limiter.limit("3 per hour")
@log_request
def auth_set_password():
    """Set or change the application password.

    If no password is currently set, this creates a new password.
    If a password is set, the current password must be provided.

    Request body:
    {
        "currentPassword": "old-password",  // Required if password is set
        "newPassword": "new-password"       // Min 8 characters
    }

    To remove password protection, set newPassword to empty string or null.
    """
    data = request.get_json()
    if not data:
        return error_response('Request body required', 400)

    db = get_database()
    current_hash = db.get_setting('app_password')
    password_set = current_hash is not None and current_hash != ''

    # If password is set, verify current password
    if password_set:
        current_password = data.get('currentPassword', '')
        if not check_password_hash(current_hash, current_password):
            logger.warning(f"Failed password change attempt from {request.remote_addr}")
            return error_response('Current password is incorrect', 401)

    new_password = data.get('newPassword', '')

    # Remove password protection if empty
    if not new_password:
        db.set_setting('app_password', '')
        logger.info(f"Password protection removed by {request.remote_addr}")
        return json_response({
            'message': 'Password protection removed',
            'passwordSet': False
        })

    # Validate new password
    if len(new_password) < 8:
        return error_response('Password must be at least 8 characters', 400)

    # Hash and store new password
    password_hash = generate_password_hash(new_password)
    db.set_setting('app_password', password_hash)
    logger.info(f"Password {'changed' if password_set else 'set'} by {request.remote_addr}")

    # Ensure current session is authenticated
    session.permanent = True
    session['authenticated'] = True

    return json_response({
        'message': f"Password {'changed' if password_set else 'set'} successfully",
        'passwordSet': True
    })


# ========== Search Endpoints ==========

@api.route('/search', methods=['GET'])
@log_request
def search():
    """Full-text search across all content.

    Query params:
        q: Search query (required)
        type: Filter by content type (episode, podcast, pattern, sponsor)
        limit: Maximum results (default 50, max 100)

    Returns:
        List of search results with type, id, podcastSlug, title, snippet, score
    """
    query = request.args.get('q', '').strip()
    if not query:
        return error_response('Search query (q) is required', 400)

    content_type = request.args.get('type')
    if content_type and content_type not in ('episode', 'podcast', 'pattern', 'sponsor'):
        return error_response('Invalid type. Use: episode, podcast, pattern, sponsor', 400)

    try:
        limit = min(int(request.args.get('limit', 50)), 100)
    except ValueError:
        limit = 50

    db = get_database()
    results = db.search(query, content_type=content_type, limit=limit)

    return json_response({
        'query': query,
        'results': results,
        'total': len(results)
    })


@api.route('/search/rebuild', methods=['POST'])
@limiter.limit("1 per minute")
@log_request
def rebuild_search_index():
    """Rebuild the full-text search index.

    This reindexes all content (podcasts, episodes, patterns, sponsors).
    May take a few seconds for large databases.
    """
    db = get_database()
    count = db.rebuild_search_index()

    return json_response({
        'message': f'Search index rebuilt with {count} items',
        'indexedCount': count
    })


@api.route('/search/stats', methods=['GET'])
@log_request
def search_stats():
    """Get search index statistics."""
    db = get_database()
    stats = db.get_search_index_stats()

    return json_response({
        'stats': stats
    })

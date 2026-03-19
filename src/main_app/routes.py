"""Flask routes: serve_ui, serve_rss, serve_episode, serve_transcript_vtt, serve_chapters_json, health_check."""
import json
import logging
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests
import requests.exceptions
from flask import Response, send_file, abort, send_from_directory, request

from config import APP_USER_AGENT, JIT_RETRY_COOLDOWN_SECONDS, MAX_EPISODE_RETRIES
from utils.time import parse_iso_datetime

feed_logger = logging.getLogger('podcast.feed')
refresh_logger = logging.getLogger('podcast.refresh')

# Import shared warn-dedup set so routes and processing share one instance
from main_app.shared_state import permanently_failed_warned as _permanently_failed_warned

# Resolved once at registration time
STATIC_DIR = None
ROOT_DIR = None


def log_request_detailed(f):
    """Decorator to log requests with detailed info (IP, user-agent, response time)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        start_time = time.time()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_agent = request.headers.get('User-Agent', 'Unknown')[:100]

        try:
            result = f(*args, **kwargs)
            elapsed = (time.time() - start_time) * 1000  # ms
            status = result.status_code if hasattr(result, 'status_code') else 200
            feed_logger.info(f"{request.method} {request.path} {status} {elapsed:.0f}ms [{client_ip}] [{user_agent}]")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            feed_logger.error(f"{request.method} {request.path} ERROR {elapsed:.0f}ms [{client_ip}] - {e}")
            raise
    return decorated


def _get_components():
    """Late import to avoid circular imports at module level."""
    from main_app import db, storage, rss_parser, status_service
    return db, storage, rss_parser, status_service


def get_feed_map():
    """Wrapper that delegates to feeds module (allows patching in tests)."""
    from main_app.feeds import get_feed_map as _get_feed_map
    return _get_feed_map()


def _lookup_episode(slug, episode_id, feed_map, episode_row=None):
    """Fetch the RSS feed once and return episode data + podcast name.

    Returns (episode_dict, podcast_name) or (None, None).
    episode_dict keys: url, title, description, artwork_url, published.
    Falls back to database if episode is not in the upstream RSS feed.
    """
    db, _, rss_parser, _ = _get_components()
    original_feed = rss_parser.fetch_feed(feed_map[slug]['in'])
    if original_feed:
        parsed_feed = rss_parser.parse_feed(original_feed)
        podcast_name = parsed_feed.feed.get('title', 'Unknown') if parsed_feed else 'Unknown'
        episodes = rss_parser.extract_episodes(original_feed)
        for ep in episodes:
            if ep['id'] == episode_id:
                return ep, podcast_name

    # Fallback: episode not in upstream RSS (dropped off due to age/cap).
    # Use the original_url stored in the database from discovery.
    episode = episode_row or db.get_episode(slug, episode_id)
    if episode and episode.get('original_url'):
        return {
            'id': episode_id,
            'url': episode['original_url'],
            'title': episode.get('title'),
            'description': episode.get('description'),
            'artwork_url': episode.get('artwork_url'),
            'published': episode.get('published_at'),
        }, episode.get('podcast_title', 'Unknown')

    return None, None


def _head_upstream(slug, episode_id, original_url):
    """Proxy a HEAD request to the upstream audio URL."""
    try:
        resp = requests.head(original_url, timeout=10, allow_redirects=True,
                             headers={'User-Agent': APP_USER_AGENT})
        if resp.status_code == 200:
            proxy_resp = Response('', status=200)
            for h in ('Content-Type', 'Accept-Ranges'):
                if h in resp.headers:
                    proxy_resp.headers[h] = resp.headers[h]
            if 'Content-Length' in resp.headers:
                proxy_resp.content_length = int(resp.headers['Content-Length'])
            return proxy_resp
    except requests.exceptions.RequestException as e:
        feed_logger.warning(f"[{slug}:{episode_id}] HEAD upstream failed: {e}")
    abort(503)


def register_routes(app):
    """Register all routes on the Flask app."""
    global STATIC_DIR, ROOT_DIR

    STATIC_DIR = Path(__file__).parent.parent.parent / 'static' / 'ui'
    ROOT_DIR = Path(__file__).parent.parent.parent

    # ========== Web UI Static File Serving ==========

    @app.route('/ui/')
    @app.route('/ui/<path:path>')
    def serve_ui(path=''):
        """Serve React UI static files."""
        if not STATIC_DIR.exists():
            return "UI not built. Run 'npm run build' in frontend directory.", 404

        # For assets directory, return 404 if file doesn't exist (don't serve index.html)
        # This prevents MIME type errors when JS/CSS files are not found
        if path and path.startswith('assets/') and not (STATIC_DIR / path).exists():
            return f"Asset not found: {path}", 404

        # Serve index.html for SPA routes (non-asset paths)
        if not path or not (STATIC_DIR / path).exists():
            return send_from_directory(STATIC_DIR, 'index.html')

        return send_from_directory(STATIC_DIR, path)

    # ========== API Documentation ==========

    @app.route('/docs')
    @app.route('/docs/')
    def swagger_ui():
        """Serve Swagger UI for API documentation."""
        return '''<!DOCTYPE html>
<html>
<head>
    <title>MinusPod API</title>
    <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        SwaggerUIBundle({
            url: "/openapi.yaml",
            dom_id: '#swagger-ui',
            presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
            layout: "BaseLayout"
        });
    </script>
</body>
</html>'''

    @app.route('/openapi.yaml')
    def serve_openapi():
        """Serve OpenAPI specification with dynamic version."""
        openapi_path = ROOT_DIR / 'openapi.yaml'
        if openapi_path.exists():
            try:
                from version import __version__
                import re
                content = openapi_path.read_text()
                # Replace version line dynamically
                content = re.sub(
                    r'^(\s*version:\s*).*$',
                    rf'\g<1>{__version__}',
                    content,
                    count=1,
                    flags=re.MULTILINE
                )
                return Response(content, mimetype='application/x-yaml')
            except Exception:
                return send_file(openapi_path, mimetype='application/x-yaml')
        abort(404)

    # ========== Browser Icon Routes ==========
    # Short-circuit favicon/apple-touch-icon requests so they don't fall through
    # to the /<slug> feed route and trigger expensive DB lookups.

    @app.route('/favicon.ico')
    def favicon():
        response = send_from_directory(STATIC_DIR, 'favicon.svg')
        response.headers['Content-Type'] = 'image/svg+xml'
        return response

    @app.route('/apple-touch-icon.png')
    @app.route('/apple-touch-icon-precomposed.png')
    @app.route('/apple-touch-icon-120x120.png')
    @app.route('/apple-touch-icon-120x120-precomposed.png')
    def apple_touch_icon():
        return send_from_directory(STATIC_DIR, 'apple-touch-icon.png')

    # ========== RSS Feed Routes ==========

    @app.route('/<slug>')
    @log_request_detailed
    def serve_rss(slug):
        """Serve modified RSS feed."""
        # Import here to use the module-level get_feed_map (patchable)
        import main_app.routes as _routes
        from main_app.feeds import refresh_all_feeds, refresh_rss_feed
        db, storage, _, _ = _get_components()

        feed_map = _routes.get_feed_map()

        if slug not in feed_map:
            refresh_logger.info(f"[{slug}] Not found, refreshing feeds")
            refresh_all_feeds()
            feed_map = _routes.get_feed_map()

            if slug not in feed_map:
                feed_logger.warning(f"[{slug}] Feed not found")
                abort(404)

        # Check if RSS cache exists or is stale
        cached_rss = storage.get_rss(slug)
        data = storage.load_data_json(slug)
        last_checked = data.get('last_checked')

        should_refresh = False
        force_refresh = False  # Force full fetch bypasses 304 - use when cache is missing
        if not cached_rss:
            should_refresh = True
            force_refresh = True  # No cache, must get full content (can't use 304)
            feed_logger.info(f"[{slug}] No RSS cache, refreshing")
        elif last_checked:
            try:
                last_time = parse_iso_datetime(last_checked)
                age_minutes = (datetime.now(timezone.utc) - last_time).total_seconds() / 60
                if age_minutes > 15:
                    should_refresh = True
                    feed_logger.info(f"[{slug}] RSS cache stale ({age_minutes:.0f}min), refreshing")
            except (ValueError, TypeError):
                should_refresh = True

        if should_refresh:
            refresh_rss_feed(slug, feed_map[slug]['in'], force=force_refresh)
            cached_rss = storage.get_rss(slug)

        if cached_rss:
            feed_logger.info(f"[{slug}] Serving RSS feed")
            return Response(cached_rss, mimetype='application/rss+xml')
        else:
            feed_logger.error(f"[{slug}] RSS feed not available")
            abort(503)

    @app.route('/episodes/<slug>/<episode_id>.mp3')
    @log_request_detailed
    def serve_episode(slug, episode_id):
        """Serve processed episode audio (JIT processing)."""
        # Use module-level references so tests can patch them
        import main_app.routes as _routes
        from main_app.feeds import refresh_all_feeds
        from main_app.processing import start_background_processing
        db, storage, _, status_service = _get_components()

        feed_map = _routes.get_feed_map()

        if slug not in feed_map:
            feed_logger.info(f"[{slug}] Not found for episode {episode_id}, refreshing")
            refresh_all_feeds()
            feed_map = _routes.get_feed_map()

            if slug not in feed_map:
                feed_logger.warning(f"[{slug}] Feed not found for episode {episode_id}")
                abort(404)

        # Validate episode ID
        if not all(c.isalnum() or c in '-_' for c in episode_id):
            feed_logger.warning(f"[{slug}] Invalid episode ID: {episode_id}")
            abort(400)

        # Check episode status
        episode = db.get_episode(slug, episode_id)
        status = episode['status'] if episode else None

        if status == 'processed':
            file_path = storage.get_episode_path(slug, episode_id)
            if file_path.exists():
                feed_logger.info(f"[{slug}:{episode_id}] Cache hit")
                return send_file(file_path, mimetype='audio/mpeg')
            else:
                feed_logger.error(f"[{slug}:{episode_id}] Processed file missing")
                status = None

        elif status == 'permanently_failed':
            ep_key = f"{slug}:{episode_id}"
            if ep_key not in _permanently_failed_warned:
                _permanently_failed_warned.add(ep_key)
                feed_logger.warning(f"[{ep_key}] Episode permanently failed, not retrying")
            else:
                feed_logger.debug(f"[{ep_key}] Episode permanently failed (already warned)")
            return Response(
                "Episode processing has permanently failed after multiple attempts",
                status=410  # Gone - resource no longer available
            )

        elif status == 'failed':
            retry_count = episode.get('retry_count', 0) or 0
            if retry_count >= MAX_EPISODE_RETRIES:
                # Mark as permanently failed
                feed_logger.warning(f"[{slug}:{episode_id}] Max retries ({MAX_EPISODE_RETRIES}) exceeded, marking permanently failed")
                db.upsert_episode(slug, episode_id, status='permanently_failed')
                return Response(
                    "Episode processing has permanently failed after multiple attempts",
                    status=410
                )
            # Cooldown check - don't retry if failed recently (gives CDN time to propagate)
            updated_at = episode.get('updated_at')
            if updated_at and retry_count > 0:
                last_update = parse_iso_datetime(updated_at)
                now = datetime.now(timezone.utc)
                cooldown_seconds = JIT_RETRY_COOLDOWN_SECONDS * (2 ** (retry_count - 1))
                elapsed = (now - last_update).total_seconds()
                if elapsed < cooldown_seconds:
                    wait_remaining = int(cooldown_seconds - elapsed)
                    feed_logger.debug(f"[{slug}:{episode_id}] Failed {elapsed:.0f}s ago, cooldown {cooldown_seconds}s (retry {retry_count})")
                    return Response(
                        "Episode processing failed recently, retrying soon",
                        status=503,
                        headers={'Retry-After': str(max(wait_remaining, 30))}
                    )

            feed_logger.info(f"[{slug}:{episode_id}] Retrying failed episode (attempt {retry_count + 1}/{MAX_EPISODE_RETRIES})")
            status = None

        elif status == 'processing':
            feed_logger.info(f"[{slug}:{episode_id}] Currently processing")
            return Response(
                "Episode is being processed",
                status=503,
                headers={'Retry-After': '30'}
            )

        # HEAD requests should not trigger processing - proxy upstream headers
        if request.method == 'HEAD' and status != 'processed':
            ep_data, _ = _routes._lookup_episode(slug, episode_id, feed_map, episode_row=episode)
            if ep_data:
                return _routes._head_upstream(slug, episode_id, ep_data['url'])
            abort(404)

        # Need to process - find original URL from RSS
        ep_data, podcast_name = _routes._lookup_episode(slug, episode_id, feed_map, episode_row=episode)
        if not ep_data:
            feed_logger.error(f"[{slug}:{episode_id}] Episode not found in RSS or database")
            abort(404)

        original_url = ep_data['url']
        episode_title = ep_data.get('title', 'Unknown')
        episode_description = ep_data.get('description')
        episode_artwork_url = ep_data.get('artwork_url')

        # Start background processing (non-blocking)
        started, reason = start_background_processing(
            slug, episode_id, original_url, episode_title,
            podcast_name, episode_description, episode_artwork_url,
            published_at=ep_data.get('published')
        )

        if started:
            feed_logger.info(f"[{slug}:{episode_id}] Started background processing")
            return Response(
                "Episode processing started, please retry",
                status=503,
                headers={'Retry-After': '30'}
            )
        elif reason == "already_processing":
            feed_logger.info(f"[{slug}:{episode_id}] Already processing")
            return Response(
                "Episode is being processed",
                status=503,
                headers={'Retry-After': '30'}
            )
        else:
            # Queue is busy with another episode - queue this one and return 503
            status_service.queue_episode(slug, episode_id, episode_title, podcast_name)
            queue_position = status_service.get_queue_position(slug, episode_id)
            feed_logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), queued at position {queue_position}")
            return Response(
                json.dumps({
                    'status': 'queued',
                    'message': f'Episode queued for processing at position {queue_position}',
                    'queuePosition': queue_position,
                    'retryAfter': 60
                }),
                status=503,
                mimetype='application/json',
                headers={'Retry-After': '60'}
            )

    @app.route('/episodes/<slug>/<episode_id>.vtt')
    @log_request_detailed
    def serve_transcript_vtt(slug, episode_id):
        """Serve VTT transcript for episode (Podcasting 2.0)."""
        _, storage, _, _ = _get_components()
        # Validate episode ID
        if not all(c.isalnum() or c in '-_' for c in episode_id):
            feed_logger.warning(f"[{slug}] Invalid episode ID for VTT: {episode_id}")
            abort(400)

        vtt_content = storage.get_transcript_vtt(slug, episode_id)
        if not vtt_content:
            feed_logger.info(f"[{slug}:{episode_id}] VTT transcript not found")
            abort(404)

        feed_logger.info(f"[{slug}:{episode_id}] Serving VTT transcript")
        response = Response(vtt_content, mimetype='text/vtt')
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    @app.route('/episodes/<slug>/<episode_id>/chapters.json')
    @log_request_detailed
    def serve_chapters_json(slug, episode_id):
        """Serve chapters JSON for episode (Podcasting 2.0)."""
        _, storage, _, _ = _get_components()
        # Validate episode ID
        if not all(c.isalnum() or c in '-_' for c in episode_id):
            feed_logger.warning(f"[{slug}] Invalid episode ID for chapters: {episode_id}")
            abort(400)

        chapters = storage.get_chapters_json(slug, episode_id)
        if not chapters:
            feed_logger.info(f"[{slug}:{episode_id}] Chapters not found")
            abort(404)

        feed_logger.info(f"[{slug}:{episode_id}] Serving chapters JSON")
        response = Response(json.dumps(chapters), mimetype='application/json+chapters')
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    @app.route('/health')
    @log_request_detailed
    def health_check():
        """Health check endpoint."""
        import main_app.routes as _routes
        try:
            import sys
            # Add parent directory to path for version module
            parent_dir = str(Path(__file__).parent.parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            from version import __version__
            version = __version__
        except ImportError:
            version = 'unknown'

        feed_map = _routes.get_feed_map()
        return {'status': 'ok', 'feeds': len(feed_map), 'version': version}

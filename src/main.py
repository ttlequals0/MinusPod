"""Main Flask web server for podcast ad removal with web UI."""
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, Response, send_file, abort, send_from_directory, request
from flask_cors import CORS
from slugify import slugify
import shutil

# Configure structured logging
_logging_configured = False

def setup_logging():
    """Configure application logging."""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()

    # Create formatters
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler only - Docker captures stdout for logging
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Configure root logger - clear existing handlers first to prevent duplicates
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, log_level, logging.INFO))
    root.addHandler(console_handler)

    # Set specific logger levels
    logging.getLogger('werkzeug').setLevel(logging.INFO)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    # Create application loggers
    for name in ['podcast.api', 'podcast.feed', 'podcast.audio',
                 'podcast.transcribe', 'podcast.claude', 'podcast.refresh']:
        logging.getLogger(name).setLevel(getattr(logging, log_level, logging.INFO))


setup_logging()
logger = logging.getLogger('podcast.app')
feed_logger = logging.getLogger('podcast.feed')
refresh_logger = logging.getLogger('podcast.refresh')
audio_logger = logging.getLogger('podcast.audio')


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


# Initialize Flask app
app = Flask(__name__)

# Enable CORS for development (Vite dev server)
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:5173", "http://localhost:3000", "http://localhost:8080"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Import and register API blueprint
from api import api as api_blueprint
app.register_blueprint(api_blueprint)

# Import components
from storage import Storage
from rss_parser import RSSParser
from transcriber import Transcriber
from ad_detector import AdDetector, merge_and_deduplicate, refine_ad_boundaries, merge_same_sponsor_ads
from ad_validator import AdValidator
from audio_processor import AudioProcessor
from database import Database

# Initialize components
storage = Storage()
rss_parser = RSSParser()
transcriber = Transcriber()
ad_detector = AdDetector()
audio_processor = AudioProcessor()
db = Database()


def get_feed_map():
    """Get feed map from database."""
    feeds = db.get_feeds_config()
    return {slugify(feed['out'].strip('/')): feed for feed in feeds}


def refresh_rss_feed(slug: str, feed_url: str):
    """Refresh RSS feed for a podcast."""
    try:
        refresh_logger.info(f"[{slug}] Starting RSS refresh from: {feed_url}")

        # Fetch original RSS
        feed_content = rss_parser.fetch_feed(feed_url)
        if not feed_content:
            refresh_logger.error(f"[{slug}] Failed to fetch RSS feed")
            return False

        # Parse feed to extract metadata
        parsed_feed = rss_parser.parse_feed(feed_content)
        if parsed_feed and parsed_feed.feed:
            title = parsed_feed.feed.get('title')
            description = parsed_feed.feed.get('description', '')[:500]

            # Extract artwork URL
            artwork_url = None
            if hasattr(parsed_feed.feed, 'image') and hasattr(parsed_feed.feed.image, 'href'):
                artwork_url = parsed_feed.feed.image.href
            elif 'image' in parsed_feed.feed and 'href' in parsed_feed.feed.image:
                artwork_url = parsed_feed.feed.image.href

            # Update podcast metadata in database
            db.update_podcast(
                slug,
                title=title,
                description=description,
                artwork_url=artwork_url,
                last_checked_at=datetime.utcnow().isoformat() + 'Z'
            )

            # Download artwork if available
            if artwork_url:
                storage.download_artwork(slug, artwork_url)

        # Modify feed URLs
        modified_rss = rss_parser.modify_feed(feed_content, slug)

        # Save modified RSS
        storage.save_rss(slug, modified_rss)

        # Update last_checked timestamp
        db.update_podcast(slug, last_checked_at=datetime.utcnow().isoformat() + 'Z')

        refresh_logger.info(f"[{slug}] RSS refresh complete")
        return True
    except Exception as e:
        refresh_logger.error(f"[{slug}] RSS refresh failed: {e}")
        return False


def refresh_all_feeds():
    """Refresh all RSS feeds once."""
    try:
        refresh_logger.info("Refreshing all RSS feeds")

        feed_map = get_feed_map()
        for slug, feed_info in feed_map.items():
            refresh_rss_feed(slug, feed_info['in'])

        refresh_logger.info(f"RSS refresh complete for {len(feed_map)} feeds")
        return True
    except Exception as e:
        refresh_logger.error(f"RSS refresh failed: {e}")
        return False


def run_cleanup():
    """Run episode cleanup based on retention period."""
    try:
        deleted, freed_mb = db.cleanup_old_episodes()
        if deleted > 0:
            refresh_logger.info(f"Cleanup: removed {deleted} episodes, freed {freed_mb:.1f} MB")
    except Exception as e:
        refresh_logger.error(f"Cleanup failed: {e}")


def background_rss_refresh():
    """Background task to refresh RSS feeds every 15 minutes."""
    while True:
        refresh_all_feeds()
        run_cleanup()
        time.sleep(900)  # 15 minutes


def process_episode(slug: str, episode_id: str, episode_url: str,
                   episode_title: str = "Unknown", podcast_name: str = "Unknown",
                   episode_description: str = None):
    """Process a single episode (transcribe, detect ads, remove ads)."""
    start_time = time.time()

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Starting: \"{episode_title}\"")

        # Update status to processing
        db.upsert_episode(slug, episode_id,
            original_url=episode_url,
            title=episode_title,
            description=episode_description,
            status='processing')

        # Step 1: Check if transcript exists in database
        segments = None
        transcript_text = storage.get_transcript(slug, episode_id)

        if transcript_text:
            audio_logger.info(f"[{slug}:{episode_id}] Found existing transcript in database")

            # Parse segments from transcript
            segments = []
            for line in transcript_text.split('\n'):
                if line.strip() and line.startswith('['):
                    try:
                        time_part, text_part = line.split('] ', 1)
                        time_range = time_part.strip('[')
                        start_str, end_str = time_range.split(' --> ')

                        def parse_timestamp(ts):
                            parts = ts.split(':')
                            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

                        segments.append({
                            'start': parse_timestamp(start_str),
                            'end': parse_timestamp(end_str),
                            'text': text_part
                        })
                    except:
                        continue

            if segments:
                duration_min = segments[-1]['end'] / 60 if segments else 0
                audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(segments)} segments, {duration_min:.1f} min")

            # Still need to download audio for processing
            audio_path = transcriber.download_audio(episode_url)
            if not audio_path:
                raise Exception("Failed to download audio")
        else:
            # Download and transcribe
            audio_logger.info(f"[{slug}:{episode_id}] Downloading audio")
            audio_path = transcriber.download_audio(episode_url)
            if not audio_path:
                raise Exception("Failed to download audio")

            audio_logger.info(f"[{slug}:{episode_id}] Starting transcription")
            segments = transcriber.transcribe(audio_path, podcast_name=podcast_name)
            if not segments:
                raise Exception("Failed to transcribe audio")

            duration_min = segments[-1]['end'] / 60 if segments else 0
            audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

            # Save transcript
            transcript_text = transcriber.segments_to_text(segments)
            storage.save_transcript(slug, episode_id, transcript_text)

        try:
            # Step 2: Detect ads (first pass)
            ad_result = ad_detector.process_transcript(segments, podcast_name, episode_title, slug, episode_id, episode_description)
            storage.save_ads_json(slug, episode_id, ad_result, pass_number=1)

            # Check ad detection status
            ad_detection_status = ad_result.get('status', 'success')
            first_pass_ads = ad_result.get('ads', [])

            if ad_detection_status == 'failed':
                error_msg = ad_result.get('error', 'Unknown error')
                audio_logger.error(f"[{slug}:{episode_id}] Ad detection failed: {error_msg}")
                # Update database with failed status
                db.upsert_episode(slug, episode_id, ad_detection_status='failed')
                raise Exception(f"Ad detection failed: {error_msg}")

            # Update database with successful status
            db.upsert_episode(slug, episode_id, ad_detection_status='success')

            if first_pass_ads:
                total_ad_time = sum(ad['end'] - ad['start'] for ad in first_pass_ads)
                audio_logger.info(f"[{slug}:{episode_id}] First pass: Detected {len(first_pass_ads)} ads ({total_ad_time/60:.1f} min)")
            else:
                audio_logger.info(f"[{slug}:{episode_id}] First pass: No ads detected")

            # Track counts per pass
            first_pass_count = len(first_pass_ads)
            second_pass_count = 0

            # Track all ads (will combine first and second pass)
            all_ads = first_pass_ads.copy()

            # Step 3: Multi-pass detection (if enabled) - PARALLEL approach
            # Runs second pass on SAME original transcript (not re-transcribed)
            if ad_detector.is_multi_pass_enabled():
                audio_logger.info(f"[{slug}:{episode_id}] Multi-pass enabled, starting blind second pass")

                # Run BLIND second pass - independent analysis with different detection focus
                # Does NOT know what first pass found - we merge/dedupe results ourselves
                second_pass_result = ad_detector.detect_ads_second_pass(
                    segments,  # Same transcript, blind analysis
                    podcast_name, episode_title, slug, episode_id, episode_description
                )

                # Save second pass data to database
                storage.save_ads_json(slug, episode_id, second_pass_result, pass_number=2)

                second_pass_ads = second_pass_result.get('ads', [])

                if second_pass_ads:
                    # Merge and deduplicate ads from both passes
                    all_ads = merge_and_deduplicate(first_pass_ads, second_pass_ads)

                    # Calculate counts based on pass field in merged results
                    # pass=1: first pass only, pass=2: second pass only, pass='merged': both found
                    first_pass_only = sum(1 for ad in all_ads if ad.get('pass') == 1)
                    second_pass_only = sum(1 for ad in all_ads if ad.get('pass') == 2)
                    merged_count = sum(1 for ad in all_ads if ad.get('pass') == 'merged')

                    # Update counts: first_pass_count = first_only + merged, second_pass_count = second_only + merged
                    first_pass_count = first_pass_only + merged_count
                    second_pass_count = second_pass_only + merged_count

                    total_ad_time = sum(ad['end'] - ad['start'] for ad in all_ads)
                    audio_logger.info(f"[{slug}:{episode_id}] After merge: {len(all_ads)} ads "
                                     f"(first:{first_pass_only}, second:{second_pass_only}, merged:{merged_count}, {total_ad_time/60:.1f} min)")

                    # Save combined ad markers
                    storage.save_combined_ads(slug, episode_id, all_ads)
                else:
                    audio_logger.info(f"[{slug}:{episode_id}] Second pass: No additional ads found")

            # Step 3.5: Refine ad boundaries using word timestamps and keyword detection
            if all_ads and segments:
                all_ads = refine_ad_boundaries(all_ads, segments)

            # Step 3.6: Merge ads that mention the same sponsor with sponsor content in gaps
            # This handles Claude fragmenting long ads or mislabeling parts
            if all_ads and segments:
                all_ads = merge_same_sponsor_ads(all_ads, segments)

            # Step 3.7: Validate detected ads
            # Catches errors, flags suspicious detections, auto-corrects issues
            if all_ads:
                episode_duration = segments[-1]['end'] if segments else 0
                validator = AdValidator(episode_duration, segments)
                validation_result = validator.validate(all_ads)

                audio_logger.info(
                    f"[{slug}:{episode_id}] Validation: "
                    f"{validation_result.accepted} accepted, "
                    f"{validation_result.reviewed} review, "
                    f"{validation_result.rejected} rejected"
                )

                # Store ALL ads (including rejected) for API/UI display
                all_ads_with_validation = validation_result.ads
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

                # Only remove ACCEPT and REVIEW ads from audio
                # REJECT ads stay in audio but are stored for display
                ads_to_remove = [
                    ad for ad in validation_result.ads
                    if ad.get('validation', {}).get('decision') != 'REJECT'
                ]

                rejected_count = validation_result.rejected
                if rejected_count > 0:
                    audio_logger.info(
                        f"[{slug}:{episode_id}] {rejected_count} ads rejected (kept in audio)"
                    )
            else:
                ads_to_remove = []
                all_ads_with_validation = []

            # Step 4: Process audio ONCE with validated ads (excluding rejected)
            audio_logger.info(f"[{slug}:{episode_id}] Starting FFMPEG processing ({len(ads_to_remove)} ads to remove)")
            processed_path = audio_processor.process_episode(audio_path, ads_to_remove)
            if not processed_path:
                raise Exception("Failed to process audio with FFMPEG")

            # Get durations
            original_duration = audio_processor.get_audio_duration(audio_path)
            new_duration = audio_processor.get_audio_duration(processed_path)

            # Move processed file to final location
            final_path = storage.get_episode_path(slug, episode_id)
            shutil.move(processed_path, final_path)

            # Update status to processed with combined ad count and per-pass counts
            # ads_removed counts only non-rejected ads (ones actually removed from audio)
            db.upsert_episode(slug, episode_id,
                status='processed',
                processed_file=f"episodes/{episode_id}.mp3",
                original_duration=original_duration,
                new_duration=new_duration,
                ads_removed=len(ads_to_remove),
                ads_removed_firstpass=first_pass_count,
                ads_removed_secondpass=second_pass_count)

            processing_time = time.time() - start_time

            # Track cumulative time saved
            if original_duration and new_duration:
                time_saved = original_duration - new_duration
                if time_saved > 0:
                    db.increment_total_time_saved(time_saved)

                audio_logger.info(
                    f"[{slug}:{episode_id}] Complete: {original_duration/60:.1f}->{new_duration/60:.1f}min, "
                    f"{len(ads_to_remove)} ads removed, {processing_time:.1f}s"
                )
            else:
                audio_logger.info(f"[{slug}:{episode_id}] Complete: {len(ads_to_remove)} ads removed, {processing_time:.1f}s")

            return True

        finally:
            # Clean up temp audio file
            if os.path.exists(audio_path):
                os.unlink(audio_path)

    except Exception as e:
        processing_time = time.time() - start_time
        audio_logger.error(f"[{slug}:{episode_id}] Failed: {e} ({processing_time:.1f}s)")

        # Update status to failed
        db.upsert_episode(slug, episode_id,
            status='failed',
            error_message=str(e))
        return False


# ========== Web UI Static File Serving ==========

STATIC_DIR = Path(__file__).parent.parent / 'static' / 'ui'
ROOT_DIR = Path(__file__).parent.parent


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
    <title>Podcast Server API</title>
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
            content = openapi_path.read_text()
            # Replace version line dynamically
            import re
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


# ========== RSS Feed Routes ==========

@app.route('/<slug>')
@log_request_detailed
def serve_rss(slug):
    """Serve modified RSS feed."""
    feed_map = get_feed_map()

    if slug not in feed_map:
        refresh_logger.info(f"[{slug}] Not found, refreshing feeds")
        refresh_all_feeds()
        feed_map = get_feed_map()

        if slug not in feed_map:
            feed_logger.warning(f"[{slug}] Feed not found")
            abort(404)

    # Check if RSS cache exists or is stale
    cached_rss = storage.get_rss(slug)
    data = storage.load_data_json(slug)
    last_checked = data.get('last_checked')

    should_refresh = False
    if not cached_rss:
        should_refresh = True
        feed_logger.info(f"[{slug}] No RSS cache, refreshing")
    elif last_checked:
        try:
            last_time = datetime.fromisoformat(last_checked.replace('Z', '+00:00'))
            age_minutes = (datetime.utcnow() - last_time.replace(tzinfo=None)).total_seconds() / 60
            if age_minutes > 15:
                should_refresh = True
                feed_logger.info(f"[{slug}] RSS cache stale ({age_minutes:.0f}min), refreshing")
        except:
            should_refresh = True

    if should_refresh:
        refresh_rss_feed(slug, feed_map[slug]['in'])
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
    feed_map = get_feed_map()

    if slug not in feed_map:
        feed_logger.info(f"[{slug}] Not found for episode {episode_id}, refreshing")
        refresh_all_feeds()
        feed_map = get_feed_map()

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

    elif status == 'failed':
        feed_logger.info(f"[{slug}:{episode_id}] Retrying failed episode")
        status = None

    elif status == 'processing':
        feed_logger.info(f"[{slug}:{episode_id}] Currently processing")
        abort(503)

    # Need to process - find original URL from RSS
    cached_rss = storage.get_rss(slug)
    if not cached_rss:
        feed_logger.error(f"[{slug}:{episode_id}] No RSS available")
        abort(404)

    original_feed = rss_parser.fetch_feed(feed_map[slug]['in'])
    if not original_feed:
        feed_logger.error(f"[{slug}:{episode_id}] Could not fetch original RSS")
        abort(503)

    parsed_feed = rss_parser.parse_feed(original_feed)
    podcast_name = parsed_feed.feed.get('title', 'Unknown') if parsed_feed else 'Unknown'

    episodes = rss_parser.extract_episodes(original_feed)
    original_url = None
    episode_title = "Unknown"
    episode_description = None
    for ep in episodes:
        if ep['id'] == episode_id:
            original_url = ep['url']
            episode_title = ep.get('title', 'Unknown')
            episode_description = ep.get('description')
            break

    if not original_url:
        feed_logger.error(f"[{slug}:{episode_id}] Episode not found in RSS")
        abort(404)

    feed_logger.info(f"[{slug}:{episode_id}] Starting processing")

    if process_episode(slug, episode_id, original_url, episode_title, podcast_name, episode_description):
        file_path = storage.get_episode_path(slug, episode_id)
        if file_path.exists():
            return send_file(file_path, mimetype='audio/mpeg')

    feed_logger.info(f"[{slug}:{episode_id}] Processing failed, redirecting to original")
    return Response(status=302, headers={'Location': original_url})


@app.route('/health')
@log_request_detailed
def health_check():
    """Health check endpoint."""
    try:
        import sys
        # Add parent directory to path for version module
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from version import __version__
        version = __version__
    except ImportError:
        version = 'unknown'

    feed_map = get_feed_map()
    return {'status': 'ok', 'feeds': len(feed_map), 'version': version}


# Startup initialization (runs when module is imported by gunicorn)
def _startup():
    """Initialize the application on startup."""
    # Import and log version
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from version import __version__
        logger.info(f"Podcast Server v{__version__} starting...")
    except ImportError:
        logger.warning("Could not import version")

    base_url = os.getenv('BASE_URL', 'http://localhost:8000')
    logger.info(f"BASE_URL: {base_url}")

    # Start background RSS refresh thread
    refresh_thread = threading.Thread(target=background_rss_refresh, daemon=True)
    refresh_thread.start()
    logger.info("Started background refresh thread")

    # Initial RSS refresh
    logger.info("Performing initial RSS refresh")
    feed_map = get_feed_map()
    for slug, feed_info in feed_map.items():
        refresh_rss_feed(slug, feed_info['in'])
        logger.info(f"Feed: {base_url}/{slug}")

    logger.info(f"Web UI available at: {base_url}/ui/")


_startup()

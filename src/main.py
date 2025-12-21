"""Main Flask web server for podcast ad removal with web UI."""
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from functools import wraps
from flask import Flask, Response, send_file, abort, send_from_directory, request
from flask_cors import CORS
from slugify import slugify
import shutil

# Configure structured logging
_logging_configured = False
import json as _json


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Outputs logs as JSON objects for easier parsing by log aggregators
    like Loki, Elasticsearch, or CloudWatch.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, 'episode_id'):
            log_data['episode_id'] = record.episode_id
        if hasattr(record, 'slug'):
            log_data['slug'] = record.slug

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return _json.dumps(log_data)


def setup_logging():
    """Configure application logging.

    Environment variables:
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO
        LOG_FORMAT: Log output format ('text' or 'json'). Default: text
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_format = os.environ.get('LOG_FORMAT', 'text').lower()

    # Create appropriate formatter based on LOG_FORMAT
    if log_format == 'json':
        formatter = JSONFormatter(datefmt='%Y-%m-%dT%H:%M:%S')
    else:
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

# Minimum confidence threshold to cut an ad from audio
# Ads below this threshold are kept in audio to avoid false positives
MIN_CUT_CONFIDENCE = 0.80


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


# Maximum retry attempts for failed episodes before marking as permanently_failed
MAX_EPISODE_RETRIES = 3

# Import exception types for error classification
import requests.exceptions
try:
    from anthropic import APIError, APIConnectionError, RateLimitError, InternalServerError
    ANTHROPIC_EXCEPTIONS = True
except ImportError:
    ANTHROPIC_EXCEPTIONS = False


def is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient (worth retrying) or permanent.

    Transient errors (retry):
    - Network timeouts and connection errors
    - Rate limiting
    - Server-side errors (5xx)

    Permanent errors (don't retry):
    - Invalid requests (4xx client errors)
    - Invalid audio format
    - File not found
    - Value errors (bad data)
    """
    # Network/connection errors are transient
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        ConnectionError,
        TimeoutError,
    )):
        return True

    # Anthropic errors
    if ANTHROPIC_EXCEPTIONS:
        # Transient Anthropic errors
        if isinstance(error, (RateLimitError, APIConnectionError, InternalServerError)):
            return True
        # Permanent Anthropic errors (BadRequestError, AuthenticationError, etc.)
        if isinstance(error, APIError):
            return False

    # Permanent errors - don't retry
    if isinstance(error, (
        ValueError,
        FileNotFoundError,
        PermissionError,
        TypeError,
    )):
        return False

    # Check error message for common permanent failure patterns
    error_msg = str(error).lower()
    permanent_patterns = [
        'invalid audio',
        'unsupported format',
        'corrupt',
        'authentication',
        'unauthorized',
        'forbidden',
        'not found',
        '400 ',
        '401 ',
        '403 ',
        '404 ',
    ]
    if any(pattern in error_msg for pattern in permanent_patterns):
        return False

    # Default: assume transient for unknown errors (safer to retry)
    return True


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
from api import api as api_blueprint, init_limiter
app.register_blueprint(api_blueprint)
init_limiter(app)

# Import components
from storage import Storage
from rss_parser import RSSParser
from transcriber import Transcriber
from ad_detector import AdDetector, merge_and_deduplicate, refine_ad_boundaries, merge_same_sponsor_ads
from ad_validator import AdValidator
from audio_processor import AudioProcessor
from database import Database
from processing_queue import ProcessingQueue
from audio_analysis import AudioAnalyzer
from sponsor_service import SponsorService
from status_service import StatusService
from pattern_service import PatternService

# Initialize components
storage = Storage()
rss_parser = RSSParser()
transcriber = Transcriber()
ad_detector = AdDetector()
audio_processor = AudioProcessor()
db = Database()
audio_analyzer = AudioAnalyzer(db=db)
sponsor_service = SponsorService(db)
status_service = StatusService()
pattern_service = PatternService(db)

# Backfill processing history from existing episodes (runs once on startup)
try:
    backfilled = db.backfill_processing_history()
    if backfilled > 0:
        audio_logger.info(f"Backfilled {backfilled} records to processing_history")
except Exception as e:
    audio_logger.warning(f"History backfill failed: {e}")

# Backfill patterns from existing corrections (runs once on startup)
try:
    patterns_created = db.backfill_patterns_from_corrections()
    if patterns_created > 0:
        audio_logger.info(f"Created {patterns_created} patterns from existing corrections")
except Exception as e:
    audio_logger.warning(f"Pattern backfill failed: {e}")

# Graceful shutdown support
shutdown_event = threading.Event()
processing_queue = ProcessingQueue()


def graceful_shutdown(signum, frame):
    """Handle shutdown signals gracefully.

    Waits for current processing to complete (up to 5 minutes)
    before exiting.
    """
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name} signal, initiating graceful shutdown...")

    # Signal all background threads to stop
    shutdown_event.set()

    # Wait for processing queue to finish current episode (max 5 minutes)
    max_wait = 300
    waited = 0
    while processing_queue.is_busy() and waited < max_wait:
        current = processing_queue.get_current()
        if current:
            logger.info(f"Waiting for processing to complete: {current[0]}:{current[1]} ({waited}s/{max_wait}s)")
        time.sleep(5)
        waited += 5

    if processing_queue.is_busy():
        logger.warning("Shutdown timeout reached, forcing exit with incomplete processing")
    else:
        logger.info("All processing complete, shutting down cleanly")

    sys.exit(0)

# Deduplicate patterns (cleanup duplicate patterns from earlier bugs)
try:
    deduped = db.deduplicate_patterns()
    if deduped > 0:
        audio_logger.info(f"Removed {deduped} duplicate patterns")
except Exception as e:
    audio_logger.warning(f"Pattern deduplication failed: {e}")

# Extract sponsors for patterns that don't have one
try:
    sponsors_extracted = db.extract_sponsors_for_patterns()
    if sponsors_extracted > 0:
        audio_logger.info(f"Extracted sponsors for {sponsors_extracted} patterns")
except Exception as e:
    audio_logger.warning(f"Sponsor extraction failed: {e}")


def get_feed_map():
    """Get feed map from database."""
    feeds = db.get_feeds_config()
    return {slugify(feed['out'].strip('/')): feed for feed in feeds}


def refresh_rss_feed(slug: str, feed_url: str):
    """Refresh RSS feed for a podcast."""
    try:
        # Get podcast name for status display
        podcast = db.get_podcast(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # Track feed refresh in status service
        status_service.start_feed_refresh(slug, podcast_name)

        refresh_logger.info(f"[{slug}] Starting RSS refresh from: {feed_url}")

        # Fetch original RSS
        feed_content = rss_parser.fetch_feed(feed_url)
        if not feed_content:
            refresh_logger.error(f"[{slug}] Failed to fetch RSS feed")
            status_service.complete_feed_refresh(slug, 0)
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

            # Detect DAI platform and network from feed metadata
            feed_author = parsed_feed.feed.get('author', '')
            network_info = pattern_service.update_podcast_metadata(
                podcast_id=slug,
                feed_url=feed_url,
                feed_content=feed_content,
                feed_title=title,
                feed_description=description,
                feed_author=feed_author
            )
            if network_info.get('dai_platform') or network_info.get('network_id'):
                refresh_logger.info(
                    f"[{slug}] Detected: platform={network_info.get('dai_platform')}, "
                    f"network={network_info.get('network_id')}"
                )

            # Download artwork if available
            if artwork_url:
                storage.download_artwork(slug, artwork_url)

        # Queue new episodes for auto-processing if enabled
        # Only queue episodes published within the last 48 hours to avoid processing entire backlog
        if db.is_auto_process_enabled_for_podcast(slug):
            episodes = rss_parser.extract_episodes(feed_content)
            queued_count = 0
            cutoff_time = datetime.utcnow() - timedelta(hours=48)

            for ep in episodes:
                # Check if episode already exists in database
                existing = db.get_episode(slug, ep['id'])
                if existing is None:
                    # Parse publish date to check if recent
                    published_str = ep.get('published', '')
                    is_recent = False
                    if published_str:
                        try:
                            # RSS dates are typically RFC 2822 format
                            pub_date = parsedate_to_datetime(published_str)
                            # Make comparison timezone-naive
                            if pub_date.tzinfo:
                                pub_date = pub_date.replace(tzinfo=None)
                            is_recent = pub_date >= cutoff_time
                        except (ValueError, TypeError):
                            # If we can't parse the date, skip this episode for auto-process
                            refresh_logger.debug(f"[{slug}] Could not parse date for episode: {ep.get('title')}")
                            is_recent = False

                    if is_recent:
                        # New recent episode - queue for processing
                        # Convert pubDate to ISO format for storage
                        iso_published = None
                        if published_str:
                            try:
                                parsed_pub = parsedate_to_datetime(published_str)
                                iso_published = parsed_pub.strftime('%Y-%m-%dT%H:%M:%SZ')
                            except (ValueError, TypeError):
                                pass
                        queue_id = db.queue_episode_for_processing(
                            slug, ep['id'], ep['url'], ep.get('title'), iso_published
                        )
                        if queue_id:
                            queued_count += 1
                            refresh_logger.debug(f"[{slug}] Queued recent episode: {ep.get('title')}")

            if queued_count > 0:
                refresh_logger.info(f"[{slug}] Queued {queued_count} new episode(s) for auto-processing")

        # Modify feed URLs
        modified_rss = rss_parser.modify_feed(feed_content, slug)

        # Save modified RSS
        storage.save_rss(slug, modified_rss)

        # Update last_checked timestamp
        db.update_podcast(slug, last_checked_at=datetime.utcnow().isoformat() + 'Z')

        refresh_logger.info(f"[{slug}] RSS refresh complete")
        status_service.complete_feed_refresh(slug, 0)  # TODO: Count new episodes
        return True
    except Exception as e:
        refresh_logger.error(f"[{slug}] RSS refresh failed: {e}")
        status_service.remove_feed_refresh(slug)
        return False


def refresh_all_feeds():
    """Refresh all RSS feeds in parallel."""
    try:
        refresh_logger.info("Refreshing all RSS feeds")

        feed_map = get_feed_map()

        # Parallelize feed refresh with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(refresh_rss_feed, slug, feed_info['in']): slug
                for slug, feed_info in feed_map.items()
            }
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    future.result()
                except Exception as e:
                    refresh_logger.error(f"[{slug}] Feed refresh failed: {e}")

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

    # Clean orphan podcast directories (podcasts deleted from DB but directories remain)
    try:
        valid_slugs = {p['slug'] for p in db.get_all_podcasts()}
        podcast_base = os.path.join(storage.data_dir, 'podcasts')
        if os.path.exists(podcast_base):
            for slug in os.listdir(podcast_base):
                if slug not in valid_slugs:
                    orphan_path = os.path.join(podcast_base, slug)
                    if os.path.isdir(orphan_path):
                        refresh_logger.warning(f"Removing orphan podcast directory: {slug}")
                        shutil.rmtree(orphan_path, ignore_errors=True)
    except Exception as e:
        refresh_logger.error(f"Orphan cleanup failed: {e}")


def background_rss_refresh():
    """Background task to refresh RSS feeds every 15 minutes.

    Uses shutdown_event.wait() instead of time.sleep() to allow
    graceful shutdown interruption.
    """
    while not shutdown_event.is_set():
        refresh_all_feeds()
        run_cleanup()
        # Wait 15 minutes, but allow early exit on shutdown
        shutdown_event.wait(timeout=900)


def background_queue_processor():
    """Background task to process queued episodes for auto-processing.

    Uses shutdown_event for graceful shutdown support.
    """
    refresh_logger.info("Auto-process queue processor started")
    backoff_seconds = 30  # Initial backoff for busy queue
    while not shutdown_event.is_set():
        try:
            # Get next queued episode
            queued = db.get_next_queued_episode()

            if queued:
                queue_id = queued['id']
                slug = queued['podcast_slug']
                episode_id = queued['episode_id']
                original_url = queued['original_url']
                title = queued.get('title', 'Unknown')
                podcast_name = queued.get('podcast_title', slug)
                published_at = queued.get('published_at')

                refresh_logger.info(f"[{slug}:{episode_id}] Auto-processing queued episode: {title}")

                # Mark as processing
                db.update_queue_status(queue_id, 'processing')

                try:
                    # Try to start background processing using the existing queue
                    started, reason = start_background_processing(
                        slug, episode_id, original_url, title, podcast_name, None, None, published_at
                    )

                    if started:
                        # Reset backoff on successful start
                        backoff_seconds = 30
                        # Wait for processing to complete (poll status)
                        max_wait = 600  # 10 minutes max
                        waited = 0
                        while waited < max_wait and not shutdown_event.is_set():
                            shutdown_event.wait(timeout=10)
                            waited += 10
                            episode = db.get_episode(slug, episode_id)
                            if episode and episode['status'] in ('processed', 'failed', 'permanently_failed'):
                                break

                        # Check final status
                        episode = db.get_episode(slug, episode_id)
                        if episode and episode['status'] == 'processed':
                            db.update_queue_status(queue_id, 'completed')
                            refresh_logger.info(f"[{slug}:{episode_id}] Auto-process completed successfully")
                        else:
                            error_msg = episode.get('error_message', 'Processing failed') if episode else 'Unknown error'
                            db.update_queue_status(queue_id, 'failed', error_msg)
                            refresh_logger.warning(f"[{slug}:{episode_id}] Auto-process failed: {error_msg}")
                    elif reason == "already_processing":
                        # Episode is already being processed, wait with backoff
                        refresh_logger.info(f"[{slug}:{episode_id}] Already processing, waiting {backoff_seconds}s...")
                        shutdown_event.wait(timeout=backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 300)  # Max 5 minutes
                    else:
                        # Queue is busy with another episode, try again later with backoff
                        db.update_queue_status(queue_id, 'pending')  # Put back in queue
                        refresh_logger.debug(f"[{slug}:{episode_id}] Queue busy, will retry in {backoff_seconds}s")
                        shutdown_event.wait(timeout=backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 300)  # Max 5 minutes

                except Exception as e:
                    db.update_queue_status(queue_id, 'failed', str(e))
                    refresh_logger.error(f"[{slug}:{episode_id}] Auto-process error: {e}")

            else:
                # No queued episodes, wait before checking again
                shutdown_event.wait(timeout=30)

            # Periodically clean up completed queue items
            db.clear_completed_queue_items(older_than_hours=24)

        except Exception as e:
            refresh_logger.error(f"Queue processor error: {e}")
            shutdown_event.wait(timeout=60)  # Wait before retrying on error


def reset_stuck_processing_episodes():
    """Reset any episodes stuck in 'processing' status from previous crash."""
    conn = db.get_connection()
    cursor = conn.execute(
        """SELECT e.id, e.episode_id, p.slug
           FROM episodes e
           JOIN podcasts p ON e.podcast_id = p.id
           WHERE e.status = 'processing'"""
    )
    stuck = cursor.fetchall()

    for row in stuck:
        refresh_logger.warning(
            f"Resetting stuck episode: {row['slug']}/{row['episode_id']}"
        )
        conn.execute(
            "UPDATE episodes SET status = 'pending', error_message = 'Reset after restart' "
            "WHERE id = ?",
            (row['id'],)
        )
    conn.commit()

    if stuck:
        refresh_logger.info(f"Reset {len(stuck)} stuck episodes to pending")


def _process_episode_background(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None):
    """Background thread wrapper for process_episode with queue management."""
    queue = ProcessingQueue()
    try:
        process_episode(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at)
    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Background processing failed: {e}")
    finally:
        queue.release()


def start_background_processing(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None):
    """
    Start processing in background thread.

    Returns:
        Tuple of (started: bool, reason: str)
        - (True, "started") if processing was started
        - (False, "already_processing") if this episode is already being processed
        - (False, "queue_busy:slug:episode_id") if another episode is processing
    """
    queue = ProcessingQueue()

    # Check if already processing this episode
    if queue.is_processing(slug, episode_id):
        return False, "already_processing"

    # Check if queue is busy with another episode
    if not queue.acquire(slug, episode_id, timeout=0):
        current = queue.get_current()
        if current:
            return False, f"queue_busy:{current[0]}:{current[1]}"
        return False, "queue_busy"

    # Start background thread
    processing_thread = threading.Thread(
        target=_process_episode_background,
        args=(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at),
        daemon=True
    )
    processing_thread.start()

    return True, "started"


def process_episode(slug: str, episode_id: str, episode_url: str,
                   episode_title: str = "Unknown", podcast_name: str = "Unknown",
                   episode_description: str = None, episode_artwork_url: str = None,
                   episode_published_at: str = None):
    """Process a single episode (transcribe, detect ads, remove ads)."""
    start_time = time.time()

    # Check for reprocess mode (Gap 3 fix)
    episode_data = db.get_episode(slug, episode_id)
    reprocess_mode = episode_data.get('reprocess_mode') if episode_data else None
    skip_patterns = reprocess_mode == 'full'  # 'full' mode = skip pattern DB, Claude only

    if reprocess_mode:
        audio_logger.info(f"[{slug}:{episode_id}] Reprocess mode: {reprocess_mode} (skip_patterns={skip_patterns})")

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Starting: \"{episode_title}\"")

        # Track status for UI
        status_service.start_job(slug, episode_id, episode_title, podcast_name)
        status_service.update_job_stage("downloading", 0)

        # Update status to processing
        db.upsert_episode(slug, episode_id,
            original_url=episode_url,
            title=episode_title,
            description=episode_description,
            artwork_url=episode_artwork_url,
            published_at=episode_published_at,
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

            status_service.update_job_stage("transcribing", 20)
            audio_logger.info(f"[{slug}:{episode_id}] Starting transcription")
            segments = transcriber.transcribe(audio_path, podcast_name=podcast_name)
            if not segments:
                raise Exception("Failed to transcribe audio")

            duration_min = segments[-1]['end'] / 60 if segments else 0
            audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

            # Save transcript
            transcript_text = transcriber.segments_to_text(segments)
            storage.save_transcript(slug, episode_id, transcript_text)

            # Unload Whisper model to free ~5-6GB memory for audio analysis
            from transcriber import WhisperModelSingleton
            WhisperModelSingleton.unload_model()
            audio_logger.info(f"[{slug}:{episode_id}] Unloaded Whisper model before audio analysis")

        # Step 1.5: Run audio analysis (if enabled for this podcast)
        audio_analysis_result = None
        if audio_analyzer.is_enabled_for_podcast(slug):
            status_service.update_job_stage("analyzing", 25)
            audio_logger.info(f"[{slug}:{episode_id}] Running audio analysis")
            try:
                audio_analysis_result = audio_analyzer.analyze(
                    audio_path,
                    transcript_segments=segments,
                    status_callback=lambda stage, progress: status_service.update_job_stage(stage, progress)
                )
                if audio_analysis_result.signals:
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Audio analysis: {len(audio_analysis_result.signals)} signals "
                        f"in {audio_analysis_result.analysis_time_seconds:.1f}s"
                    )
                if audio_analysis_result.errors:
                    for err in audio_analysis_result.errors:
                        audio_logger.warning(f"[{slug}:{episode_id}] Audio analysis warning: {err}")

                # Save audio analysis results to database
                import json
                db.save_episode_audio_analysis(slug, episode_id, json.dumps(audio_analysis_result.to_dict()))
            except Exception as e:
                audio_logger.error(f"[{slug}:{episode_id}] Audio analysis failed: {e}")
                # Continue without audio analysis - it's optional

        try:
            # Step 2: Detect ads (first pass)
            status_service.update_job_stage("detecting", 50)
            ad_result = ad_detector.process_transcript(
                segments, podcast_name, episode_title, slug, episode_id, episode_description,
                audio_analysis=audio_analysis_result,
                audio_path=audio_path,
                skip_patterns=skip_patterns  # Gap 3: 'full' mode skips pattern DB
            )
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
                    podcast_name, episode_title, slug, episode_id, episode_description,
                    audio_analysis=audio_analysis_result,
                    skip_patterns=skip_patterns  # Gap 3: 'full' mode skips pattern DB
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

                # Load user-marked false positives to auto-reject during validation
                false_positive_corrections = db.get_false_positive_corrections(episode_id)
                if false_positive_corrections:
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Loaded {len(false_positive_corrections)} "
                        f"false positive corrections"
                    )

                validator = AdValidator(
                    episode_duration, segments, episode_description,
                    false_positive_corrections=false_positive_corrections
                )
                validation_result = validator.validate(all_ads)

                audio_logger.info(
                    f"[{slug}:{episode_id}] Validation: "
                    f"{validation_result.accepted} accepted, "
                    f"{validation_result.reviewed} review, "
                    f"{validation_result.rejected} rejected"
                )

                # Only remove ACCEPT and REVIEW ads from audio that meet confidence threshold
                # REJECT ads and low-confidence ads stay in audio but are stored for display
                ads_to_remove = []
                low_confidence_count = 0
                for ad in validation_result.ads:
                    validation = ad.get('validation', {})
                    if validation.get('decision') == 'REJECT':
                        ad['was_cut'] = False  # Rejected ads are kept in audio
                        continue
                    # Check confidence - use adjusted_confidence if available, else original
                    confidence = validation.get('adjusted_confidence', ad.get('confidence', 1.0))
                    if confidence < MIN_CUT_CONFIDENCE:
                        low_confidence_count += 1
                        ad['was_cut'] = False  # Low-confidence ads are kept in audio
                        audio_logger.info(
                            f"[{slug}:{episode_id}] Keeping low-confidence ad in audio: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({confidence:.0%} < {MIN_CUT_CONFIDENCE:.0%})"
                        )
                        continue
                    ad['was_cut'] = True  # This ad will be cut from audio
                    ads_to_remove.append(ad)

                # Store ALL ads (including rejected) for API/UI display with was_cut flag
                all_ads_with_validation = validation_result.ads
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

                rejected_count = validation_result.rejected
                if rejected_count > 0 or low_confidence_count > 0:
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Kept in audio: {rejected_count} rejected, "
                        f"{low_confidence_count} low-confidence (<{MIN_CUT_CONFIDENCE:.0%})"
                    )
            else:
                ads_to_remove = []
                all_ads_with_validation = []

            # Step 4: Process audio ONCE with validated ads (excluding rejected)
            status_service.update_job_stage("processing", 80)
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
            # Clear reprocess_mode and reprocess_requested_at after successful processing
            db.upsert_episode(slug, episode_id,
                status='processed',
                processed_file=f"episodes/{episode_id}.mp3",
                original_duration=original_duration,
                new_duration=new_duration,
                ads_removed=len(ads_to_remove),
                ads_removed_firstpass=first_pass_count,
                ads_removed_secondpass=second_pass_count,
                reprocess_mode=None,
                reprocess_requested_at=None)

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

            # Record processing history
            try:
                podcast_data = db.get_podcast_by_slug(slug)
                if podcast_data:
                    db.record_processing_history(
                        podcast_id=podcast_data['id'],
                        podcast_slug=slug,
                        podcast_title=podcast_data.get('title') or podcast_name,
                        episode_id=episode_id,
                        episode_title=episode_title,
                        status='completed',
                        processing_duration_seconds=processing_time,
                        ads_detected=len(ads_to_remove)
                    )
            except Exception as hist_err:
                audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")

            status_service.complete_job()
            return True

        finally:
            # Clean up temp audio file
            if os.path.exists(audio_path):
                os.unlink(audio_path)

    except Exception as e:
        processing_time = time.time() - start_time
        audio_logger.error(f"[{slug}:{episode_id}] Failed: {e} ({processing_time:.1f}s)")

        # Update status to failed with retry count
        status_service.fail_job()

        # Classify error as transient (worth retrying) or permanent
        transient = is_transient_error(e)

        # Get current retry count
        current_retry = (episode_data.get('retry_count', 0) or 0) if episode_data else 0

        # Only increment retry count for transient errors
        if transient:
            new_retry_count = current_retry + 1
            if new_retry_count >= MAX_EPISODE_RETRIES:
                new_status = 'permanently_failed'
                audio_logger.warning(f"[{slug}:{episode_id}] Max retries reached ({MAX_EPISODE_RETRIES}), marking as permanently failed")
            else:
                new_status = 'failed'
                audio_logger.info(f"[{slug}:{episode_id}] Transient error, will retry (attempt {new_retry_count}/{MAX_EPISODE_RETRIES})")
        else:
            # Permanent error - don't retry, mark as permanently failed immediately
            new_status = 'permanently_failed'
            new_retry_count = current_retry  # Don't increment
            audio_logger.warning(f"[{slug}:{episode_id}] Permanent error, not retrying: {type(e).__name__}")

        db.upsert_episode(slug, episode_id,
            status=new_status,
            retry_count=new_retry_count,
            error_message=str(e))

        # Record processing history for failure
        try:
            podcast_data = db.get_podcast_by_slug(slug)
            if podcast_data:
                db.record_processing_history(
                    podcast_id=podcast_data['id'],
                    podcast_slug=slug,
                    podcast_title=podcast_data.get('title') or podcast_name,
                    episode_id=episode_id,
                    episode_title=episode_title,
                    status='failed',
                    processing_duration_seconds=processing_time,
                    ads_detected=0,
                    error_message=str(e)
                )
        except Exception as hist_err:
            audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")

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

    elif status == 'permanently_failed':
        feed_logger.warning(f"[{slug}:{episode_id}] Episode permanently failed, not retrying")
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
        feed_logger.info(f"[{slug}:{episode_id}] Retrying failed episode (attempt {retry_count + 1}/{MAX_EPISODE_RETRIES})")
        status = None

    elif status == 'processing':
        feed_logger.info(f"[{slug}:{episode_id}] Currently processing")
        return Response(
            "Episode is being processed",
            status=503,
            headers={'Retry-After': '30'}
        )

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
    episode_artwork_url = None
    for ep in episodes:
        if ep['id'] == episode_id:
            original_url = ep['url']
            episode_title = ep.get('title', 'Unknown')
            episode_description = ep.get('description')
            episode_artwork_url = ep.get('artwork_url')
            break

    if not original_url:
        feed_logger.error(f"[{slug}:{episode_id}] Episode not found in RSS")
        abort(404)

    # Start background processing (non-blocking)
    started, reason = start_background_processing(
        slug, episode_id, original_url, episode_title,
        podcast_name, episode_description, episode_artwork_url
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
        # Queue is busy with another episode
        feed_logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), redirecting to original")
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

    # Reset any episodes stuck in 'processing' status from previous crash
    reset_stuck_processing_episodes()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
    logger.info("Registered signal handlers for graceful shutdown")

    # Seed sponsor and normalization data (only inserts if table is empty)
    sponsor_service.seed_initial_data()
    logger.info("Sponsor service initialized")

    # Start background RSS refresh thread
    refresh_thread = threading.Thread(target=background_rss_refresh, daemon=True)
    refresh_thread.start()
    logger.info("Started background refresh thread")

    # Start background queue processor thread for auto-processing
    queue_thread = threading.Thread(target=background_queue_processor, daemon=True)
    queue_thread.start()
    logger.info("Started auto-process queue processor thread")

    # Initial RSS refresh
    logger.info("Performing initial RSS refresh")
    feed_map = get_feed_map()
    for slug, feed_info in feed_map.items():
        refresh_rss_feed(slug, feed_info['in'])
        logger.info(f"Feed: {base_url}/{slug}")

    logger.info(f"Web UI available at: {base_url}/ui/")


_startup()

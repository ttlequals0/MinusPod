"""Main Flask web server for podcast ad removal with web UI."""
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from functools import wraps
from flask import Flask, Response, send_file, abort, send_from_directory, request
from flask_cors import CORS
from slugify import slugify
import shutil

from utils.time import parse_timestamp

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

# Import default confidence threshold from centralized config
from config import MIN_CUT_CONFIDENCE


def get_min_cut_confidence() -> float:
    """Get the minimum confidence threshold for cutting ads from audio.

    This is configurable via the 'min_cut_confidence' setting (aggressiveness slider).
    Lower = more aggressive (removes more potential ads)
    Higher = more conservative (removes only high-confidence ads)

    Default value is MIN_CUT_CONFIDENCE from config.py
    """
    try:
        value = db.get_setting('min_cut_confidence')
        if value:
            threshold = float(value)
            # Clamp to valid range
            return max(0.50, min(0.95, threshold))
    except (ValueError, TypeError):
        pass
    return MIN_CUT_CONFIDENCE


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

import requests.exceptions
from llm_client import is_retryable_error, is_llm_api_error, start_episode_token_tracking, get_episode_token_totals


def is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient (worth retrying) or permanent.

    Delegates LLM API error classification to llm_client.is_retryable_error(),
    then applies episode-processing-specific checks for network, OOM, CDN, and
    audio format errors.
    """
    # Network/connection errors are transient
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        ConnectionError,
        TimeoutError,
    )):
        return True

    # Delegate LLM API error checks to the shared classifier
    if is_retryable_error(error):
        return True

    # Known LLM API error that wasn't retryable -- permanent
    if is_llm_api_error(error):
        return False

    # Permanent errors - don't retry
    if isinstance(error, (
        ValueError,
        FileNotFoundError,
        PermissionError,
        TypeError,
    )):
        return False

    # Check error message for patterns
    error_msg = str(error).lower()

    # OOM errors are PERMANENT - retrying without more RAM won't help
    oom_patterns = [
        'out of memory', 'oom', 'cuda out of memory',
        'cannot allocate memory', 'memory allocation failed',
        'killed', 'memoryerror', 'torch.cuda.outofmemoryerror',
    ]
    if any(pattern in error_msg for pattern in oom_patterns):
        return False

    # CDN errors are transient
    transient_patterns = [
        'cdn not ready', 'cdn timeout', 'cdn server error', 'cdn check failed',
    ]
    if any(pattern in error_msg for pattern in transient_patterns):
        return True

    # Permanent content/auth errors
    permanent_patterns = [
        'invalid audio', 'unsupported format', 'corrupt',
        'authentication', 'unauthorized', 'forbidden', 'not found',
        '400 ', '401 ', '403 ', '404 ',
    ]
    if any(pattern in error_msg for pattern in permanent_patterns):
        return False

    # Default: assume transient for unknown errors (safer to retry)
    return True


# Initialize Flask app
app = Flask(__name__)

# Session configuration for authentication
import secrets


def get_or_create_secret_key():
    """Get secret key from database or create and persist one.

    This ensures all Gunicorn workers use the same key, preventing
    session validation failures when requests hit different workers.
    """
    from database import Database
    db = Database()

    secret_key = db.get_setting('flask_secret_key')
    if not secret_key:
        secret_key = secrets.token_hex(32)
        db.set_setting('flask_secret_key', secret_key)
        logger.info("Generated and persisted new Flask secret key")

    return secret_key


# Use environment variable if set, otherwise use persistent database key
app.secret_key = os.environ.get('SECRET_KEY') or get_or_create_secret_key()
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = int(os.environ.get('SESSION_LIFETIME_HOURS', '24')) * 3600

# Enable gzip compression for responses
from flask_compress import Compress
compress = Compress()
app.config['COMPRESS_MIMETYPES'] = [
    'application/json',
    'text/html',
    'text/xml',
    'application/xml',
    'application/rss+xml',
    'text/plain',
]
app.config['COMPRESS_LEVEL'] = 6  # Balance between speed and compression
app.config['COMPRESS_MIN_SIZE'] = 500  # Only compress responses > 500 bytes
compress.init_app(app)

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
from ad_detector import AdDetector, refine_ad_boundaries, snap_early_ads_to_zero, merge_same_sponsor_ads, extend_ad_boundaries_by_content
from ad_validator import AdValidator
from audio_processor import AudioProcessor
from database import Database
from processing_queue import ProcessingQueue
from audio_analysis import AudioAnalyzer
from sponsor_service import SponsorService
from status_service import StatusService
from pattern_service import PatternService
from transcript_generator import TranscriptGenerator
from chapters_generator import ChaptersGenerator

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

# Thread-safe TTL cache for reducing database queries
class TTLCache:
    """Simple thread-safe cache with time-to-live expiration."""

    def __init__(self, ttl_seconds: int = 30):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, key: str):
        """Get cached value if not expired, else return None."""
        with self._lock:
            if key in self._cache:
                value, expires = self._cache[key]
                if time.time() < expires:
                    return value
                del self._cache[key]
        return None

    def set(self, key: str, value):
        """Set cached value with TTL."""
        with self._lock:
            self._cache[key] = (value, time.time() + self._ttl)

    def invalidate(self, key: str = None):
        """Invalidate specific key or entire cache."""
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()


# Initialize caches for performance
_feed_cache = TTLCache(ttl_seconds=30)
_settings_cache = TTLCache(ttl_seconds=60)
_parsed_feeds_cache = TTLCache(ttl_seconds=60)


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
    """Get feed map from database, with TTL caching."""
    cached = _feed_cache.get('all_feeds')
    if cached is not None:
        return cached

    feeds = db.get_feeds_config()
    result = {slugify(feed['out'].strip('/')): feed for feed in feeds}
    _feed_cache.set('all_feeds', result)
    return result


def invalidate_feed_cache():
    """Invalidate feed cache after any feed modification."""
    _feed_cache.invalidate('all_feeds')


def get_parsed_feed(slug: str, source_url: str):
    """Get cached parsed feed or parse fresh.

    Reduces redundant RSS fetching and parsing by caching parsed feeds
    for 60 seconds.
    """
    cached = _parsed_feeds_cache.get(slug)
    if cached is not None:
        refresh_logger.debug(f"[{slug}] Using cached parsed feed")
        return cached

    feed_content = rss_parser.fetch_feed(source_url)
    if feed_content:
        parsed = rss_parser.parse_feed(feed_content)
        _parsed_feeds_cache.set(slug, parsed)
        return parsed
    return None


def refresh_rss_feed(slug: str, feed_url: str, force: bool = False):
    """Refresh RSS feed for a podcast.

    Args:
        slug: Podcast slug
        feed_url: URL of the original RSS feed
        force: If True, bypass conditional GET (ETag/Last-Modified) to force full fetch.
               Use this when the RSS cache was deleted and needs regeneration.
    """
    try:
        # Get podcast name and etag for conditional fetch
        podcast = db.get_podcast_by_slug(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # Track feed refresh in status service
        status_service.start_feed_refresh(slug, podcast_name)

        refresh_logger.info(f"[{slug}] Starting RSS refresh from: {feed_url}")

        # Fetch original RSS with conditional GET (ETag/Last-Modified)
        # Skip conditional GET if force=True (cache was deleted, need full content)
        existing_etag = None if force else (podcast.get('etag') if podcast else None)
        existing_last_modified = None if force else (podcast.get('last_modified_header') if podcast else None)

        feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
            feed_url,
            etag=existing_etag,
            last_modified=existing_last_modified
        )

        # Handle 304 Not Modified - feed hasn't changed
        if feed_content is None and (new_etag or new_last_modified):
            refresh_logger.info(f"[{slug}] Feed unchanged (304), skipping refresh")
            status_service.complete_feed_refresh(slug, 0)
            return True

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
                last_checked_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            )

            # Update ETag for conditional GET on next refresh
            if new_etag or new_last_modified:
                db.update_podcast_etag(slug, new_etag, new_last_modified)

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
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=48)

            for ep in episodes:
                # Check if episode already exists in database
                existing = db.get_episode(slug, ep['id'])
                if existing is None:
                    # Also check by title+pubDate to catch ID changes (Megaphone feeds, etc.)
                    # This prevents duplicate processing when RSS GUID changes
                    published_str = ep.get('published', '')
                    iso_published = None
                    if published_str:
                        try:
                            parsed_pub = parsedate_to_datetime(published_str)
                            iso_published = parsed_pub.strftime('%Y-%m-%dT%H:%M:%SZ')
                        except (ValueError, TypeError):
                            pass

                    if iso_published and ep.get('title'):
                        existing_by_title = db.get_episode_by_title_and_date(
                            slug, ep.get('title'), iso_published
                        )
                        if existing_by_title:
                            refresh_logger.warning(
                                f"[{slug}] Episode ID changed: {existing_by_title['episode_id']} -> {ep['id']}, "
                                f"title: {ep.get('title')}"
                            )
                            continue  # Skip - episode already exists with different ID

                    # Parse publish date to check if recent
                    is_recent = False
                    if published_str:
                        try:
                            # RSS dates are typically RFC 2822 format
                            pub_date = parsedate_to_datetime(published_str)
                            # Ensure timezone-aware for comparison
                            if pub_date.tzinfo is None:
                                pub_date = pub_date.replace(tzinfo=timezone.utc)
                            is_recent = pub_date >= cutoff_time
                        except (ValueError, TypeError):
                            # If we can't parse the date, skip this episode for auto-process
                            refresh_logger.debug(f"[{slug}] Could not parse date for episode: {ep.get('title')}")
                            is_recent = False

                    if is_recent:
                        # New recent episode - queue for processing
                        # iso_published already calculated above for deduplication check
                        queue_id = db.queue_episode_for_processing(
                            slug, ep['id'], ep['url'], ep.get('title'), iso_published,
                            ep.get('description')
                        )
                        if queue_id:
                            queued_count += 1
                            refresh_logger.debug(f"[{slug}] Queued recent episode: {ep.get('title')}")

            if queued_count > 0:
                refresh_logger.info(f"[{slug}] Queued {queued_count} new episode(s) for auto-processing")

        # Modify feed URLs (pass storage to include Podcasting 2.0 tags)
        modified_rss = rss_parser.modify_feed(feed_content, slug, storage=storage)

        # Save modified RSS
        storage.save_rss(slug, modified_rss)

        # Update last_checked timestamp
        db.update_podcast(slug, last_checked_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))

        refresh_logger.info(f"[{slug}] RSS refresh complete")
        status_service.complete_feed_refresh(slug, 0)
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

    # Periodic search index rebuild (every 6 hours)
    try:
        last_rebuild = getattr(run_cleanup, '_last_index_rebuild', 0)
        if time.time() - last_rebuild > 21600:
            count = db.rebuild_search_index()
            run_cleanup._last_index_rebuild = time.time()
            refresh_logger.info(f"Periodic search index rebuild: {count} items indexed")
    except Exception as e:
        refresh_logger.error(f"Search index rebuild failed: {e}")


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
    orphan_check_interval = 0  # Counter for orphan check (every 10 iterations)
    while not shutdown_event.is_set():
        try:
            # Periodically check for orphaned queue items (every ~5 minutes)
            orphan_check_interval += 1
            if orphan_check_interval >= 10:
                orphan_check_interval = 0
                reset_count, failed_count = db.reset_orphaned_queue_items(stuck_minutes=65)
                if reset_count > 0 or failed_count > 0:
                    refresh_logger.info(f"Reset {reset_count} orphaned queue items, {failed_count} exceeded max attempts")

                retry_count = db.reset_failed_queue_items(max_retries=MAX_EPISODE_RETRIES)
                if retry_count > 0:
                    refresh_logger.info(f"Reset {retry_count} failed queue items for automatic retry")

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
                description = queued.get('description')

                refresh_logger.info(f"[{slug}:{episode_id}] Auto-processing queued episode: {title}")

                try:
                    # Try to start background processing using the existing queue
                    started, reason = start_background_processing(
                        slug, episode_id, original_url, title, podcast_name, description, None, published_at
                    )

                    if started:
                        # Only mark as processing AFTER we successfully acquired the lock
                        db.update_queue_status(queue_id, 'processing')
                        # Reset backoff on successful start
                        backoff_seconds = 30
                        # Wait for processing to complete (poll status)
                        max_wait = 3600  # 60 minutes max (match MAX_JOB_DURATION)
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
                        elif episode and episode['status'] == 'processing':
                            # Still processing after timeout - don't mark as failed, let it continue
                            # Put back in queue to check again later
                            db.update_queue_status(queue_id, 'pending')
                            refresh_logger.info(f"[{slug}:{episode_id}] Still processing after {max_wait}s, will check again later")
                        else:
                            # Actually failed - get the real error message
                            error_msg = episode.get('error_message') if episode else None
                            if not error_msg:
                                error_msg = f"Processing ended with status: {episode.get('status') if episode else 'unknown'}"
                            db.update_queue_status(queue_id, 'failed', error_msg)
                            episode_status = episode.get('status') if episode else None
                            if episode_status == 'permanently_failed':
                                refresh_logger.warning(f"[{slug}:{episode_id}] Auto-process permanently failed: {error_msg}")
                            else:
                                refresh_logger.info(f"[{slug}:{episode_id}] Auto-process failed (transient), will auto-retry: {error_msg}")
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
    """Reset any episodes stuck in 'processing' status from previous crash.

    Only resets episodes that have been processing for longer than 30 minutes
    to avoid killing actively-processing jobs when a worker restarts.

    Tracks retry count and marks episodes as permanently_failed after MAX_EPISODE_RETRIES
    to prevent infinite retry loops for episodes that consistently crash workers.
    """
    conn = db.get_connection()
    cursor = conn.execute(
        """SELECT e.id, e.episode_id, e.retry_count, p.slug
           FROM episodes e
           JOIN podcasts p ON e.podcast_id = p.id
           WHERE e.status = 'processing'
             AND datetime(e.updated_at) < datetime('now', '-30 minutes')"""
    )
    stuck = cursor.fetchall()

    reset_count = 0
    failed_count = 0

    for row in stuck:
        current_retry_count = row['retry_count'] or 0
        new_retry_count = current_retry_count + 1

        if new_retry_count >= MAX_EPISODE_RETRIES:
            # Too many retries - mark as permanently failed
            refresh_logger.warning(
                f"Marking episode as permanently_failed after {new_retry_count} crashes: "
                f"{row['slug']}/{row['episode_id']}"
            )
            conn.execute(
                """UPDATE episodes SET
                   status = 'permanently_failed',
                   retry_count = ?,
                   error_message = 'Exceeded retry limit after repeated processing crashes'
                   WHERE id = ?""",
                (new_retry_count, row['id'])
            )
            failed_count += 1
        else:
            # Still have retries left - reset to pending
            refresh_logger.warning(
                f"Resetting stuck episode (attempt {new_retry_count}/{MAX_EPISODE_RETRIES}): "
                f"{row['slug']}/{row['episode_id']}"
            )
            conn.execute(
                """UPDATE episodes SET
                   status = 'pending',
                   retry_count = ?,
                   error_message = 'Reset after restart (retry attempt)'
                   WHERE id = ?""",
                (new_retry_count, row['id'])
            )
            reset_count += 1

    conn.commit()

    if stuck:
        refresh_logger.info(
            f"Stuck episode cleanup: {reset_count} reset to pending, "
            f"{failed_count} marked permanently_failed"
        )


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

    # Update StatusService IMMEDIATELY after lock acquired (prevents race condition)
    # This ensures the new episode is tracked before any other episode can start
    status_service.start_job(slug, episode_id, title, podcast_name)

    # Start background thread
    processing_thread = threading.Thread(
        target=_process_episode_background,
        args=(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at),
        daemon=True
    )
    processing_thread.start()

    return True, "started"


def _download_and_transcribe(slug, episode_id, episode_url, podcast_name):
    """Pipeline stage: Download audio and get/create transcript segments.

    Returns (audio_path, segments) or raises on failure.
    """
    segments = None
    transcript_text = storage.get_transcript(slug, episode_id)

    if transcript_text:
        audio_logger.info(f"[{slug}:{episode_id}] Found existing transcript in database")
        segments = []
        for line in transcript_text.split('\n'):
            if line.strip() and line.startswith('['):
                try:
                    time_part, text_part = line.split('] ', 1)
                    time_range = time_part.strip('[')
                    start_str, end_str = time_range.split(' --> ')
                    segments.append({
                        'start': parse_timestamp(start_str),
                        'end': parse_timestamp(end_str),
                        'text': text_part
                    })
                except (ValueError, TypeError):
                    continue

        if segments:
            duration_min = segments[-1]['end'] / 60
            audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(segments)} segments, {duration_min:.1f} min")

        available, cdn_error = transcriber.check_audio_availability(episode_url)
        if not available:
            raise Exception(f"CDN not ready: {cdn_error}")

        audio_path = transcriber.download_audio(episode_url)
        if not audio_path:
            raise Exception("Failed to download audio")
    else:
        available, cdn_error = transcriber.check_audio_availability(episode_url)
        if not available:
            raise Exception(f"CDN not ready: {cdn_error}")

        audio_logger.info(f"[{slug}:{episode_id}] Downloading audio")
        audio_path = transcriber.download_audio(episode_url)
        if not audio_path:
            raise Exception("Failed to download audio")

        status_service.update_job_stage("pass1:transcribing", 20)
        audio_logger.info(f"[{slug}:{episode_id}] Starting transcription")
        segments = transcriber.transcribe_chunked(audio_path, podcast_name=podcast_name)
        if not segments:
            raise Exception("Failed to transcribe audio")

        duration_min = segments[-1]['end'] / 60 if segments else 0
        audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

        transcript_text = transcriber.segments_to_text(segments)
        storage.save_transcript(slug, episode_id, transcript_text)

    return audio_path, segments


def _run_audio_analysis(slug, episode_id, audio_path, segments):
    """Pipeline stage: Run volume + transition detection on audio."""
    status_service.update_job_stage("pass1:analyzing", 25)
    audio_logger.info(f"[{slug}:{episode_id}] Running audio analysis")
    try:
        result = audio_analyzer.analyze(
            audio_path,
            transcript_segments=segments,
            status_callback=lambda stage, progress: status_service.update_job_stage(stage, progress)
        )
        if result.signals:
            audio_logger.info(
                f"[{slug}:{episode_id}] Audio analysis: {len(result.signals)} signals "
                f"in {result.analysis_time_seconds:.1f}s"
            )
        if result.errors:
            for err in result.errors:
                audio_logger.warning(f"[{slug}:{episode_id}] Audio analysis warning: {err}")

        import json as _json
        db.save_episode_audio_analysis(slug, episode_id, _json.dumps(result.to_dict()))
        return result
    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Audio analysis failed: {e}")
        return None


def _detect_ads_first_pass(slug, episode_id, segments, audio_path,
                            episode_description, podcast_description,
                            skip_patterns, audio_analysis_result,
                            podcast_name, episode_title,
                            progress_callback):
    """Pipeline stage: Run first-pass Claude ad detection.

    Returns (first_pass_ads, first_pass_count, ad_result).
    """
    status_service.update_job_stage("pass1:detecting", 50)
    ad_result = ad_detector.process_transcript(
        segments, podcast_name, episode_title, slug, episode_id, episode_description,
        audio_path=audio_path,
        podcast_id=slug,
        skip_patterns=skip_patterns,
        podcast_description=podcast_description,
        progress_callback=progress_callback,
        audio_analysis=audio_analysis_result
    )
    storage.save_ads_json(slug, episode_id, ad_result, pass_number=1)

    ad_detection_status = ad_result.get('status', 'success')
    first_pass_ads = ad_result.get('ads', [])

    if ad_detection_status == 'failed':
        error_msg = ad_result.get('error', 'Unknown error')
        audio_logger.error(f"[{slug}:{episode_id}] Ad detection failed: {error_msg}")
        db.upsert_episode(slug, episode_id, ad_detection_status='failed')
        raise Exception(f"Ad detection failed: {error_msg}")

    db.upsert_episode(slug, episode_id, ad_detection_status='success')

    if first_pass_ads:
        total_ad_time = sum(ad['end'] - ad['start'] for ad in first_pass_ads)
        audio_logger.info(f"[{slug}:{episode_id}] First pass: Detected {len(first_pass_ads)} ads ({total_ad_time/60:.1f} min)")
    else:
        audio_logger.info(f"[{slug}:{episode_id}] First pass: No ads detected")

    return first_pass_ads, len(first_pass_ads), ad_result


def _refine_and_validate(slug, episode_id, all_ads, segments, audio_path,
                          episode_description, episode_duration, min_cut_confidence,
                          podcast_name):
    """Pipeline stage: Refine ad boundaries, detect rolls, validate, gate by confidence.

    Returns (ads_to_remove, all_ads_with_validation).
    """
    # Boundary refinement
    if all_ads and segments:
        all_ads = refine_ad_boundaries(all_ads, segments)
    if all_ads and segments:
        all_ads = extend_ad_boundaries_by_content(all_ads, segments)
    if all_ads:
        all_ads = snap_early_ads_to_zero(all_ads)
    if all_ads and segments:
        all_ads = merge_same_sponsor_ads(all_ads, segments)

    # Heuristic pre/post-roll detection
    if segments:
        from roll_detector import detect_preroll, detect_postroll
        preroll_ad = detect_preroll(segments, all_ads, podcast_name=podcast_name)
        if preroll_ad:
            all_ads.append(preroll_ad)
            audio_logger.info(f"[{slug}:{episode_id}] Heuristic pre-roll: 0.0s-{preroll_ad['end']:.1f}s")

        postroll_ad = detect_postroll(segments, all_ads, episode_duration=episode_duration)
        if postroll_ad:
            all_ads.append(postroll_ad)
            audio_logger.info(f"[{slug}:{episode_id}] Heuristic post-roll: {postroll_ad['start']:.1f}s-{postroll_ad['end']:.1f}s")

    # Validation
    if not all_ads:
        return [], []

    false_positive_corrections = db.get_false_positive_corrections(episode_id)
    if false_positive_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(false_positive_corrections)} false positive corrections")

    confirmed_corrections = db.get_confirmed_corrections(episode_id)
    if confirmed_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(confirmed_corrections)} confirmed corrections")

    validator = AdValidator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence
    )
    validation_result = validator.validate(all_ads)

    audio_logger.info(
        f"[{slug}:{episode_id}] Validation: "
        f"{validation_result.accepted} accepted, "
        f"{validation_result.reviewed} review, "
        f"{validation_result.rejected} rejected"
    )

    # Confidence gating: ACCEPT = cut, REJECT = keep, REVIEW = threshold check
    ads_to_remove = []
    low_confidence_count = 0
    for ad in validation_result.ads:
        validation = ad.get('validation', {})
        decision = validation.get('decision')
        if decision == 'REJECT':
            ad['was_cut'] = False
            continue
        if decision == 'ACCEPT':
            ad['was_cut'] = True
            ads_to_remove.append(ad)
            continue
        confidence = validation.get('adjusted_confidence', ad.get('confidence', 1.0))
        if confidence < min_cut_confidence:
            low_confidence_count += 1
            ad['was_cut'] = False
            audio_logger.info(
                f"[{slug}:{episode_id}] Keeping REVIEW ad in audio: "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s ({confidence:.0%} < {min_cut_confidence:.0%})"
            )
            continue
        ad['was_cut'] = True
        ads_to_remove.append(ad)

    all_ads_with_validation = validation_result.ads
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

    # Learn patterns from cut ads
    cut_ads = [a for a in all_ads_with_validation if a.get('was_cut')]
    if cut_ads and slug:
        patterns_learned = ad_detector._learn_from_detections(
            cut_ads, segments, slug, episode_id, audio_path=audio_path
        )
        if patterns_learned > 0:
            audio_logger.info(f"[{slug}:{episode_id}] Learned {patterns_learned} new patterns from cut ads")

    rejected_count = validation_result.rejected
    if rejected_count > 0 or low_confidence_count > 0:
        audio_logger.info(
            f"[{slug}:{episode_id}] Kept in audio: {rejected_count} rejected, "
            f"{low_confidence_count} low-confidence (<{min_cut_confidence:.0%})"
        )

    return ads_to_remove, all_ads_with_validation


def _run_verification_pass(slug, episode_id, processed_path, ads_to_remove,
                            podcast_name, episode_title, episode_description,
                            podcast_description, skip_patterns, min_cut_confidence,
                            local_audio_processor, progress_callback):
    """Pipeline stage: Run verification (second pass) on processed audio.

    Returns (verification_count, v_ads_for_ui, processed_path).
    """
    verification_count = 0
    v_ads_for_ui = []

    try:
        from verification_pass import VerificationPass
        verifier = VerificationPass(
            ad_detector=ad_detector, transcriber=transcriber,
            audio_analyzer=audio_analyzer, sponsor_service=sponsor_service, db=db,
        )
        verification_result = verifier.verify(
            processed_audio_path=processed_path,
            podcast_name=podcast_name, episode_title=episode_title,
            slug=slug, episode_id=episode_id,
            pass1_cuts=ads_to_remove,
            episode_description=episode_description,
            podcast_description=podcast_description,
            skip_patterns=skip_patterns,
            progress_callback=progress_callback,
        )
        verification_ads_original = verification_result.get('ads', [])
        verification_ads_processed = verification_result.get('ads_processed', [])
        verification_segments = verification_result.get('segments', [])
        storage.save_ads_json(slug, episode_id, verification_result, pass_number=2)

        # Heuristic roll detection on pass 2
        if verification_segments:
            from roll_detector import detect_preroll, detect_postroll
            from verification_pass import _build_timestamp_map, _map_to_original

            processed_dur = verification_segments[-1]['end'] if verification_segments else 0
            ts_map = _build_timestamp_map(ads_to_remove) if ads_to_remove else None

            preroll_v = detect_preroll(verification_segments, verification_ads_processed, podcast_name=podcast_name)
            if preroll_v:
                verification_ads_processed.append(preroll_v)
                mapped = preroll_v.copy()
                if ts_map:
                    mapped['start'] = _map_to_original(preroll_v['start'], ts_map)
                    mapped['end'] = _map_to_original(preroll_v['end'], ts_map)
                verification_ads_original.append(mapped)
                audio_logger.info(f"[{slug}:{episode_id}] Pass 2 heuristic pre-roll: 0.0s-{preroll_v['end']:.1f}s")

            postroll_v = detect_postroll(verification_segments, verification_ads_processed, episode_duration=processed_dur)
            if postroll_v:
                verification_ads_processed.append(postroll_v)
                mapped = postroll_v.copy()
                if ts_map:
                    mapped['start'] = _map_to_original(postroll_v['start'], ts_map)
                    mapped['end'] = _map_to_original(postroll_v['end'], ts_map)
                verification_ads_original.append(mapped)
                audio_logger.info(f"[{slug}:{episode_id}] Pass 2 heuristic post-roll: {postroll_v['start']:.1f}s-{postroll_v['end']:.1f}s")

        if verification_ads_processed:
            audio_logger.info(f"[{slug}:{episode_id}] Verification found {len(verification_ads_processed)} missed ads - re-cutting pass 1 output")

            # Validate verification ads
            if verification_segments:
                processed_duration = verification_segments[-1]['end']
                v_validator = AdValidator(processed_duration, verification_segments,
                                         episode_description, min_cut_confidence=min_cut_confidence)
                v_validation = v_validator.validate(verification_ads_processed)

                keep_indices = {idx for idx, ad in enumerate(v_validation.ads)
                                if ad.get('validation', {}).get('decision') != 'REJECT'}
                verification_ads_processed = [ad for idx, ad in enumerate(v_validation.ads) if idx in keep_indices]
                verification_ads_original = [ad for idx, ad in enumerate(verification_ads_original) if idx in keep_indices]

            if verification_ads_processed:
                # Confidence gate and re-cut
                v_ads_to_cut = []
                for i, ad in enumerate(verification_ads_processed):
                    confidence = ad.get('validation', {}).get('adjusted_confidence', ad.get('confidence', 1.0))
                    if confidence >= min_cut_confidence:
                        ad['was_cut'] = True
                        ad['detection_stage'] = 'verification'
                        v_ads_to_cut.append(ad)
                        orig_ad = verification_ads_original[i]
                        orig_ad['was_cut'] = True
                        orig_ad['detection_stage'] = 'verification'
                        v_ads_for_ui.append(orig_ad)
                    else:
                        ad['was_cut'] = False

                if v_ads_to_cut:
                    recut_path = local_audio_processor.process_episode(processed_path, v_ads_to_cut)
                    if recut_path:
                        if os.path.exists(processed_path):
                            os.unlink(processed_path)
                        processed_path = recut_path
                        verification_count = len(v_ads_to_cut)
                        audio_logger.info(f"[{slug}:{episode_id}] Re-cut pass 1 output, removed {len(v_ads_to_cut)} additional ads")
                    else:
                        audio_logger.error(f"[{slug}:{episode_id}] Verification re-cut failed, keeping pass 1 output")
                        v_ads_for_ui = []
        else:
            audio_logger.info(f"[{slug}:{episode_id}] Verification: clean")

    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Verification pass failed: {e}")

    return verification_count, v_ads_for_ui, processed_path


def _generate_assets(slug, episode_id, segments, all_cuts, episode_description,
                      podcast_name, episode_title):
    """Pipeline stage: Generate VTT transcript and chapters."""
    try:
        vtt_enabled = db.get_setting('vtt_transcripts_enabled')
        transcript_gen = TranscriptGenerator()

        if vtt_enabled is None or vtt_enabled.lower() == 'true':
            vtt_content = transcript_gen.generate_vtt(segments, all_cuts)
            if vtt_content and len(vtt_content) > 10:
                storage.save_transcript_vtt(slug, episode_id, vtt_content)
                audio_logger.info(f"[{slug}:{episode_id}] Generated VTT transcript")

        processed_text = transcript_gen.generate_text(segments, all_cuts)
        if processed_text:
            db.save_episode_details(slug, episode_id, transcript_text=processed_text)

        chapters_enabled = db.get_setting('chapters_enabled')
        if chapters_enabled is None or chapters_enabled.lower() == 'true':
            chapters_gen = ChaptersGenerator()
            chapters = chapters_gen.generate_chapters(
                segments, all_cuts, episode_description,
                podcast_name, episode_title
            )
            if chapters and chapters.get('chapters'):
                storage.save_chapters_json(slug, episode_id, chapters)
                audio_logger.info(f"[{slug}:{episode_id}] Generated {len(chapters['chapters'])} chapters")
    except Exception as e:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to generate Podcasting 2.0 assets: {e}")


def _finalize_episode(slug, episode_id, episode_title, podcast_name,
                       ads_to_remove, verification_count, first_pass_count,
                       original_duration, new_duration, start_time):
    """Pipeline stage: Update DB, record history, refresh RSS."""
    db.upsert_episode(slug, episode_id,
        status='processed',
        processed_file=f"episodes/{episode_id}.mp3",
        original_duration=original_duration,
        new_duration=new_duration,
        ads_removed=len(ads_to_remove) + verification_count,
        ads_removed_firstpass=first_pass_count,
        ads_removed_secondpass=verification_count,
        reprocess_mode=None,
        reprocess_requested_at=None)

    try:
        db.index_episode(episode_id, slug)
    except Exception as idx_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to update search index: {idx_err}")

    try:
        feed_map = get_feed_map()
        if slug in feed_map:
            refresh_rss_feed(slug, feed_map[slug]['in'], force=True)
    except Exception as cache_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to regenerate RSS cache: {cache_err}")

    processing_time = time.time() - start_time

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

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    try:
        podcast_data = db.get_podcast_by_slug(slug)
        if podcast_data:
            db.record_processing_history(
                podcast_id=podcast_data['id'], podcast_slug=slug,
                podcast_title=podcast_data.get('title') or podcast_name,
                episode_id=episode_id, episode_title=episode_title,
                status='completed', processing_duration_seconds=processing_time,
                ads_detected=len(ads_to_remove),
                input_tokens=token_totals['input_tokens'],
                output_tokens=token_totals['output_tokens'],
                llm_cost=token_totals['cost'],
            )
    except Exception as hist_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")


def _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                episode_data, error, start_time):
    """Handle processing failure: GPU cleanup, retry logic, error recording."""
    processing_time = time.time() - start_time
    audio_logger.error(f"[{slug}:{episode_id}] Failed: {error} ({processing_time:.1f}s)")

    try:
        from transcriber import WhisperModelSingleton
        from utils.gpu import clear_gpu_memory
        clear_gpu_memory()
        WhisperModelSingleton.unload_model()
        audio_logger.info(f"[{slug}:{episode_id}] Cleaned up GPU memory after failure")
    except Exception as cleanup_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to clean up GPU memory: {cleanup_err}")

    status_service.fail_job()

    transient = is_transient_error(error)
    current_retry = (episode_data.get('retry_count', 0) or 0) if episode_data else 0

    if transient:
        new_retry_count = current_retry + 1
        if new_retry_count >= MAX_EPISODE_RETRIES:
            new_status = 'permanently_failed'
            audio_logger.warning(f"[{slug}:{episode_id}] Max retries reached ({MAX_EPISODE_RETRIES}), marking as permanently failed")
        else:
            new_status = 'failed'
            audio_logger.info(f"[{slug}:{episode_id}] Transient error, will retry (attempt {new_retry_count}/{MAX_EPISODE_RETRIES})")
    else:
        new_status = 'permanently_failed'
        new_retry_count = current_retry
        audio_logger.warning(f"[{slug}:{episode_id}] Permanent error, not retrying: {type(error).__name__}")

    db.upsert_episode(slug, episode_id, status=new_status,
        retry_count=new_retry_count, error_message=str(error))

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    try:
        podcast_data = db.get_podcast_by_slug(slug)
        if podcast_data:
            db.record_processing_history(
                podcast_id=podcast_data['id'], podcast_slug=slug,
                podcast_title=podcast_data.get('title') or podcast_name,
                episode_id=episode_id, episode_title=episode_title,
                status='failed', processing_duration_seconds=processing_time,
                ads_detected=0, error_message=str(error),
                input_tokens=token_totals['input_tokens'],
                output_tokens=token_totals['output_tokens'],
                llm_cost=token_totals['cost'],
            )
    except Exception as hist_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")


def process_episode(slug: str, episode_id: str, episode_url: str,
                   episode_title: str = "Unknown", podcast_name: str = "Unknown",
                   episode_description: str = None, episode_artwork_url: str = None,
                   episode_published_at: str = None):
    """Process a single episode through the full ad removal pipeline.

    Pipeline stages:
    1. Download audio and transcribe (or load existing transcript)
    2. Audio analysis (volume + transition detection)
    3. First-pass ad detection via Claude
    4. Boundary refinement, roll detection, validation
    5. Audio processing (FFMPEG cut)
    6. Verification pass (second-pass detection on processed audio)
    7. Generate Podcasting 2.0 assets (VTT transcript, chapters)
    8. Finalize (update DB, record history, refresh RSS)
    """
    start_time = time.time()
    start_episode_token_tracking()

    episode_data = db.get_episode(slug, episode_id)
    reprocess_mode = episode_data.get('reprocess_mode') if episode_data else None
    skip_patterns = reprocess_mode == 'full'

    if reprocess_mode:
        audio_logger.info(f"[{slug}:{episode_id}] Reprocess mode: {reprocess_mode} (skip_patterns={skip_patterns})")

    podcast_settings = db.get_podcast_by_slug(slug)
    podcast_description = podcast_settings.get('description') if podcast_settings else None

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Starting: \"{episode_title}\"")
        min_cut_confidence = get_min_cut_confidence()
        audio_logger.info(f"[{slug}:{episode_id}] Confidence threshold: {min_cut_confidence:.0%}")

        status_service.start_job(slug, episode_id, episode_title, podcast_name)
        status_service.update_job_stage("downloading", 0)

        db.upsert_episode(slug, episode_id,
            original_url=episode_url, title=episode_title,
            description=episode_description, artwork_url=episode_artwork_url,
            published_at=episode_published_at, status='processing')

        # Stage 1: Download and transcribe
        audio_path, segments = _download_and_transcribe(slug, episode_id, episode_url, podcast_name)

        try:
            # Stage 2: Audio analysis
            audio_analysis_result = _run_audio_analysis(slug, episode_id, audio_path, segments)

            # Progress callback for detection stages
            current_pass = "pass1"
            def detection_progress_callback(stage, percent):
                status_service.update_job_stage(f"{current_pass}:{stage}", percent)

            # Stage 3: First-pass detection
            first_pass_ads, first_pass_count, ad_result = _detect_ads_first_pass(
                slug, episode_id, segments, audio_path,
                episode_description, podcast_description,
                skip_patterns, audio_analysis_result,
                podcast_name, episode_title, detection_progress_callback
            )

            all_ads = first_pass_ads.copy()

            # Stage 4: Refine and validate
            episode_duration = audio_processor.get_audio_duration(audio_path)
            if not episode_duration:
                episode_duration = segments[-1]['end'] if segments else 0

            ads_to_remove, all_ads_with_validation = _refine_and_validate(
                slug, episode_id, all_ads, segments, audio_path,
                episode_description, episode_duration, min_cut_confidence, podcast_name
            )

            # Stage 5: Process audio
            status_service.update_job_stage("pass1:processing", 80)
            audio_logger.info(f"[{slug}:{episode_id}] Starting FFMPEG processing ({len(ads_to_remove)} ads to remove)")

            settings = db.get_all_settings()
            bitrate = settings.get('audio_bitrate', {}).get('value', '128k')
            local_audio_processor = AudioProcessor(bitrate=bitrate)

            processed_path = local_audio_processor.process_episode(audio_path, ads_to_remove)
            if not processed_path:
                raise Exception("Failed to process audio with FFMPEG")

            original_duration = local_audio_processor.get_audio_duration(audio_path)

            # Stage 6: Verification pass
            current_pass = "pass2"
            verification_count, v_ads_for_ui, processed_path = _run_verification_pass(
                slug, episode_id, processed_path, ads_to_remove,
                podcast_name, episode_title, episode_description,
                podcast_description, skip_patterns, min_cut_confidence,
                local_audio_processor, detection_progress_callback
            )

            # Merge pass 2 ads into combined list for UI
            if v_ads_for_ui:
                all_ads_with_validation = list(all_ads_with_validation) + v_ads_for_ui
                all_ads_with_validation.sort(key=lambda x: x['start'])
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

            new_duration = local_audio_processor.get_audio_duration(processed_path)

            # Move processed file to final location
            final_path = storage.get_episode_path(slug, episode_id)
            shutil.move(processed_path, final_path)

            # Stage 7: Generate assets
            all_cuts_for_assets = ads_to_remove + v_ads_for_ui
            _generate_assets(slug, episode_id, segments, all_cuts_for_assets,
                              episode_description, podcast_name, episode_title)

            # Stage 8: Finalize
            _finalize_episode(slug, episode_id, episode_title, podcast_name,
                               ads_to_remove, verification_count, first_pass_count,
                               original_duration, new_duration, start_time)

            status_service.complete_job()
            return True

        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)

    except Exception as e:
        _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                    episode_data, e, start_time)
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
    force_refresh = False  # Force full fetch bypasses 304 - use when cache is missing
    if not cached_rss:
        should_refresh = True
        force_refresh = True  # No cache, must get full content (can't use 304)
        feed_logger.info(f"[{slug}] No RSS cache, refreshing")
    elif last_checked:
        try:
            last_time = datetime.fromisoformat(last_checked.replace('Z', '+00:00'))
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
        # Queue is busy with another episode - queue this one and return 503
        status_service.queue_episode(slug, episode_id, episode_title, podcast_name)
        queue_position = status_service.get_queue_position(slug, episode_id)
        feed_logger.info(f"[{slug}:{episode_id}] Queue busy ({reason}), queued at position {queue_position}")
        return Response(
            _json.dumps({
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
    # Validate episode ID
    if not all(c.isalnum() or c in '-_' for c in episode_id):
        feed_logger.warning(f"[{slug}] Invalid episode ID for chapters: {episode_id}")
        abort(400)

    chapters = storage.get_chapters_json(slug, episode_id)
    if not chapters:
        feed_logger.info(f"[{slug}:{episode_id}] Chapters not found")
        abort(404)

    import json
    feed_logger.info(f"[{slug}:{episode_id}] Serving chapters JSON")
    response = Response(json.dumps(chapters), mimetype='application/json+chapters')
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


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
        logger.info(f"MinusPod v{__version__} starting...")
    except ImportError:
        logger.warning("Could not import version")

    base_url = os.getenv('BASE_URL', 'http://localhost:8000')
    logger.info(f"BASE_URL: {base_url}")

    # Verify LLM endpoint is reachable (important for openai-compatible providers)
    try:
        from llm_client import verify_llm_connection
        verify_llm_connection()
    except Exception as e:
        logger.warning(f"LLM verification skipped: {e}")

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

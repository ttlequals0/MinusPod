"""Main Flask web server for podcast ad removal with web UI."""
import fcntl
import json
import logging
import os
import secrets
import signal
import sys
import threading
from pathlib import Path

from flask import Flask
from flask_cors import CORS
from flask_compress import Compress

# Configure structured logging
_logging_configured = False


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

        return json.dumps(log_data)


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
                 'podcast.transcribe', 'podcast.claude', 'podcast.refresh',
                 'podcast.llm_io']:
        logging.getLogger(name).setLevel(getattr(logging, log_level, logging.INFO))


setup_logging()
logger = logging.getLogger('podcast.app')
audio_logger = logging.getLogger('podcast.audio')

# Import components
from storage import Storage
from rss_parser import RSSParser
from transcriber import Transcriber
from ad_detector import AdDetector
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

# Graceful shutdown support
shutdown_event = threading.Event()
processing_queue = ProcessingQueue()

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


def get_or_create_secret_key():
    """Get secret key from database or create and persist one.

    This ensures all Gunicorn workers use the same key, preventing
    session validation failures when requests hit different workers.
    """
    from database import Database
    _db = Database()

    secret_key = _db.get_setting('flask_secret_key')
    if not secret_key:
        secret_key = secrets.token_hex(32)
        _db.set_setting('flask_secret_key', secret_key)
        logger.info("Generated and persisted new Flask secret key")

    return secret_key


def graceful_shutdown(signum, frame):
    """Handle shutdown signals gracefully.

    Sets the shutdown event to signal background threads to stop.
    Does NOT block the signal handler -- Gunicorn's --graceful-timeout (330s)
    provides the actual wait period before SIGKILL. Blocking here would prevent
    gthread worker heartbeats, causing premature SIGKILL after --timeout.
    """
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name} signal, initiating graceful shutdown...")

    # Signal all background threads to stop
    shutdown_event.set()

    current = processing_queue.get_current()
    if current:
        logger.info(f"Shutdown signal sent, processing in progress: {current[0]}:{current[1]}")
        logger.info("Gunicorn graceful-timeout will allow processing to finish")


def _try_become_background_leader() -> bool:
    """Try to acquire exclusive lock for background thread ownership.

    Only one Gunicorn worker should run background tasks (RSS refresh,
    queue processor) to avoid SQLite write contention.
    """
    lock_path = Path(os.getenv('DATA_DIR', '/app/data')) / '.background_leader.lock'
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_path, 'a')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep file handle open (lock released when process exits)
        _try_become_background_leader._lock_file = lock_file
        return True
    except (IOError, OSError):
        return False


# Initialize Flask app
app = Flask(__name__)

# Session configuration for authentication
app.secret_key = os.environ.get('SECRET_KEY') or get_or_create_secret_key()
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = int(os.environ.get('SESSION_LIFETIME_HOURS', '24')) * 3600

# Enable gzip compression for responses
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

# Register routes from routes module
from main_app.routes import register_routes
register_routes(app)

# Re-export public API for downstream consumers
from main_app.feeds import refresh_rss_feed, refresh_all_feeds, invalidate_feed_cache, get_feed_map
from main_app.processing import start_background_processing
from main_app.background import background_rss_refresh, background_queue_processor, reset_stuck_processing_episodes


# Startup initialization (runs when module is imported by gunicorn)
def _startup():
    """Initialize the application on startup."""
    from main_app.feeds import get_feed_map, refresh_rss_feed
    from main_app.background import background_rss_refresh, background_queue_processor, reset_stuck_processing_episodes

    # Import version (version.py is in project root, not src/)
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
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

    # Only one worker should run background tasks to avoid SQLite contention
    if _try_become_background_leader():
        # Start background RSS refresh thread
        refresh_thread = threading.Thread(target=background_rss_refresh, daemon=True)
        refresh_thread.start()
        logger.info("Started background refresh thread")

        # Start background queue processor thread for auto-processing
        queue_thread = threading.Thread(target=background_queue_processor, daemon=True)
        queue_thread.start()
        logger.info("Started auto-process queue processor thread")

        # Initial RSS refresh (leader only to avoid SQLite contention)
        logger.info("Performing initial RSS refresh")
        feed_map = get_feed_map()
        for slug, feed_info in feed_map.items():
            refresh_rss_feed(slug, feed_info['in'])
            logger.info(f"Feed: {base_url}/{slug}")
    else:
        logger.info("Background threads managed by another worker, skipping")

    logger.info(f"Web UI available at: {base_url}/ui/")


_startup()

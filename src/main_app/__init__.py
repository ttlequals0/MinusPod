"""Main Flask web server for podcast ad removal with web UI."""
import fcntl
import json
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import uuid
from pathlib import Path

import defusedxml
# Neutralize DTD/external-entity expansion in every stdlib XML parser
# (xml.etree.ElementTree, xml.sax, xml.dom.minidom, xml.dom.pulldom)
# before anything else imports them. feedparser routes namespace
# handling through xml.sax, so this also hardens RSS ingestion.
defusedxml.defuse_stdlib()

from flask import Flask
from flask_compress import Compress

# Configure structured logging
_logging_configured = False


_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()


class _RequestIDFilter(logging.Filter):
    """Enrich log records with the current Flask request_id, if any."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from flask import g, has_request_context
            if has_request_context():
                rid = getattr(g, 'request_id', None)
                if rid:
                    record.request_id = rid
        except Exception:
            pass
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Outputs logs as JSON objects for easier parsing by log aggregators
    like Loki, Elasticsearch, or CloudWatch. Each line carries hostname
    and PID to make it easier to correlate across multi-worker
    deployments; request_id is populated from Flask ``g`` when the
    log call happens inside an active request context (see
    :func:`_attach_request_id`).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'hostname': _HOSTNAME,
            'pid': os.getpid(),
        }

        for attr in ('episode_id', 'slug', 'request_id'):
            value = getattr(record, attr, None)
            if value is not None:
                log_data[attr] = value

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
    # Stamp every record with the current Flask request_id when available
    # so the JSON formatter's request_id field is always accurate inside
    # request handlers.
    console_handler.addFilter(_RequestIDFilter())

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
from secrets_crypto import migrate_plaintext_secrets

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

# One-shot startup backfills, version-gated via system_settings so
# they only run on the first boot that ships the corresponding code.
# Previously every worker boot scanned the episodes table on every
# commit that touched these features; the guard avoids that churn on
# deployments that already ran the backfill.
_BACKFILL_VERSION_KEY = 'startup_backfills_version'
_BACKFILL_VERSION = '2.0.0'
_stored_backfill_version = None
try:
    _stored_backfill_version = db.get_system_setting(_BACKFILL_VERSION_KEY)
except Exception as e:
    audio_logger.warning(f"Could not read backfill version marker: {e}")

if _stored_backfill_version != _BACKFILL_VERSION:
    try:
        backfilled = db.backfill_processing_history()
        if backfilled > 0:
            audio_logger.info(f"Backfilled {backfilled} records to processing_history")
    except Exception as e:
        audio_logger.warning(f"History backfill failed: {e}")

    try:
        patterns_created = db.backfill_patterns_from_corrections()
        if patterns_created > 0:
            audio_logger.info(f"Created {patterns_created} patterns from existing corrections")
    except Exception as e:
        audio_logger.warning(f"Pattern backfill failed: {e}")

    try:
        deduped = db.deduplicate_patterns()
        if deduped > 0:
            audio_logger.info(f"Removed {deduped} duplicate patterns")
    except Exception as e:
        audio_logger.warning(f"Pattern deduplication failed: {e}")

    try:
        sponsors_extracted = db.extract_sponsors_for_patterns()
        if sponsors_extracted > 0:
            audio_logger.info(f"Extracted sponsors for {sponsors_extracted} patterns")
    except Exception as e:
        audio_logger.warning(f"Sponsor extraction failed: {e}")

    try:
        db.set_system_setting(_BACKFILL_VERSION_KEY, _BACKFILL_VERSION)
    except Exception as e:
        audio_logger.warning(f"Could not write backfill version marker: {e}")

def _validate_configured_base_urls():
    """Best-effort sanity check on operator-configured base URLs.

    Runs once at startup. Any env var or DB setting that holds a URL the
    SSRF validator would refuse surfaces at ERROR so an operator sees the
    problem during deploy instead of at the first outbound call. A failure
    never aborts startup; the fetch-time validators catch the same URL
    when it is actually used.
    """
    from utils.url import validate_base_url, SSRFError
    checks = [
        ('env:OPENAI_BASE_URL', os.environ.get('OPENAI_BASE_URL')),
        ('env:WHISPER_API_BASE_URL', os.environ.get('WHISPER_API_BASE_URL')),
        ('env:OPENROUTER_BASE_URL', os.environ.get('OPENROUTER_BASE_URL')),
        ('env:ANTHROPIC_BASE_URL', os.environ.get('ANTHROPIC_BASE_URL')),
    ]
    try:
        db_settings = db.get_all_settings()
    except Exception:
        db_settings = {}
    for key in ('openai_base_url', 'whisper_api_base_url'):
        entry = db_settings.get(key)
        if isinstance(entry, dict) and entry.get('value'):
            checks.append((f"db:{key}", entry['value']))

    for source, url in checks:
        if not url:
            continue
        try:
            validate_base_url(url)
        except SSRFError as exc:
            audio_logger.error(
                "Configured base URL failed SSRF validation at startup: source=%s url=%s reason=%s",
                source, url, exc,
            )


_validate_configured_base_urls()


# Re-encrypt any legacy plaintext provider secrets under the current
# master passphrase. Idempotent on ``enc:v1:`` rows and no-op when there
# is no plaintext to migrate, so racing gunicorn workers cannot corrupt
# the DB; backup filenames include PID and a UUID suffix to avoid
# collisions when two workers do encrypt at the same wall-clock second.
try:
    migrate_plaintext_secrets(db)
except Exception as e:
    audio_logger.warning(f"Secret migration failed: {e}")

# The legacy OPENAI_API_KEY -> ANTHROPIC_API_KEY fallback was removed.
# Warn operators whose env still fits the old shape so the behavior
# change is discoverable. The check is env-only so the warning fires
# once per worker boot regardless of migration sentinel state.
if not os.environ.get('OPENAI_API_KEY') and os.environ.get('ANTHROPIC_API_KEY'):
    audio_logger.warning(
        "OPENAI_API_KEY is not set but ANTHROPIC_API_KEY is. The "
        "cross-provider fallback has been removed; OpenAI-compatible "
        "requests will no longer use ANTHROPIC_API_KEY. Set "
        "OPENAI_API_KEY explicitly or configure an OpenAI provider "
        "via Settings."
    )


# Gunicorn workers are independent processes, so an OS-level flock is
# what keeps concurrent first-boot races from minting two different
# keys. Without it, a stray second winner invalidates sessions held by
# the first winner's cookies.
_SECRET_KEY_LOCKFILE = Path(
    os.environ.get('DATA_DIR')
    or os.environ.get('DATA_PATH')
    or os.environ.get('MINUSPOD_DATA_DIR')
    or '/app/data'
) / '.secret_key.lock'


class SecretKeyUnavailableError(RuntimeError):
    """Raised when the Flask secret key cannot be loaded or minted safely.

    Without a stable secret key, signed session cookies issued by one worker
    cannot be verified by another, and a forged cookie can't be distinguished
    from a legitimate one. Refuse to start rather than silently shipping a
    diverging or attacker-controllable key. Override `SECRET_KEY` via env to
    bypass this path entirely (e.g. in dev or ephemeral test environments).
    """


def get_or_create_secret_key():
    """Get secret key from database or create and persist one under flock.

    Raises SecretKeyUnavailableError if both the lockfile AND the DB write
    fail; without coordination, two workers racing the mint path produce
    divergent keys and silently invalidate each other's sessions.
    """
    from database import Database
    _db = Database()

    secret_key = _db.get_setting('flask_secret_key')
    if secret_key:
        return secret_key

    try:
        _SECRET_KEY_LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        lock_fd = os.open(str(_SECRET_KEY_LOCKFILE), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        logger.warning("Secret-key lockfile unavailable (%s); proceeding without flock", exc)
        lock_fd = None

    try:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        secret_key = _db.get_setting('flask_secret_key')
        if not secret_key:
            new_key = secrets.token_hex(32)
            try:
                _db.set_setting('flask_secret_key', new_key)
            except Exception as exc:
                if lock_fd is None:
                    raise SecretKeyUnavailableError(
                        "Refusing to start: secret key DB write failed and the lockfile "
                        "is unavailable, so we cannot guarantee workers agree on the key. "
                        f"Underlying error: {exc!r}. Set SECRET_KEY via env to bypass."
                    ) from exc
                raise
            secret_key = new_key
            logger.info("Generated and persisted new Flask secret key")
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

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

    # Release the background-leader flock explicitly. Linux frees the
    # advisory lock when the FD closes anyway, so this is defensive;
    # on NFS and weird filesystem layers with buggy close-on-exit
    # semantics, the explicit LOCK_UN is the only way to guarantee
    # release inside the graceful window.
    lock_file = getattr(_try_become_background_leader, '_lock_file', None)
    if lock_file is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            logger.info("Released background leader lock")
        except Exception as exc:
            logger.warning("Failed to release background leader lock: %s", exc)

    # Terminate any tracked subprocess children so a SIGTERM on the
    # worker does not leave ffmpeg / whisper processes orphaned. The
    # registry is a no-op if no processes have been registered.
    try:
        from utils.subprocess_registry import terminate_all
        terminate_all(timeout=5.0)
    except Exception as exc:
        logger.warning("subprocess_registry terminate_all failed: %s", exc)


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


def _init_sentry():
    """Opt-in Sentry bootstrap.

    Enabled only when both ``SENTRY_DSN`` and ``sentry-sdk`` are
    available. Scrubs cookies, the ``Authorization`` and
    ``X-CSRF-Token`` headers, and any URL-query key whose lowercased
    name contains ``key``, ``secret``, ``token``, or ``password``
    before events leave the process. Does not enable performance
    tracing.
    """
    dsn = os.environ.get('SENTRY_DSN', '').strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        audio_logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; skipping Sentry init"
        )
        return

    _SECRET_QUERY_HINTS = ('key', 'secret', 'token', 'password')

    def _scrub(event, _hint):
        request_data = event.get('request') or {}
        headers = request_data.get('headers') or {}
        for header in list(headers):
            if header.lower() in ('authorization', 'cookie', 'x-csrf-token'):
                headers[header] = '[scrubbed]'

        query_string = request_data.get('query_string')
        if isinstance(query_string, str) and '=' in query_string:
            scrubbed_pairs = []
            for pair in query_string.split('&'):
                name, _, value = pair.partition('=')
                if any(h in name.lower() for h in _SECRET_QUERY_HINTS):
                    scrubbed_pairs.append(f"{name}=[scrubbed]")
                else:
                    scrubbed_pairs.append(pair)
            request_data['query_string'] = '&'.join(scrubbed_pairs)

        event.pop('_meta', None)
        return event

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.0,
        send_default_pii=False,
        before_send=_scrub,
        release=os.environ.get('MINUSPOD_RELEASE') or None,
        environment=os.environ.get('SENTRY_ENVIRONMENT') or 'production',
    )
    audio_logger.info("Sentry initialized (DSN configured)")


_init_sentry()


# Initialize Flask app
app = Flask(__name__)

# Reverse-proxy awareness. Cloudflare + cloudflared puts the real client IP
# in X-Forwarded-For, so request.remote_addr is otherwise the tunnel's
# loopback hop. The lockout logic in api/auth.py keys its decision on
# remote_addr; without ProxyFix, every failed login looks like it came
# from 127.0.0.1 and lockout never fires. Configure via
# MINUSPOD_TRUSTED_PROXY_COUNT=1 (most single-proxy setups) or higher.
_trusted_proxy_hops = int(os.environ.get('MINUSPOD_TRUSTED_PROXY_COUNT', '0') or 0)
if _trusted_proxy_hops > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=_trusted_proxy_hops,
        x_proto=_trusted_proxy_hops,
        x_host=_trusted_proxy_hops,
    )
    # Log without formatting the raw env-derived value; an operator who
    # wants the exact hop count can read MINUSPOD_TRUSTED_PROXY_COUNT. This
    # keeps CodeQL's py/clear-text-logging-sensitive-data heuristic quiet.
    audio_logger.info("ProxyFix enabled from MINUSPOD_TRUSTED_PROXY_COUNT")
else:
    # Docker deployments behind a proxy typically need this; a loud warn
    # on startup saves a support round-trip when lockout appears not to
    # work.
    if os.environ.get('DOCKER_CONTAINER') or os.path.exists('/.dockerenv'):
        audio_logger.warning(
            "Running in a container without MINUSPOD_TRUSTED_PROXY_COUNT set; "
            "remote_addr will reflect the proxy hop, not the real client. "
            "Login lockout decisions will be inaccurate behind Cloudflare / "
            "nginx unless you set MINUSPOD_TRUSTED_PROXY_COUNT."
        )

# Session configuration for authentication
app.secret_key = os.environ.get('SECRET_KEY') or get_or_create_secret_key()
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Strict')
app.config['PERMANENT_SESSION_LIFETIME'] = int(os.environ.get('SESSION_LIFETIME_HOURS', '24')) * 3600

# Hard cap on request body size. Chosen to cover the largest legitimate
# request (OPML import and patterns export round-trip both target ~10 MB);
# pure JSON endpoints can reject well below this with per-route checks.
# Clients receive 413 Payload Too Large automatically when exceeded.
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

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

# CORS is intentionally NOT enabled. MinusPod is single-origin (browser
# talks to the same host that serves the API); the Vite dev server at
# :5173 proxies /api/* to :8000, so cross-origin requests never reach
# the Python process in practice. Removing flask-cors closes an
# allow-credentials-from-any-origin footgun and simplifies the
# middleware stack.

# Import and register API blueprint
from api import api as api_blueprint, init_limiter
app.register_blueprint(api_blueprint)
init_limiter(app)


@app.before_request
def _attach_request_id():
    """Populate Flask ``g.request_id`` with either the inbound
    ``X-Request-ID`` header or a fresh UUID hex. The value gets
    propagated to the JSON log formatter via a filter, and echoed
    back on the response so clients can correlate."""
    from flask import g, request
    inbound = request.headers.get('X-Request-ID', '').strip()
    g.request_id = inbound[:128] if inbound else uuid.uuid4().hex[:16]


@app.after_request
def _echo_request_id(response):
    from flask import g
    rid = getattr(g, 'request_id', None)
    if rid:
        response.headers['X-Request-ID'] = rid
    return response


@app.after_request
def _apply_csrf_cookie(response):
    """Mint a CSRF cookie alongside the session cookie so the frontend can
    populate the X-CSRF-Token header on mutating requests. Delegated to
    api.csrf.apply_csrf_cookie; see that module for the full contract.
    """
    from api.csrf import apply_csrf_cookie
    cookie_secure = app.config.get('SESSION_COOKIE_SECURE', True)
    return apply_csrf_cookie(response, cookie_secure)


@app.after_request
def _apply_security_headers(response):
    """Attach baseline security headers to every response.

    HSTS is only meaningful over HTTPS and would break clients that hit the
    instance on plain HTTP for recovery, so it is opt-in via env. CSP is
    scoped to HTML responses so RSS, VTT, and JSON payloads are not given
    a policy they cannot meaningfully enforce; the Content-Type check
    avoids emitting CSP on podcast-app consumer endpoints.
    """
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if os.environ.get('MINUSPOD_ENABLE_HSTS', 'false').lower() == 'true':
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains'
        )
    content_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
    if content_type == 'text/html':
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    elif content_type == 'application/json':
        # JSON API responses cannot execute script, but a permissive CSP
        # still helps if a browser is tricked into rendering one. Lock it
        # down to deny everything and block framing.
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'none'; frame-ancestors 'none'"
        )
    return response

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

    # Only one worker should run background tasks to avoid SQLite contention
    if _try_become_background_leader():
        # Seed sponsor and normalization data. The 2.0.13 rewrite made this
        # idempotent (per-startup name-diff insert), so it must run on the
        # leader only or the workers race on writes and trigger
        # "database is locked" cascades on the reprocess endpoint.
        sponsor_service.seed_initial_data()
        logger.info("Sponsor service initialized (leader)")

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

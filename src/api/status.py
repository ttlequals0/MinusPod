"""Status routes: /status/* endpoints (SSE stream, current status)."""
import json
import logging
import queue
import threading

from flask import Response, session

from api import (
    api, log_request, json_response,
    get_database, get_status_service,
)
from config import DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER
from offline_queue import get_probe_state
from rate_limit_hold import (
    RATE_LIMIT_DEFERRED_SERVICE, get_hold_until, hold_is_active,
)
from utils.ttl_cache import TTLCache

logger = logging.getLogger('podcast.api')

# Services the offline queue can park an episode on, in display order.
OFFLINE_SERVICES = (DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER)

# What a caller sees when nothing is held, and when the read fails.
EMPTY_HOLD = {
    'queuePaused': False, 'holdUntil': None, 'rateLimitHeld': 0,
    'offlineHeld': 0, 'offlineServices': [],
}

# The block is rebuilt per reader, so it is cached. The underlying state only
# changes on the ~5-minute maintenance tick or a 429, so a short TTL is enough.
_HOLD_CACHE_TTL_SECONDS = 15
_hold_cache = TTLCache(ttl_seconds=_HOLD_CACHE_TTL_SECONDS)
_hold_cache_lock = threading.Lock()


def _offline_service_view(db, service: str) -> dict | None:
    """One offline-queue service's held count and last probe verdict, or None
    when nothing is waiting on it."""
    held = db.count_deferred_episodes(service=service)
    if not held:
        return None
    reachable, checked_at = get_probe_state(db, service)
    return {
        'service': service, 'held': held,
        'reachable': reachable, 'checkedAt': checked_at,
    }


def _build_hold_block(db) -> dict:
    """Queue hold state: the rate-limit pause and any offline-queue waits.

    Reports what the maintenance tick last observed. Nothing here probes a
    service, so an open SSE stream cannot generate outbound traffic.
    """
    hold_until = get_hold_until(db)
    return {
        'queuePaused': hold_is_active(hold_until),
        'holdUntil': hold_until,
        'rateLimitHeld': db.count_deferred_episodes(
            service=RATE_LIMIT_DEFERRED_SERVICE),
        # Every non-rate-limit deferral, so the total agrees with
        # /settings/offline-queue even for a service not broken out below.
        'offlineHeld': db.count_deferred_episodes(
            exclude_service=RATE_LIMIT_DEFERRED_SERVICE),
        'offlineServices': [
            v for v in (_offline_service_view(db, s) for s in OFFLINE_SERVICES) if v
        ],
    }


def hold_block() -> dict:
    """Cached _build_hold_block. Never raises: a status frame must still go out
    when the hold read fails, so the caller sees an empty hold instead."""
    # The lock spans the rebuild so two readers arriving on the same expiry
    # boundary do not both run the queries.
    with _hold_cache_lock:
        cached = _hold_cache.get('hold')
        if cached is not None:
            return cached
        try:
            block = _build_hold_block(get_database())
        except Exception as e:
            logger.warning(f"Could not read queue hold state: {e}")
            block = dict(EMPTY_HOLD)
        _hold_cache.set('hold', block)
        return block


def status_payload(status=None) -> dict:
    """Status snapshot plus the queue hold block.

    status_service is file-backed and imports no database on purpose, so the
    hold state is merged here instead, where both the stream and the one-time
    GET pick it up from the same place.
    """
    payload = get_status_service().to_dict(status)
    payload['hold'] = hold_block()
    return payload


# ========== Status Stream Endpoint (SSE) ==========

def _is_authenticated() -> bool:
    """Mirror the api.before_request auth rule. When no password is set
    there is no auth to enforce; otherwise the session flag is required.
    """
    db = get_database()
    password_hash = db.get_setting('app_password')
    if not password_hash:
        return True
    return bool(session.get('authenticated', False))


@api.route('/status/stream', methods=['GET'])
def status_stream():
    """
    Server-Sent Events stream for real-time processing status updates.

    Listed in AUTH_EXEMPT_PATHS because EventSource cannot surface an
    HTTP 401 to the JavaScript handler -- the browser reconnect-loops
    against the closed response with no signal about why. Auth is
    snapshotted once at connect time below: an unauthenticated caller
    receives a single ``event: auth-failed`` SSE message and the
    stream closes. GlobalStatusBar.tsx listens for that event and
    redirects to /ui/login. A session that lapses mid-stream is caught
    on the client's next non-SSE API call, which apiRequest
    401-redirects.
    """
    # Evaluate auth inside the request context before the generator
    # runs. The generator lives past request-end (SSE is long-polled),
    # so session/request proxies are not usable from inside the loop.
    # A lapsed session after connect is caught by the client's next
    # non-SSE API call, which apiRequest 401-redirects to /ui/login.
    authenticated_at_connect = _is_authenticated()

    def generate():
        if not authenticated_at_connect:
            yield "event: auth-failed\ndata: {}\n\n"
            return

        status_service = get_status_service()
        update_queue = queue.Queue(maxsize=50)

        def on_update(status):
            # Queue the raw snapshot: this runs on the pipeline's broadcast
            # thread, which must not block on the hold block's DB reads.
            try:
                update_queue.put_nowait(status)
            except queue.Full:
                pass  # Drop update if queue is full

        unsubscribe = status_service.subscribe(on_update)

        try:
            yield f"data: {json.dumps(status_payload())}\n\n"

            while True:
                try:
                    status = update_queue.get(timeout=15)
                    yield f"data: {json.dumps(status_payload(status))}\n\n"
                except queue.Empty:
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
    return json_response(status_payload())

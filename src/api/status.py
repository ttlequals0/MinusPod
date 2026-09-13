"""Status routes: /status/* endpoints (SSE stream, current status)."""
import json
import hashlib
import logging
import os
import threading
import time

from flask import Response, request, session

from api import (
    api, log_request, json_response,
    get_database, get_status_service,
)
from config import DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER
from offline_queue import get_probe_state
from rate_limit_hold import get_active_hold
from processing_queue import (
    ProcessingQueue, is_processing_paused, set_processing_paused,
)
from utils.ttl_cache import TTLCache
from whisper_pool import get_pool
from api.auth_state import (
    SESSION_GENERATION_KEY, authentication_required, current_generation,
    session_is_authenticated,
)

logger = logging.getLogger('podcast.api')

# Services the offline queue can park an episode on, in display order.
OFFLINE_SERVICES = (DEFER_SERVICE_LLM, DEFER_SERVICE_WHISPER)

# What a caller sees when nothing is held, and when the read fails.
EMPTY_HOLD = {
    'queuePaused': False, 'holdUntil': None, 'holdSince': None,
    'offlineHeld': 0, 'offlineServices': [],
}

# The block is rebuilt per reader, so it is cached. The underlying state only
# changes on a queue-processor pass or a 429, so a short TTL is enough.
_HOLD_CACHE_TTL_SECONDS = 15
_hold_cache = TTLCache(ttl_seconds=_HOLD_CACHE_TTL_SECONDS)
_hold_cache_lock = threading.Lock()
_last_hold: dict = {}
_SSE_MAX_SECONDS = 30
_SSE_POLL_SECONDS = 2
try:
    _SSE_SLOT_COUNT = max(0, int(os.environ.get('GUNICORN_THREADS', '8')) - 1)
except ValueError:
    _SSE_SLOT_COUNT = 0
_SSE_SLOTS = threading.BoundedSemaphore(_SSE_SLOT_COUNT) if _SSE_SLOT_COUNT else None


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
    hold_until, hold_since = get_active_hold(db)
    return {
        'queuePaused': hold_until is not None,
        'holdUntil': hold_until,
        'holdSince': hold_since,
        # Every deferral, so the total agrees with /settings/offline-queue
        # even for a service not broken out below.
        'offlineHeld': db.count_deferred_episodes(),
        'offlineServices': [
            v for v in (_offline_service_view(db, s) for s in OFFLINE_SERVICES) if v
        ],
    }


def hold_block() -> dict:
    """Cached _build_hold_block. Never raises and never queues: while one
    thread rebuilds, or when the read fails, callers get the last good block
    so a locked database cannot stall every status frame in the worker."""
    cached = _hold_cache.get('hold')
    if cached is not None:
        return cached
    if not _hold_cache_lock.acquire(blocking=False):
        return _last_hold.get('block') or dict(EMPTY_HOLD)
    try:
        cached = _hold_cache.get('hold')
        if cached is not None:
            return cached
        try:
            block = _build_hold_block(get_database())
        except Exception as e:
            logger.warning(f"Could not read queue hold state: {e}")
            block = _last_hold.get('block') or dict(EMPTY_HOLD)
        _hold_cache.set('hold', block)
        _last_hold['block'] = block
        return block
    finally:
        _hold_cache_lock.release()


def status_payload(status=None) -> dict:
    """Status snapshot plus the queue hold block.

    status_service is file-backed and imports no database on purpose, so the
    hold state is merged here instead, where both the stream and the one-time
    GET pick it up from the same place.
    """
    payload = get_status_service().to_dict(status)
    payload['hold'] = hold_block()
    payload['processingPaused'] = is_processing_paused(get_database())
    # This worker may not be the leader, so nothing else refreshes its pool.
    pool = get_pool()
    pool.refresh()
    payload['whisper'] = pool.snapshot()
    return payload


def _payload_version(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


# ========== Status Stream Endpoint (SSE) ==========

def _is_authenticated() -> bool:
    """Mirror the api.before_request auth rule. When no password is set
    there is no auth to enforce; otherwise the session flag is required.
    """
    db = get_database()
    return session_is_authenticated(db)


@api.route('/status/stream', methods=['GET'])
def status_stream():
    """Compatibility SSE stream with a 30-second lifetime and bounded slots."""
    # The generator cannot use Flask's session proxy after request teardown,
    # so capture the cookie generation and compare it with shared DB state.
    authenticated_at_connect = _is_authenticated()
    generation_at_connect = session.get(SESSION_GENERATION_KEY)

    if _SSE_SLOTS is None:
        return Response(status=410)

    def generate():
        acquired = _SSE_SLOTS.acquire(blocking=False)
        if not acquired:
            yield "event: unavailable\ndata: {}\n\n"
            return
        try:
            if not authenticated_at_connect:
                yield "event: auth-failed\ndata: {}\n\n"
                return

            deadline = time.monotonic() + _SSE_MAX_SECONDS
            last_version = None
            while time.monotonic() < deadline:
                db = get_database()
                if (authentication_required(db)
                        and generation_at_connect != current_generation(db)):
                    yield "event: auth-failed\ndata: {}\n\n"
                    return
                payload = status_payload()
                version = (payload.get('revision'), _payload_version(payload))
                if last_version is None or version != last_version:
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_version = version
                else:
                    yield ": keepalive\n\n"
                time.sleep(_SSE_POLL_SECONDS)
        finally:
            if acquired:
                _SSE_SLOTS.release()

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
    payload = status_payload()
    response = json_response(payload)
    response.headers['ETag'] = f'"{_payload_version(payload)}"'
    return response


@api.route('/status/processing-admission', methods=['GET', 'PUT'])
@log_request
def processing_admission():
    """Read or change the durable pause for new processing runs."""
    db = get_database()
    if request.method == 'PUT':
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('paused'), bool):
            return json_response({'error': 'paused must be a boolean'}, 400)
        set_processing_paused(data['paused'], db)
    queue = ProcessingQueue()
    return json_response({
        'paused': is_processing_paused(db),
        'activeRuns': queue.slot_count(),
        'queuedEpisodes': db.count_pending_queued_episodes(),
    })

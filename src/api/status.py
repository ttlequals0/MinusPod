"""Status routes: /status/* endpoints (SSE stream, current status)."""
import json
import logging
import queue

from flask import Response, session

from api import (
    api, log_request, json_response,
    get_database, get_status_service,
)

logger = logging.getLogger('podcast.api')


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
            try:
                update_queue.put_nowait(status_service.to_dict())
            except queue.Full:
                pass  # Drop update if queue is full

        unsubscribe = status_service.subscribe(on_update)

        try:
            yield f"data: {json.dumps(status_service.to_dict())}\n\n"

            while True:
                try:
                    status = update_queue.get(timeout=15)
                    yield f"data: {json.dumps(status)}\n\n"
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
    status_service = get_status_service()
    return json_response(status_service.to_dict())

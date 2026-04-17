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

    Returns a continuous event stream with status updates whenever
    processing state changes.

    The endpoint is listed in AUTH_EXEMPT_PREFIXES because EventSource
    cannot surface a 401 status to the client; instead we emit an
    application-level ``auth-failed`` event when the session lapses and
    the browser-side handler in GlobalStatusBar.tsx redirects to /login.
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

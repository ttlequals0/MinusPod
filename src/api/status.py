"""Status routes: /status/* endpoints (SSE stream, current status)."""
import json
import logging
import queue

from flask import Response

from api import (
    api, log_request, json_response,
    get_status_service,
)

logger = logging.getLogger('podcast.api')


# ========== Status Stream Endpoint (SSE) ==========

@api.route('/status/stream', methods=['GET'])
def status_stream():
    """
    Server-Sent Events stream for real-time processing status updates.

    Returns a continuous event stream with status updates whenever
    processing state changes.
    """
    def generate():
        status_service = get_status_service()
        update_queue = queue.Queue(maxsize=50)

        # Subscribe to status updates
        def on_update(status):
            try:
                update_queue.put_nowait(status_service.to_dict())
            except queue.Full:
                pass  # Drop update if queue is full

        unsubscribe = status_service.subscribe(on_update)

        try:
            # Send initial status immediately
            yield f"data: {json.dumps(status_service.to_dict())}\n\n"

            # Stream updates as they occur
            while True:
                try:
                    # Wait for update with timeout (for keepalive)
                    status = update_queue.get(timeout=15)
                    yield f"data: {json.dumps(status)}\n\n"
                except queue.Empty:
                    # Send keepalive comment
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

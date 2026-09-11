"""Cooperative cancellation primitives for in-flight episode processing.

Lightweight module with no Flask/CUDA dependencies so it can be imported
in unit tests without triggering heavy initialization.
"""
import logging
import threading
import time

import run_context
from processing_queue import ProcessingQueue

logger = logging.getLogger('podcast.audio')

# Local wakeups are keyed by immutable run ID. SQLite carries the request to
# another worker; this map only avoids waiting until the next durable poll.
_cancel_events: dict[str, threading.Event] = {}
_cancel_events_lock = threading.Lock()


class ProcessingCancelled(Exception):
    """Raised when processing is cancelled by user."""
    pass


class ProcessingOwnershipLost(Exception):
    """Raised when a worker cannot prove that it still owns its run."""


def _check_cancel(cancel_event, slug, episode_id, run_id=None):
    """Poll durable ownership and raise before the worker publishes more work."""
    if cancel_event and cancel_event.is_set():
        logger.info(f"[{slug}:{episode_id}] Processing cancelled by user")
        raise ProcessingCancelled()
    if run_id is None:
        run_id = getattr(run_context.current(), 'run_id', None)
    if run_id:
        state = ProcessingQueue().poll(run_id)
        if state == 'cancel_requested':
            if cancel_event:
                cancel_event.set()
            logger.info(f"[{slug}:{episode_id}] Processing cancelled by user")
            raise ProcessingCancelled()
        if state != 'running':
            raise ProcessingOwnershipLost(
                f"Processing ownership lost for {slug}:{episode_id} ({run_id})")


def request_cancellation(slug, episode_id) -> str | None:
    """Persist a cancellation request and wake its local owner when present."""
    try:
        run_id = ProcessingQueue().active_run_id(slug, episode_id)
        if not run_id or run_id == '__database_unavailable__':
            return None
        conn = ProcessingQueue()._database().get_connection()
        cursor = conn.execute(
            "UPDATE processing_runs SET state = 'cancel_requested', "
            "cancel_requested_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE run_id = ? AND state IN ('running', 'cancel_requested')",
            (run_id,),
        )
        conn.commit()
        requested = cursor.rowcount > 0
    except Exception as exc:
        logger.warning("Could not record cancellation for %s:%s: %s", slug, episode_id, exc)
        return None
    if requested:
        with _cancel_events_lock:
            event = _cancel_events.get(run_id)
        if event:
            event.set()
        return run_id
    return None


def wait_for_cancellation(run_id: str, timeout: float) -> bool:
    """Wait until the owner has stopped and moved the run to a terminal state."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            row = ProcessingQueue()._database().get_connection().execute(
                "SELECT state FROM processing_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        except Exception as exc:
            logger.warning("Could not observe cancellation for run %s: %s", run_id, exc)
            return False
        if not row or row['state'] not in ('running', 'cancel_requested'):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def cancel_processing(slug, episode_id, wait_timeout: float = 0) -> bool:
    """Request cancellation and optionally wait for the owner to stop."""
    run_id = request_cancellation(slug, episode_id)
    if not run_id:
        return False
    return wait_for_cancellation(run_id, wait_timeout) if wait_timeout > 0 else True

"""Process registry for graceful shutdown.

Tracks long-running subprocesses (ffmpeg, whisper) so SIGTERM on the gunicorn
worker can terminate them with SIGTERM -> SIGKILL escalation instead of
leaving orphans. Thread-safe; prefer ``tracked_popen`` over bare
``register`` / ``unregister`` so the finally-block guarantees cleanup.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_processes: set[subprocess.Popen] = set()


def register(proc: subprocess.Popen) -> None:
    with _lock:
        _processes.add(proc)


def unregister(proc: subprocess.Popen) -> None:
    with _lock:
        _processes.discard(proc)


def terminate_all(timeout: float = 5.0) -> None:
    """SIGTERM every tracked process; SIGKILL any still alive after ``timeout``."""
    with _lock:
        victims = list(_processes)

    if not victims:
        return

    logger.warning("subprocess_registry: terminating %d tracked processes", len(victims))
    for proc in victims:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("subprocess_registry: SIGTERM failed pid=%s: %s", proc.pid, exc)

    deadline = time.monotonic() + timeout
    for proc in victims:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            logger.warning("subprocess_registry: SIGKILL pid=%s after %.1fs", proc.pid, timeout)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("subprocess_registry: SIGKILL failed pid=%s: %s", proc.pid, exc)


@contextmanager
def tracked_popen(*args, **kwargs) -> Iterator[subprocess.Popen]:
    proc = subprocess.Popen(*args, **kwargs)
    register(proc)
    try:
        yield proc
    finally:
        unregister(proc)

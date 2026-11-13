"""Process registry for graceful shutdown.

Tracks long-running subprocesses (ffmpeg, whisper) so SIGTERM on the gunicorn worker can
escalate SIGTERM -> SIGKILL on them instead of leaving orphans. Thread-safe; prefer
``tracked_popen`` over bare ``register`` / ``unregister`` so cleanup is guaranteed.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

import run_context

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_processes: set[subprocess.Popen] = set()


def _is_ffmpeg_command(args) -> bool:
    try:
        executable = args if isinstance(args, (str, bytes, os.PathLike)) else args[0]
        return os.path.basename(os.fsdecode(executable)) == 'ffmpeg'
    except (IndexError, TypeError, ValueError):
        return False


def _record_ffmpeg_elapsed(started: float | None) -> None:
    if started is None:
        return
    run_context.record_ffmpeg_elapsed(time.monotonic() - started)


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
    command = args[0] if args else kwargs.get('args')
    started = time.monotonic() if _is_ffmpeg_command(command) else None
    proc = subprocess.Popen(*args, **kwargs)
    register(proc)
    try:
        yield proc
    finally:
        unregister(proc)
        if proc.poll() is not None:
            _record_ffmpeg_elapsed(started)


def tracked_run(
    args,
    *,
    timeout: float | None = None,
    check: bool = False,
    input: bytes | None = None,
    capture_output: bool = False,
    stdout=None,
    stderr=None,
    stdin=None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Drop-in replacement for ``subprocess.run`` that registers the child.

    With ``subprocess.run`` the Python signal handler only fires between C calls, so a child
    can outlive a SIGTERM to the worker and become an orphan under PID 1 if gunicorn then
    SIGKILLs it. Popen plus the registry lets ``terminate_all`` (called from the graceful-shutdown
    handler) forward SIGTERM, then SIGKILL after a deadline, to every tracked child.
    """
    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("capture_output may not be used with stdout/stderr")
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE

    if input is not None:
        if stdin is not None:
            raise ValueError("input may not be used with stdin")
        stdin = subprocess.PIPE

    started = time.monotonic() if _is_ffmpeg_command(args) else None
    proc = subprocess.Popen(
        args,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        **kwargs,
    )
    register(proc)
    try:
        try:
            stdout_data, stderr_data = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            # Drain pipes so FDs close and the child actually reaps.
            drained_stdout, drained_stderr = proc.communicate()
            # Mirror stdlib `subprocess.run` contract: populate the
            # exception's stdout/stderr so callers that handle the
            # timeout can still inspect what was produced.
            e.stdout = drained_stdout
            e.stderr = drained_stderr
            raise
        completed = subprocess.CompletedProcess(
            args=args,
            returncode=proc.returncode,
            stdout=stdout_data,
            stderr=stderr_data,
        )
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode, args, completed.stdout, completed.stderr
            )
        return completed
    finally:
        unregister(proc)
        _record_ffmpeg_elapsed(started)

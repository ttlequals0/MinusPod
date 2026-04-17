"""Gunicorn configuration.

Mirrors the flags previously inlined in ``entrypoint.sh`` so lifecycle hooks
(``on_starting``, ``post_fork``, ``when_ready``) can be wired from a tracked
config file rather than command-line arguments.
"""

from __future__ import annotations

import logging
import os
import sys


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "600"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "330"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

worker_class = "gthread"
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

_log = logging.getLogger("gunicorn.lifecycle")


def on_starting(server):
    """Master-only, pre-fork.

    BEST-EFFORT schema pre-init. Runs Database() in the master so that
    the first worker's request doesn't pay migration latency and so the
    migrations don't race between two newly-forked workers. If the
    master can't open the DB (volume mount race, permissions flap, WAL
    header from a still-dying previous container), we log and let the
    workers try -- they'll apply migrations on first access. Failing
    the master here causes a fatal crash-loop that's worse than the
    race it was trying to prevent.

    Also drops the master's connection + singleton before fork so
    workers don't inherit a live WAL handle. Each worker opens its own
    SQLite connection via post_fork + lazy Database().
    """
    src_dir = "/app/src"
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    try:
        from database import Database
        db = Database()
        try:
            if hasattr(db, '_local') and hasattr(db._local, 'connection'):
                conn = db._local.connection
                if conn is not None:
                    conn.close()
                db._local.connection = None
        finally:
            Database._instance = None
        _log.info("gunicorn on_starting: schema init complete")
    except Exception:
        _log.warning(
            "gunicorn on_starting: schema pre-init failed (%s); workers "
            "will re-attempt on first request. Investigate if this is "
            "not a transient volume / container-handoff race.",
            sys.exc_info()[1],
            exc_info=False,
        )
        # Make sure a partially-materialised singleton doesn't linger
        # into the worker forks.
        try:
            from database import Database as _Db
            _Db._instance = None
        except Exception:
            pass


def post_fork(server, worker):
    """Per-worker, post-fork.

    Reset the ``Database`` singleton so each worker opens its own sqlite
    connection instead of inheriting the master's fds. Fork-inherited
    SQLite connections corrupt state the moment two workers hit them;
    a reset failure is serious enough to refuse serving from this worker.
    """
    src_dir = "/app/src"
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from database import Database
    Database._instance = None


def when_ready(server):
    """Master, after all workers booted."""
    _log.info("gunicorn when_ready: workers=%s threads=%s", workers, threads)

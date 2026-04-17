"""Gunicorn configuration.

Mirrors the flags previously inlined in ``entrypoint.sh`` so lifecycle hooks
(``on_starting``, ``post_fork``, ``when_ready``) can be wired from a tracked
config file rather than command-line arguments.
"""

from __future__ import annotations

import os


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


def on_starting(server):
    """Master-only, pre-fork."""


def post_fork(server, worker):
    """Per-worker, post-fork."""


def when_ready(server):
    """Master, after all workers booted."""

"""Exclude offline maintenance while MinusPod workers are running."""

import fcntl
import os
from pathlib import Path

from utils.paths import resolve_data_dir


class ServiceRunningError(RuntimeError):
    pass


def lock_path(data_dir=None) -> Path:
    return Path(data_dir or resolve_data_dir()) / '.minuspod-runtime.lock'


def acquire_runtime_lock(data_dir=None):
    path = lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_SH)
    return fd


def acquire_offline_lock(data_dir=None):
    path = lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ServiceRunningError(
            'MinusPod is running; stop every worker before offline maintenance'
        ) from exc
    return fd


def release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)

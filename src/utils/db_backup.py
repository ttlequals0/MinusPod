"""SQLite online-backup helper shared across migration, cleanup, and admin paths."""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def snapshot_database(db, backup_path: str | Path, tighten_dir_perms: bool = True) -> str:
    """Write a consistent snapshot of ``db`` to ``backup_path`` via the SQLite online backup API.

    Creates parent directories and writes mode ``0o600`` (the dump holds provider secrets).
    Partially-written files are removed on failure.

    ``tighten_dir_perms`` chmods the parent to ``0o700``; pass False for a user-chosen
    destination (e.g. a NAS mount) so its permissions stay.
    """
    path = Path(backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if tighten_dir_perms:
        try:
            path.parent.chmod(0o700)
        except OSError:
            logger.warning("could not tighten backup dir permissions on %s", path.parent)

    src = db.get_connection()
    dst = sqlite3.connect(str(path))
    try:
        src.backup(dst)
    except Exception:
        dst.close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    dst.close()

    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("could not tighten backup file permissions on %s", path)

    return str(path)

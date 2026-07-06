"""Scheduled SQLite database backups.

Writes a consistent snapshot of the podcast database to a configurable
destination on a cron schedule, modeled on the community-sync feature.
Two retention modes: overwrite (keepCount == 1, one fixed filename) and
rotate (keepCount > 1, timestamped filenames pruned to the last N).

Settings keys (in the `settings` table):

  - db_backup_enabled (bool, default false)
  - db_backup_cron    (str cron expression, default '30 3 * * *')
  - db_backup_dest    (absolute dir path; '' resolves to <data_dir>/backups)
  - db_backup_keep_count (int as string, default '1', clamped 1..365)
  - db_backup_last_run, db_backup_last_error, db_backup_last_summary
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from utils.cron import is_due
from utils.db_backup import snapshot_database
from utils.time import parse_iso_datetime, utc_now_iso

logger = logging.getLogger('podcast.db_backup')

DEFAULT_CRON = '30 3 * * *'  # daily 03:30 UTC
FIXED_BACKUP_NAME = 'minuspod-backup.db'
TEMP_BACKUP_NAME = '.minuspod-backup.db.tmp'
ROTATED_NAME_RE = re.compile(r'^minuspod-backup-\d{8}-\d{6}\.db$')
KEEP_COUNT_MIN, KEEP_COUNT_MAX = 1, 365
LOCK_FILENAME = '.db_backup.lock'


class BackupInProgressError(Exception):
    """Raised when another backup already holds the lock."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parse_iso_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_backup_dest(raw: Any, data_dir: str | Path) -> Path:
    """Resolve and validate a backup destination directory.

    Raises ValueError with a user-facing message on any rejection. Normalizes
    with os.path.realpath before any filesystem check so the resolved path is
    what collision checks and the CodeQL path-injection sink both see.
    """
    if not isinstance(raw, str):
        raise ValueError('destination must be a string')
    if len(raw) > 4096:
        raise ValueError('destination path is too long')

    data_root = os.path.realpath(str(data_dir))
    if raw == '':
        # Default destination: a data_dir subdirectory. Still runs the guards
        # below (the not-a-directory check matters if a file sits there).
        target = os.path.join(data_root, 'backups')
    else:
        if not os.path.isabs(raw):
            raise ValueError('destination must be an absolute path')
        target = raw

    resolved = os.path.realpath(target)
    if resolved == data_root:
        raise ValueError('destination must not be the data directory itself')
    if resolved == '/':
        raise ValueError('destination must not be the filesystem root')
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise ValueError('destination exists and is not a directory')
    return Path(resolved)


def _clamp_keep_count(raw: Optional[str]) -> int:
    try:
        value = int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        value = 1
    return max(KEEP_COUNT_MIN, min(KEEP_COUNT_MAX, value))


def _next_rotated_path(dest: Path, now: datetime) -> Path:
    """Return a non-colliding rotated backup path for `now`.

    Two backups in the same UTC second would otherwise share a filename and the
    second os.replace would overwrite the first, destroying a good snapshot. If
    the second-resolution name is taken, step forward a second until one is free
    so keepCount > 1 always retains distinct files.
    """
    for offset in range(60):
        stamp = (now + timedelta(seconds=offset)).strftime('%Y%m%d-%H%M%S')
        candidate = dest / f'minuspod-backup-{stamp}.db'
        if not candidate.exists():
            return candidate
    # 60 taken names in a row is implausible; fall back to the base name.
    return dest / f'minuspod-backup-{now.strftime("%Y%m%d-%H%M%S")}.db'


def _prune_rotated(dest: Path, keep: int, keep_path: Optional[Path] = None) -> int:
    """Prune rotated backups down to `keep`, and drop the fixed file in rotate
    mode / all rotated files in overwrite mode. `keep_path` (the file just
    written) is never pruned. Errors are logged, never raised.
    """
    pruned = 0
    if keep == 1:
        # Overwrite mode: no timestamped files should survive.
        stale = [p for p in dest.iterdir() if ROTATED_NAME_RE.match(p.name)]
    else:
        # Rotate mode: keep the newest `keep` timestamped files; also remove a
        # leftover fixed file from a prior overwrite-mode run.
        rotated = sorted(
            (p for p in dest.iterdir() if ROTATED_NAME_RE.match(p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
        stale = rotated[keep:]
        fixed = dest / FIXED_BACKUP_NAME
        if fixed.exists():
            stale.append(fixed)

    for path in stale:
        if keep_path is not None and path == keep_path:
            continue
        try:
            path.unlink()
            pruned += 1
        except OSError as e:
            logger.warning('db_backup: could not prune %s: %s', path, e)
    return pruned


def backup_now(db) -> Dict[str, Any]:
    """Run a backup now regardless of schedule. Returns a summary dict.

    Stamps last_run before doing any work (community-sync convention: a
    persistently failing backup retries at the next cron fire, not every tick).
    On failure the settings table records the error and the exception re-raises
    so the caller can surface it.
    """
    started_at = utc_now_iso()
    db.set_setting('db_backup_last_run', started_at)

    lock_path = Path(db.data_dir) / LOCK_FILENAME
    lock_fd = open(lock_path, 'w')
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupInProgressError('a backup is already in progress')

        start = time.monotonic()
        tmp = None
        try:
            dest = validate_backup_dest(db.get_setting('db_backup_dest') or '', db.data_dir)
            keep = _clamp_keep_count(db.get_setting('db_backup_keep_count'))
            tmp = dest / TEMP_BACKUP_NAME

            # snapshot_database creates the dest dir; make sure a stale temp
            # from a crashed run can't leave the rename pointing at old bytes.
            tmp.unlink(missing_ok=True)
            snapshot_database(db, tmp)

            if keep == 1:
                mode = 'overwrite'
                final = dest / FIXED_BACKUP_NAME
            else:
                mode = 'rotate'
                final = _next_rotated_path(dest, _utc_now())
            os.replace(tmp, final)

            # Read size before pruning so a prune race can never leave us
            # stat-ing a file that was just unlinked; prune skips `final`.
            size_bytes = final.stat().st_size
            pruned = _prune_rotated(dest, keep, keep_path=final)
        except Exception as e:
            db.set_setting('db_backup_last_error', str(e))
            logger.warning('db_backup: backup failed: %s', e)
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        summary = {
            'path': str(final),
            'sizeBytes': size_bytes,
            'durationMs': int((time.monotonic() - start) * 1000),
            'mode': mode,
            'keepCount': keep,
            'prunedCount': pruned,
            'finishedAt': utc_now_iso(),
        }
        db.set_setting('db_backup_last_error', '')
        db.set_setting('db_backup_last_summary', json.dumps(summary))
        logger.info('db_backup: %s', summary)
        return summary
    finally:
        lock_fd.close()


def db_backup_tick(db, force: bool = False) -> Optional[Dict[str, Any]]:
    """Run a backup if due (or forced). Returns the summary dict, or None."""
    enabled = db.get_setting_bool('db_backup_enabled', default=False)
    if not enabled and not force:
        return None

    cron = db.get_setting('db_backup_cron') or DEFAULT_CRON
    last_run = _parse_iso(db.get_setting('db_backup_last_run'))
    now = _utc_now()

    if not force and last_run is not None and not is_due(cron, last_run, now):
        return None

    try:
        return backup_now(db)
    except Exception:
        # backup_now already logged + stamped settings.
        return None

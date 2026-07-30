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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from secrets_crypto import count_plaintext_secrets, count_stored_secrets
from utils.cron import is_due
from utils.db_backup import snapshot_database
from utils.time import (
    parse_iso_utc as _parse_iso,
    utc_now as _utc_now,
    utc_now_iso,
)

logger = logging.getLogger('podcast.db_backup')

DEFAULT_CRON = '30 3 * * *'  # daily 03:30 UTC
FIXED_BACKUP_NAME = 'minuspod-backup.db'
TEMP_BACKUP_NAME = '.minuspod-backup.db.tmp'
ROTATED_NAME_RE = re.compile(r'^minuspod-backup-auto-\d{8}-\d{6}\.db$')
KEEP_COUNT_MIN, KEEP_COUNT_MAX = 1, 365
LOCK_FILENAME = '.db_backup.lock'


class BackupInProgressError(Exception):
    """Raised when another backup already holds the lock."""


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


def _rotated_name(stamp: str) -> str:
    # 'auto' namespaces scheduler-rotated files apart from operator downloads
    # (GET /system/backup names those minuspod-backup-<ts>.db). Without it the
    # prune regex would match and delete a saved download in the dest dir.
    return f'minuspod-backup-auto-{stamp}.db'


def _next_rotated_path(dest: Path, now: datetime) -> Path:
    """Return a non-colliding rotated backup path for `now`.

    Two backups in the same UTC second would otherwise share a filename and the
    second os.replace would overwrite the first, destroying a good snapshot. If
    the second-resolution name is taken, step forward a second until one is free
    so keepCount > 1 always retains distinct files.
    """
    for offset in range(60):
        stamp = (now + timedelta(seconds=offset)).strftime('%Y%m%d-%H%M%S')
        candidate = dest / _rotated_name(stamp)
        if not candidate.exists():
            return candidate
    # 60 taken names in a row is implausible; fall back to the base name.
    logger.warning('db_backup: rotation name space exhausted; base name will be overwritten')
    return dest / _rotated_name(now.strftime('%Y%m%d-%H%M%S'))


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


def dest_writable(path: Path) -> bool:
    """Report whether a backup could be written under `path`.

    Matches mkdir(parents=True) semantics: if `path` exists it must be a
    writable directory; if it does not, walk up to the nearest existing
    ancestor and require it to be a writable directory (mkdir would create the
    missing levels under it).
    """
    if path.exists():
        return path.is_dir() and os.access(path, os.W_OK)
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return ancestor.is_dir() and os.access(ancestor, os.W_OK)


def _ensure_dest_dir(dest: Path) -> None:
    """Create `dest` if missing, chmod 0o700 ONLY the directories we create.

    A pre-existing destination (e.g. an operator-chosen shared mount) keeps its
    own permissions untouched; we never chmod ancestors we did not create.
    """
    if dest.exists():
        return
    # Find the lowest existing ancestor; everything below it we are creating.
    top_created = dest
    while not top_created.parent.exists() and top_created.parent != top_created:
        top_created = top_created.parent
    dest.mkdir(parents=True, exist_ok=True)
    # Tighten only the subtree we just created, leaf-first is unnecessary since
    # 0700 on each created level is sufficient; walk down from top_created.
    node = top_created
    while True:
        try:
            node.chmod(0o700)
        except OSError:
            logger.warning('db_backup: could not tighten permissions on %s', node)
        if node == dest:
            break
        # Descend one level toward dest.
        rel = dest.relative_to(node)
        node = node / rel.parts[0]


_secret_state_logged = False


def reset_secret_state_log() -> None:
    """Clear the once-per-process latch. For tests."""
    global _secret_state_logged
    _secret_state_logged = False


def _log_snapshot_secret_state(db, final, summary) -> None:
    """Log once per process what a snapshot exposes.

    Provider secrets are ciphertext whenever MINUSPOD_MASTER_PASSPHRASE is set,
    because Database.set_secret encrypts on write, so a configured passphrase is
    the safe case. The exposed case is a snapshot taken with none, where the keys
    are readable. The previous condition warned on exactly the wrong one.
    """
    global _secret_state_logged
    if _secret_state_logged:
        logger.debug('db_backup: %s', summary)
        return
    _secret_state_logged = True
    try:
        plaintext = count_plaintext_secrets(db)
        stored = 0 if plaintext else count_stored_secrets(db)
    except Exception as e:
        logger.debug('db_backup: could not count plaintext secrets: %s', e)
        logger.info('db_backup: %s', summary)
        return
    # The counts stay out of the log lines: CodeQL classifies any value
    # returned by a secret-named helper as sensitive, count or not.
    if plaintext:
        logger.warning(
            'db_backup: snapshot at %s holds provider secrets in plaintext. '
            'Set MINUSPOD_MASTER_PASSPHRASE to encrypt them at rest; stored '
            'values migrate on the next write. %s',
            final, summary,
        )
    elif stored:
        logger.info(
            'db_backup: snapshot at %s has encrypted provider secrets. The '
            'key-derivation salt is inside the file, so keep the passphrase '
            'somewhere other than beside the backup. %s',
            final, summary,
        )
    else:
        logger.info(
            'db_backup: snapshot at %s contains no provider secrets. %s',
            final, summary,
        )


def backup_now(db) -> Dict[str, Any]:
    """Run a backup now regardless of schedule. Returns a summary dict.

    Stamps last_run immediately after acquiring the lock (community-sync
    convention: a persistently failing backup retries at the next cron fire, not
    every tick). On failure the settings table records the error and the
    exception re-raises so the caller can surface it.
    """
    lock_path = Path(db.data_dir) / LOCK_FILENAME
    lock_fd = open(lock_path, 'w')
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupInProgressError('a backup is already in progress')

        # Stamp last_run only once we hold the lock and a backup genuinely runs;
        # a contending caller must not consume the retry slot.
        db.set_setting('db_backup_last_run', utc_now_iso())

        start = time.monotonic()
        tmp = None
        try:
            dest = validate_backup_dest(db.get_setting('db_backup_dest') or '', db.data_dir)
            keep = _clamp_keep_count(db.get_setting('db_backup_keep_count'))
            tmp = dest / TEMP_BACKUP_NAME

            # Create the dest dir ourselves so we chmod 0700 only directories we
            # create; a pre-existing user destination keeps its permissions.
            _ensure_dest_dir(dest)

            # Make sure a stale temp from a crashed run can't leave the rename
            # pointing at old bytes.
            tmp.unlink(missing_ok=True)
            snapshot_database(db, tmp, tighten_dir_perms=False)

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
        _log_snapshot_secret_state(db, final, summary)
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
    except BackupInProgressError:
        # Another backup genuinely runs and stamped last_run itself; skip
        # quietly and do not stamp last_error (nothing failed).
        logger.info('db_backup: skipped, another backup is in progress')
        return None
    except Exception:
        # backup_now already logged + stamped settings.
        return None

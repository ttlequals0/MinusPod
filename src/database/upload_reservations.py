"""Durable reservations for local episode and staging uploads."""
import os
import logging
import threading
import uuid
from pathlib import Path

from utils.time import utc_now_iso

logger = logging.getLogger(__name__)
_UPLOAD_RECOVERY_LOCK = threading.Lock()


def _pid_stat(pid: int) -> tuple[float, str] | None:
    try:
        with open(f'/proc/{pid}/stat') as fd:
            fields = fd.read().rpartition(')')[2].split()
        return float(fields[19]), fields[0]
    except (OSError, IndexError, ValueError):
        return None


def _owner_dead(pid: int, recorded_start: float | None) -> bool:
    stat = _pid_stat(pid)
    if stat is not None and stat[1] == 'Z':
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return recorded_start is not None and stat is not None and stat[0] != recorded_start


class UploadReservationMixin:
    def _recover_upload_files(self, row: dict) -> bool:
        if row['state'] == 'prepared':
            try:
                if row['operation'] != 'import' and row['temp_name']:
                    base = Path(self.data_dir).resolve()
                    temp = (base / row['temp_name']).resolve()
                    temp.relative_to(base)
                    if temp.exists():
                        temp.unlink()
                return True
            except (OSError, ValueError) as exc:
                logger.warning("Could not recover upload reservation %s: %s", row['id'], exc)
                return False
        if row['state'] != 'publishing':
            return True
        try:
            base = Path(self.data_dir).resolve()
            temp = (base / row['temp_name']).resolve() if row['temp_name'] else None
            backup = (base / row['backup_name']).resolve() if row['backup_name'] else None
            backup_target = ((base / row['backup_target_name']).resolve()
                             if row['backup_target_name'] else None)
            if temp is not None:
                temp.relative_to(base)
            if backup is not None:
                backup.relative_to(base)
            if row['scope'] == 'staging':
                final = (base / 'import-staging' / row['slug'] / row['target_key']).resolve()
            else:
                final = (base / 'podcasts' / row['slug'] / 'episodes'
                         / f"{row['target_key']}-original.mp3").resolve()
            final.relative_to(base)
            if backup_target is None:
                backup_target = final
            backup_target.relative_to(base)
            if row['operation'] == 'import' and final.exists() and temp is not None:
                temp.parent.mkdir(parents=True, exist_ok=True)
                if not temp.exists():
                    os.replace(final, temp)
            elif (row['operation'] != 'import' and temp is not None
                  and final.exists() and not temp.exists()):
                temp.parent.mkdir(parents=True, exist_ok=True)
                os.replace(final, temp)
            if backup is not None and backup.exists():
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, backup_target)
            return True
        except (OSError, ValueError) as exc:
            logger.warning("Could not recover upload reservation %s: %s", row['id'], exc)
            return False

    def _finish_upload_recovery(self, row: dict) -> bool:
        if not self._recover_upload_files(row):
            return False
        owner_pid, owner_start = self._upload_owner()
        now = utc_now_iso()
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE upload_reservations SET state = 'failed', "
                "recovery_state = NULL, updated_at = ?, finished_at = ? "
                "WHERE id = ? AND owner_pid = ? AND owner_pid_start IS ? "
                "AND state = 'publishing' AND recovery_state IS NOT NULL",
                (now, now, row['id'], owner_pid, owner_start),
            )
        if cursor.rowcount != 1:
            return False
        if row['operation'] != 'import' and row['temp_name']:
            self._cleanup_terminal_temp(row)
        return True

    def _cleanup_terminal_temp(self, row: dict) -> None:
        try:
            base = Path(self.data_dir).resolve()
            temp = (base / row['temp_name']).resolve()
            temp.relative_to(base)
            if temp.exists():
                temp.unlink()
        except (OSError, ValueError) as exc:
            logger.warning("Could not clean failed upload temp %s: %s", row['id'], exc)
            return
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE upload_reservations SET temp_name = NULL "
                "WHERE id = ? AND state = 'failed'",
                (row['id'],),
            )

    def _cleanup_published_backup(self, row: dict) -> None:
        try:
            base = Path(self.data_dir).resolve()
            backup = (base / row['backup_name']).resolve()
            backup.relative_to(base)
            if backup.exists():
                backup.unlink()
            with self.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE upload_reservations SET backup_name = NULL, "
                    "backup_target_name = NULL "
                    "WHERE id = ? AND state = 'published'",
                    (row['id'],),
                )
        except (OSError, ValueError) as exc:
            logger.warning("Could not clean published upload backup %s: %s", row['id'], exc)
        except Exception as exc:
            logger.warning("Could not record published upload cleanup %s: %s", row['id'], exc)

    def cleanup_published_upload_backup(self, reservation_id: str) -> None:
        try:
            row = self.get_connection().execute(
                "SELECT * FROM upload_reservations "
                "WHERE id = ? AND state = 'published' AND backup_name IS NOT NULL",
                (reservation_id,),
            ).fetchone()
        except Exception as exc:
            logger.warning("Could not read published upload cleanup %s: %s", reservation_id, exc)
            return
        if row:
            self._cleanup_published_backup(dict(row))

    def _reap_upload_reservations(self) -> list[dict]:
        owner_pid, owner_start = self._upload_owner()
        with _UPLOAD_RECOVERY_LOCK:
            claimed = []
            published = []
            failed = []
            with self.transaction(immediate=True) as conn:
                rows = conn.execute(
                    "SELECT r.*, p.slug FROM upload_reservations r "
                    "JOIN podcasts p ON p.id = r.podcast_id "
                    "WHERE r.state IN ('reserved', 'prepared', 'publishing')"
                ).fetchall()
                for raw in rows:
                    row = dict(raw)
                    resumable = (
                        row['recovery_state'] is not None
                        and row['owner_pid'] == owner_pid
                        and row['owner_pid_start'] == owner_start
                    )
                    if not resumable and not _owner_dead(
                            row['owner_pid'], row['owner_pid_start']):
                        continue
                    recovery_state = row['recovery_state'] or row['state']
                    # Keep the target reserved until filesystem rollback finishes.
                    cursor = conn.execute(
                        "UPDATE upload_reservations SET state = 'publishing', "
                        "recovery_state = ?, owner_pid = ?, owner_pid_start = ?, "
                        "updated_at = ? WHERE id = ? AND owner_pid = ? "
                        "AND owner_pid_start IS ? "
                        "AND state IN ('reserved', 'prepared', 'publishing')",
                        (recovery_state, owner_pid, owner_start, utc_now_iso(),
                         row['id'], row['owner_pid'], row['owner_pid_start']),
                    )
                    if cursor.rowcount:
                        row['state'] = recovery_state
                        claimed.append(row)
                published = [dict(row) for row in conn.execute(
                    "SELECT r.*, p.slug FROM upload_reservations r "
                    "JOIN podcasts p ON p.id = r.podcast_id "
                    "WHERE r.state = 'published' AND r.backup_name IS NOT NULL"
                ).fetchall()]
                failed = [dict(row) for row in conn.execute(
                    "SELECT r.*, p.slug FROM upload_reservations r "
                    "JOIN podcasts p ON p.id = r.podcast_id "
                    "WHERE r.state = 'failed' AND r.operation != 'import' "
                    "AND r.temp_name IS NOT NULL"
                ).fetchall()]

            reaped = [row for row in claimed if self._finish_upload_recovery(row)]
            for row in published:
                self._cleanup_published_backup(row)
            for row in failed:
                self._cleanup_terminal_temp(row)
            return reaped

    @staticmethod
    def _upload_owner() -> tuple[int, float | None]:
        pid = os.getpid()
        stat = _pid_stat(pid)
        return pid, stat[0] if stat else None

    def reserve_next_episode_upload(self, slug: str, season: int) -> dict | None:
        owner_pid, owner_start = self._upload_owner()
        self._reap_upload_reservations()
        with self.transaction(immediate=True) as conn:
            podcast = conn.execute(
                "SELECT id FROM podcasts WHERE slug = ? AND deletion_requested_at IS NULL",
                (slug,),
            ).fetchone()
            if not podcast:
                return None
            row = conn.execute(
                "SELECT MAX(number) AS number FROM ("
                "SELECT episode_number AS number FROM episodes "
                "WHERE podcast_id = ? AND season_number = ? UNION ALL "
                "SELECT episode_number AS number FROM upload_reservations "
                "WHERE podcast_id = ? AND scope = 'episode' AND season_number = ? "
                "AND state IN ('reserved', 'prepared', 'publishing'))",
                (podcast['id'], season, podcast['id'], season),
            ).fetchone()
            episode_number = (row['number'] or 0) + 1
            episode_id = f's{season:02d}e{episode_number:02d}'
            reservation_id = uuid.uuid4().hex
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO upload_reservations "
                "(id, podcast_id, scope, target_key, operation, season_number, "
                "episode_number, owner_pid, owner_pid_start, state, created_at, updated_at) "
                "VALUES (?, ?, 'episode', ?, 'individual', ?, ?, ?, ?, 'reserved', ?, ?)",
                (reservation_id, podcast['id'], episode_id, season, episode_number,
                 owner_pid, owner_start, now, now),
            )
            return {'id': reservation_id, 'episode_id': episode_id,
                    'episode_number': episode_number}

    def reserve_episode_uploads(self, slug: str, episode_ids: list[str],
                                operation: str, overwrite: bool = False) -> dict:
        if operation not in ('individual', 'import'):
            raise ValueError('invalid upload operation')
        owner_pid, owner_start = self._upload_owner()
        unique_ids = list(dict.fromkeys(episode_ids))
        self._reap_upload_reservations()
        with self.transaction(immediate=True) as conn:
            podcast = conn.execute(
                "SELECT id FROM podcasts WHERE slug = ? AND deletion_requested_at IS NULL",
                (slug,),
            ).fetchone()
            if not podcast:
                return {'reserved': {}, 'conflicts': unique_ids}
            conflicts = []
            for episode_id in unique_ids:
                active = conn.execute(
                    "SELECT 1 FROM upload_reservations WHERE podcast_id = ? "
                    "AND scope = 'episode' AND target_key = ? "
                    "AND state IN ('reserved', 'prepared', 'publishing')",
                    (podcast['id'], episode_id),
                ).fetchone()
                existing = conn.execute(
                    "SELECT status, deletion_requested_at FROM episodes "
                    "WHERE podcast_id = ? AND episode_id = ?",
                    (podcast['id'], episode_id),
                ).fetchone()
                processing = conn.execute(
                    "SELECT 1 FROM processing_runs WHERE podcast_id = ? "
                    "AND episode_id = ? AND state IN ('running', 'cancel_requested')",
                    (podcast['id'], episode_id),
                ).fetchone()
                if (active or processing or (existing and (
                        existing['deletion_requested_at'] is not None
                        or not overwrite or existing['status'] == 'processing'))):
                    conflicts.append(episode_id)
            if conflicts:
                return {'reserved': {}, 'conflicts': conflicts}
            now = utc_now_iso()
            reserved = {}
            for episode_id in unique_ids:
                reservation_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO upload_reservations "
                    "(id, podcast_id, scope, target_key, operation, owner_pid, "
                    "owner_pid_start, state, created_at, updated_at) "
                    "VALUES (?, ?, 'episode', ?, ?, ?, ?, 'reserved', ?, ?)",
                    (reservation_id, podcast['id'], episode_id, operation,
                     owner_pid, owner_start, now, now),
                )
                reserved[episode_id] = reservation_id
            return {'reserved': reserved, 'conflicts': []}

    def reserve_staging_upload(self, slug: str, basename: str) -> str | None:
        owner_pid, owner_start = self._upload_owner()
        self._reap_upload_reservations()
        with self.transaction(immediate=True) as conn:
            podcast = conn.execute(
                "SELECT id FROM podcasts WHERE slug = ? AND deletion_requested_at IS NULL",
                (slug,),
            ).fetchone()
            if not podcast:
                return None
            active = conn.execute(
                "SELECT 1 FROM upload_reservations WHERE podcast_id = ? "
                "AND scope = 'staging' AND target_key = ? "
                    "AND state IN ('reserved', 'prepared', 'publishing')",
                (podcast['id'], basename),
            ).fetchone()
            if active:
                return None
            reservation_id = uuid.uuid4().hex
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO upload_reservations "
                "(id, podcast_id, scope, target_key, operation, owner_pid, "
                "owner_pid_start, state, created_at, updated_at) "
                "VALUES (?, ?, 'staging', ?, 'staging', ?, ?, 'reserved', ?, ?)",
                (reservation_id, podcast['id'], basename, owner_pid, owner_start, now, now),
            )
            return reservation_id

    def prepare_upload_reservation(self, reservation_id: str,
                                   temp_name: str | None = None) -> bool:
        owner_pid, owner_start = self._upload_owner()
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE upload_reservations SET state = 'prepared', temp_name = ?, "
            "updated_at = ? WHERE id = ? AND owner_pid = ? AND owner_pid_start IS ? "
            "AND state = 'reserved'",
            (temp_name, utc_now_iso(), reservation_id, owner_pid, owner_start),
        )
        conn.commit()
        return cursor.rowcount == 1

    def owns_upload_reservation(self, reservation_id: str,
                                state: str = 'prepared') -> bool:
        owner_pid, owner_start = self._upload_owner()
        try:
            row = self.get_connection().execute(
                "SELECT 1 FROM upload_reservations WHERE id = ? AND owner_pid = ? "
                "AND owner_pid_start IS ? AND state = ?",
                (reservation_id, owner_pid, owner_start, state),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def begin_upload_publication(self, reservation_id: str, temp_name: str,
                                 backup_name: str,
                                 backup_target_name: str | None = None) -> bool:
        owner_pid, owner_start = self._upload_owner()
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE upload_reservations SET state = 'publishing', temp_name = ?, "
            "backup_name = ?, backup_target_name = ?, updated_at = ? "
            "WHERE id = ? AND owner_pid = ? "
            "AND owner_pid_start IS ? AND state = 'prepared'",
            (temp_name, backup_name, backup_target_name, utc_now_iso(), reservation_id,
             owner_pid, owner_start),
        )
        conn.commit()
        return cursor.rowcount == 1

    def finish_upload_reservation(self, reservation_id: str, state: str,
                                  commit: bool = True) -> bool:
        if state not in ('published', 'failed', 'released'):
            raise ValueError('invalid upload reservation state')
        owner_pid, owner_start = self._upload_owner()
        now = utc_now_iso()
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE upload_reservations SET state = ?, updated_at = ?, finished_at = ? "
            "WHERE id = ? AND owner_pid = ? AND owner_pid_start IS ? "
            "AND state IN ('reserved', 'prepared', 'publishing')",
            (state, now, now, reservation_id, owner_pid, owner_start),
        )
        if commit:
            conn.commit()
        return cursor.rowcount == 1

    def fail_upload_reservation(self, reservation_id: str) -> bool:
        owner_pid, owner_start = self._upload_owner()
        with _UPLOAD_RECOVERY_LOCK:
            with self.transaction(immediate=True) as conn:
                raw = conn.execute(
                    "SELECT r.*, p.slug FROM upload_reservations r "
                    "JOIN podcasts p ON p.id = r.podcast_id "
                    "WHERE r.id = ? AND r.owner_pid = ? AND r.owner_pid_start IS ? "
                    "AND r.state IN ('reserved', 'prepared', 'publishing')",
                    (reservation_id, owner_pid, owner_start),
                ).fetchone()
                if not raw:
                    return False
                row = dict(raw)
                recovery_state = row['recovery_state'] or row['state']
                cursor = conn.execute(
                    "UPDATE upload_reservations SET state = 'publishing', "
                    "recovery_state = ?, updated_at = ? WHERE id = ? "
                    "AND owner_pid = ? AND owner_pid_start IS ? "
                    "AND state IN ('reserved', 'prepared', 'publishing')",
                    (recovery_state, utc_now_iso(), reservation_id,
                     owner_pid, owner_start),
                )
                if not cursor.rowcount:
                    return False
                row['state'] = recovery_state
            return self._finish_upload_recovery(row)

    def active_upload_reservations(self, podcast_id: int) -> list[dict]:
        self._reap_upload_reservations()
        with self.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT * FROM upload_reservations WHERE podcast_id = ? "
                "AND state IN ('reserved', 'prepared', 'publishing')",
                (podcast_id,),
            ).fetchall()
            return [dict(row) for row in rows]

"""Durable cross-process ownership for episode processing."""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from processing_timeouts import get_soft_timeout
from status_service import StatusService
from utils.paths import resolve_data_dir
from database import Database

logger = logging.getLogger('podcast.processing_queue')
PROCESSING_PAUSED_KEY = 'processing_admission_paused'


def is_processing_paused(database=None) -> bool:
    try:
        db = database or Database()
        return db.get_system_setting(PROCESSING_PAUSED_KEY) == 'true'
    except Exception as exc:
        logger.warning("Could not read processing admission state: %s", exc)
        return True


def set_processing_paused(paused: bool, database=None) -> None:
    db = database or Database()
    db.set_system_setting(PROCESSING_PAUSED_KEY, 'true' if paused else 'false')


def _pid_stat(pid: int) -> tuple[float, str] | None:
    """Return Linux process start ticks and state when both are readable."""
    try:
        with open(f"/proc/{pid}/stat") as fd:
            fields = fd.read().rpartition(')')[2].split()
        return float(fields[19]), fields[0]
    except (OSError, IndexError, ValueError):
        return None


def _pid_start_time(pid: int) -> float | None:
    stat = _pid_stat(pid)
    return stat[0] if stat else None


def _sync_status_clear(slug: str, episode_id: str, run_id: str) -> None:
    try:
        StatusService().clear_if_matches(slug, episode_id, run_id=run_id)
    except Exception as exc:
        logger.debug("Could not clear recovered processing status: %s", exc)


class ProcessingQueue:
    """SQLite-backed N-slot registry shared by every worker process."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        data_dir = resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_state_path = data_dir / '.processing_queue_state.json'
        self._initialized = True

    @staticmethod
    def _database():
        return Database()

    @staticmethod
    def _owner_identity() -> tuple[int, float | None]:
        pid = os.getpid()
        return pid, _pid_start_time(pid)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        stat = _pid_stat(pid)
        if stat is not None and stat[1] == 'Z':
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _owner_proven_dead(cls, pid: int, recorded_start: float | None) -> bool:
        if not cls._pid_alive(pid):
            return True
        current_start = _pid_start_time(pid)
        return (recorded_start is not None and current_start is not None
                and current_start != recorded_start)

    def _reconcile_dead_owners(self, conn) -> list[tuple[str, str, str]]:
        """Recover processing state only when its process identity is proven dead."""
        rows = conn.execute(
            "SELECT r.run_id, r.podcast_id, r.episode_id, r.owner_pid, r.owner_pid_start, "
            "r.heartbeat_at, p.slug FROM processing_runs r "
            "JOIN podcasts p ON p.id = r.podcast_id "
            "WHERE r.state IN ('running', 'cancel_requested')"
        ).fetchall()
        recovered = []
        now = datetime.now(timezone.utc)
        for row in rows:
            if self._owner_proven_dead(row['owner_pid'], row['owner_pid_start']):
                cursor = conn.execute(
                    "UPDATE processing_runs SET state = 'interrupted', "
                    "finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                    "WHERE run_id = ? AND owner_pid = ? AND owner_pid_start IS ? "
                    "AND state IN ('running', 'cancel_requested')",
                    (row['run_id'], row['owner_pid'], row['owner_pid_start']),
                )
                if cursor.rowcount:
                    successor = conn.execute(
                        "SELECT 1 FROM processing_runs WHERE podcast_id = ? "
                        "AND episode_id = ? "
                        "AND state IN ('running', 'cancel_requested')",
                        (row['podcast_id'], row['episode_id']),
                    ).fetchone()
                    if not successor:
                        conn.execute(
                            "UPDATE episodes SET status = 'pending', "
                            "error_message = 'Reset after worker crash (no retry penalty)' "
                            "WHERE podcast_id = ? AND episode_id = ? "
                            "AND status = 'processing'",
                            (row['podcast_id'], row['episode_id']),
                        )
                        conn.execute(
                            "UPDATE auto_process_queue SET status = 'pending', "
                            "error_message = "
                            "'Reset after worker crash (no attempt penalty)', "
                            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                            "WHERE podcast_id = ? AND episode_id = ? "
                            "AND status = 'processing'",
                            (row['podcast_id'], row['episode_id']),
                        )
                    recovered.append((row['slug'], row['episode_id'], row['run_id']))
                continue
            try:
                heartbeat = datetime.fromisoformat(row['heartbeat_at'].replace('Z', '+00:00'))
                if (now - heartbeat).total_seconds() > get_soft_timeout():
                    logger.warning("Long-running job still has a live owner: %s:%s",
                                   row['slug'], row['episode_id'])
            except (AttributeError, TypeError, ValueError):
                pass
        return recovered

    def acquire(self, slug: str, episode_id: str, limit: int = 1,
                timeout: float = 0) -> str | None:
        """Acquire a durable lease and return its immutable run ID."""
        deadline = time.monotonic() + max(0.0, timeout)
        owner_pid, owner_start = self._owner_identity()
        while True:
            conn = None
            recovered = []
            try:
                conn = self._database().get_connection()
                conn.execute('BEGIN IMMEDIATE')
                paused = conn.execute(
                    "SELECT value FROM system_settings WHERE key = ?",
                    (PROCESSING_PAUSED_KEY,),
                ).fetchone()
                if paused and paused['value'] == 'true':
                    conn.rollback()
                    return None
                podcast = conn.execute(
                    "SELECT id FROM podcasts WHERE slug = ? "
                    "AND deletion_requested_at IS NULL", (slug,)
                ).fetchone()
                if not podcast:
                    conn.rollback()
                    return None
                recovered = self._reconcile_dead_owners(conn)
                blocked = conn.execute(
                    "SELECT 1 FROM episodes WHERE podcast_id = ? AND episode_id = ? "
                    "AND deletion_requested_at IS NOT NULL UNION ALL "
                    "SELECT 1 FROM upload_reservations WHERE podcast_id = ? "
                    "AND scope = 'episode' AND target_key = ? "
                    "AND state IN ('reserved', 'prepared', 'publishing') LIMIT 1",
                    (podcast['id'], episode_id, podcast['id'], episode_id),
                ).fetchone()
                duplicate = conn.execute(
                    "SELECT 1 FROM processing_runs WHERE podcast_id = ? "
                    "AND episode_id = ? AND state IN ('running', 'cancel_requested')",
                    (podcast['id'], episode_id),
                ).fetchone()
                active = conn.execute(
                    "SELECT COUNT(*) FROM processing_runs "
                    "WHERE state IN ('running', 'cancel_requested')"
                ).fetchone()[0]
                if blocked or duplicate or active >= max(1, limit):
                    conn.commit()
                else:
                    run_id = uuid.uuid4().hex
                    conn.execute(
                        "INSERT INTO processing_runs "
                        "(run_id, podcast_id, episode_id, owner_pid, owner_pid_start, state) "
                        "VALUES (?, ?, ?, ?, ?, 'running')",
                        (run_id, podcast['id'], episode_id, owner_pid, owner_start),
                    )
                    conn.commit()
                    for recovered_slug, recovered_episode, recovered_run in recovered:
                        _sync_status_clear(recovered_slug, recovered_episode, recovered_run)
                    logger.info("Processing lease acquired for %s:%s (%d/%d)",
                                slug, episode_id, active + 1, limit)
                    return run_id
            except Exception as exc:
                try:
                    if conn is not None:
                        conn.rollback()
                except Exception:
                    pass
                logger.warning("Durable processing acquire failed for %s:%s: %s",
                               slug, episode_id, exc)
                return None
            for recovered_slug, recovered_episode, recovered_run in recovered:
                _sync_status_clear(recovered_slug, recovered_episode, recovered_run)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def poll(self, run_id: str) -> str | None:
        """Refresh an owned lease and return its state, or None on any failure."""
        owner_pid, owner_start = self._owner_identity()
        conn = None
        try:
            conn = self._database().get_connection()
            row = conn.execute(
                "SELECT state, heartbeat_at FROM processing_runs WHERE run_id = ? "
                "AND owner_pid = ? AND owner_pid_start IS ? "
                "AND state IN ('running', 'cancel_requested')",
                (run_id, owner_pid, owner_start),
            ).fetchone()
            if not row:
                return None
            try:
                heartbeat = datetime.fromisoformat(row['heartbeat_at'].replace('Z', '+00:00'))
                due = (datetime.now(timezone.utc) - heartbeat).total_seconds() >= 30
            except (AttributeError, TypeError, ValueError):
                due = True
            if due:
                conn.execute(
                    "UPDATE processing_runs SET heartbeat_at = "
                    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE run_id = ? "
                    "AND owner_pid = ? AND owner_pid_start IS ? "
                    "AND state IN ('running', 'cancel_requested')",
                    (run_id, owner_pid, owner_start),
                )
                conn.commit()
            return row['state']
        except Exception as exc:
            try:
                if conn is not None:
                    conn.rollback()
            except Exception:
                pass
            logger.warning("Durable processing poll failed for run %s: %s", run_id, exc)
            return None

    def owns(self, run_id: str, allow_cancel_requested: bool = False) -> bool:
        state = self.poll(run_id)
        return state == 'running' or (allow_cancel_requested and state == 'cancel_requested')

    def release(self, run_id: str, terminal_state: str = 'finished') -> bool:
        """Release only the lease owned by this process and immutable run ID."""
        if terminal_state not in ('finished', 'interrupted'):
            raise ValueError(f"Invalid processing terminal state: {terminal_state}")
        owner_pid, owner_start = self._owner_identity()
        try:
            conn = self._database().get_connection()
            cursor = conn.execute(
                "UPDATE processing_runs SET state = ?, "
                "finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE run_id = ? AND owner_pid = ? AND owner_pid_start IS ? "
                "AND state IN ('running', 'cancel_requested')",
                (terminal_state, run_id, owner_pid, owner_start),
            )
            conn.commit()
            if cursor.rowcount:
                logger.info("Processing lease released for run %s", run_id)
                return True
        except Exception as exc:
            logger.warning("Could not release processing run %s: %s", run_id, exc)
        return False

    def get_current(self) -> list[tuple[str, str]]:
        """Return active episodes oldest first without taking a write lock."""
        try:
            rows = self._database().get_connection().execute(
                "SELECT p.slug, r.episode_id FROM processing_runs r "
                "JOIN podcasts p ON p.id = r.podcast_id "
                "WHERE r.state IN ('running', 'cancel_requested') "
                "ORDER BY r.started_at, r.rowid"
            ).fetchall()
            return [(row['slug'], row['episode_id']) for row in rows]
        except Exception as exc:
            logger.warning("Could not read durable processing runs: %s", exc)
            return []

    def active_run_id(self, slug: str, episode_id: str) -> str | None:
        """Return the active run ID, failing closed with an opaque sentinel."""
        try:
            row = self._database().get_connection().execute(
                "SELECT r.run_id FROM processing_runs r "
                "JOIN podcasts p ON p.id = r.podcast_id "
                "WHERE p.slug = ? AND r.episode_id = ? "
                "AND r.state IN ('running', 'cancel_requested')",
                (slug, episode_id),
            ).fetchone()
            return row['run_id'] if row else None
        except Exception as exc:
            logger.warning("Could not read durable processing state for %s:%s: %s",
                           slug, episode_id, exc)
            return '__database_unavailable__'

    def slot_count(self) -> int:
        try:
            return self._database().get_connection().execute(
                "SELECT COUNT(*) FROM processing_runs "
                "WHERE state IN ('running', 'cancel_requested')"
            ).fetchone()[0]
        except Exception as exc:
            logger.warning("Could not count durable processing runs: %s", exc)
            return 2 ** 31 - 1

    def owned_active_run_ids(self) -> list[str] | None:
        """Return this process's active runs, or None when ownership is unknown."""
        owner_pid, owner_start = self._owner_identity()
        try:
            rows = self._database().get_connection().execute(
                "SELECT run_id FROM processing_runs WHERE owner_pid = ? "
                "AND owner_pid_start IS ? "
                "AND state IN ('running', 'cancel_requested')",
                (owner_pid, owner_start),
            ).fetchall()
            return [row['run_id'] for row in rows]
        except Exception as exc:
            logger.warning("Could not read owned processing runs: %s", exc)
            return None

    def is_processing(self, slug: str, episode_id: str) -> bool:
        return self.active_run_id(slug, episode_id) is not None

    def clear_all(self) -> int:
        """Test cleanup helper that interrupts only this process's leases."""
        owner_pid, owner_start = self._owner_identity()
        conn = self._database().get_connection()
        cursor = conn.execute(
            "UPDATE processing_runs SET state = 'interrupted', "
            "finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE owner_pid = ? AND owner_pid_start IS ? "
            "AND state IN ('running', 'cancel_requested')",
            (owner_pid, owner_start),
        )
        conn.commit()
        return cursor.rowcount

    def drop_slots_without_start_time(self) -> int:
        """Remove the obsolete JSON registry; SQLite is the sole authority."""
        try:
            self._legacy_state_path.unlink()
            logger.info("Removed obsolete processing queue state file")
            return 1
        except FileNotFoundError:
            return 0
        except OSError as exc:
            logger.warning("Could not remove obsolete processing queue state: %s", exc)
            return 0

    def reconcile_dead_owners(self) -> int:
        """Reclaim proven-dead owners during startup or before admission."""
        conn = self._database().get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            recovered = self._reconcile_dead_owners(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        for slug, episode_id, run_id in recovered:
            _sync_status_clear(slug, episode_id, run_id)
        return len(recovered)

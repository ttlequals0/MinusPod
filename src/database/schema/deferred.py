"""One-shot upgrade steps that scan data in proportion to the instance, run off the startup lock.

Startup only records a step as pending. A background thread claims it in
deferred_upgrade_steps, works one short transaction per page and saves a cursor
with each page, so a crash at any page boundary is finished by the next claim.
"""
import json
import logging
import os
import time
import uuid

from database.fingerprint_matches import BACKFILL_GATE, OUT_OF_SCOPE_LOG_CAP
from utils.time import utc_now_iso

logger = logging.getLogger(__name__)

REDACT_GATE = 'redact_stored_error_text'
STEP_NAMES = (REDACT_GATE, BACKFILL_GATE)
STALE_HEARTBEAT_SECONDS = 120.0
REDACT_PAGE_ROWS = 500
BACKFILL_PAGE_EPISODES = 100


class StepClaimLost(Exception):
    """Another worker took the step over after this one's heartbeat went stale."""


def _is_done(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone() is not None


class DeferredUpgradeMixin:
    """Registration, claiming and paged execution of the deferred upgrade steps."""

    def _register_deferred_upgrade_steps(self, conn, fresh: bool) -> None:
        """Record every unfinished step as pending; a fresh install has nothing to do and marks them done."""
        for name in STEP_NAMES:
            if _is_done(conn, name):
                state = 'done'
            elif fresh:
                conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,))
                state = 'done'
            else:
                state = 'pending'
            # The migration mark is the truth: a mark removed by hand puts a finished step back to pending.
            conn.execute(
                """INSERT INTO deferred_upgrade_steps (name, state) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET state = excluded.state, owner = NULL, cursor = NULL,
                     heartbeat_at = NULL, rows_done = 0
                   WHERE deferred_upgrade_steps.state != excluded.state
                     AND (excluded.state = 'done' OR deferred_upgrade_steps.state = 'done')""",
                (name, state))
        conn.commit()

    def pending_upgrade_steps(self) -> list[str]:
        """Names of the steps whose migration mark is not set yet."""
        conn = self.get_connection()
        return [name for name in STEP_NAMES if not _is_done(conn, name)]

    def history_backfill_pending(self) -> bool:
        """Whether the match history backfill has not finished."""
        return not _is_done(self.get_connection(), BACKFILL_GATE)

    def _claim_upgrade_step(self, name: str, owner: str, now: float,
                            stale_after: float = STALE_HEARTBEAT_SECONDS) -> dict | None:
        """Take a pending step, or a running one whose heartbeat is stale; None when someone else holds it."""
        with self.transaction(immediate=True) as conn:
            if _is_done(conn, name):
                return None
            conn.execute("INSERT OR IGNORE INTO deferred_upgrade_steps (name, state) VALUES (?, 'pending')", (name,))
            claimed = conn.execute(
                """UPDATE deferred_upgrade_steps SET state = 'running', owner = ?, heartbeat_at = ?
                   WHERE name = ? AND (state IN ('pending', 'done')
                     OR (state = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)))""",
                (owner, now, name, now - stale_after)).rowcount
            if not claimed:
                return None
            row = conn.execute("SELECT cursor, rows_done FROM deferred_upgrade_steps WHERE name = ?",
                               (name,)).fetchone()
        return {'cursor': json.loads(row['cursor']) if row['cursor'] else {}, 'rows_done': row['rows_done']}

    def _release_upgrade_step(self, name: str, owner: str) -> None:
        """Hand a claimed step back as pending, keeping its cursor."""
        with self.transaction(immediate=True) as conn:
            conn.execute("UPDATE deferred_upgrade_steps SET state = 'pending', owner = NULL, heartbeat_at = NULL "
                         "WHERE name = ? AND owner = ? AND state = 'running'", (name, owner))

    def run_deferred_upgrade_steps(self, clock=time.time, pause=lambda: None, should_stop=lambda: False) -> bool:
        """Run every unfinished step this process can claim; False when a step failed.

        A step another worker holds is skipped. should_stop is polled between pages.
        """
        owner = f'{os.getpid()}:{uuid.uuid4().hex[:8]}'
        ok = True
        for name in STEP_NAMES:
            claim = self._claim_upgrade_step(name, owner, clock())
            if claim is None:
                continue
            try:
                self._run_claimed_step(name, owner, claim, clock, pause, should_stop)
            except StepClaimLost:
                logger.warning("Upgrade step %s was taken over by another worker", name)
            except Exception as e:
                ok = False
                logger.error("Upgrade step %s failed, it resumes on the next start: %s: %s",
                             name, type(e).__name__, e)
                self.clear_leaked_transaction(logger, f'upgrade step {name}')
                self._release_upgrade_step(name, owner)
        return ok

    def _run_claimed_step(self, name, owner, claim, clock, pause, should_stop) -> None:
        advance = {REDACT_GATE: self._advance_redaction, BACKFILL_GATE: self._advance_backfill}[name]
        started = clock()
        ctx = {'rows': claim['rows_done'], 'out_of_scope': set()}
        cursor = claim['cursor']
        logger.info("Upgrade step %s started%s", name, f", resuming at {cursor}" if cursor else "")

        def save(conn, new_cursor, rows):
            ctx['rows'] += rows
            moved = conn.execute(
                """UPDATE deferred_upgrade_steps SET cursor = ?, heartbeat_at = ?, rows_done = rows_done + ?
                   WHERE name = ? AND owner = ? AND state = 'running'""",
                (json.dumps(new_cursor), clock(), rows, name, owner)).rowcount
            if not moved:
                raise StepClaimLost(name)

        while True:
            if should_stop():
                self._release_upgrade_step(name, owner)
                return
            cursor = advance(cursor, save, ctx)
            if cursor is None:
                break
            pause()
        with self.transaction(immediate=True) as conn:
            conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,))
            conn.execute("UPDATE deferred_upgrade_steps SET state = 'done', owner = NULL, cursor = NULL "
                         "WHERE name = ? AND owner = ?", (name, owner))
        logger.info("Upgrade step %s finished: %s row(s) in %.2fs%s", name, ctx['rows'], clock() - started,
                    self._scope_note(ctx) if name == BACKFILL_GATE else "")

    @staticmethod
    def _scope_note(ctx) -> str:
        ids = sorted(ctx['out_of_scope'])
        if not ids:
            return ""
        more = len(ids) - OUT_OF_SCOPE_LOG_CAP
        return (f"; patterns with rows out of scope: [{', '.join(map(str, ids[:OUT_OF_SCOPE_LOG_CAP]))}]"
                + (f" and {more} more" if more > 0 else ""))

    def _advance_redaction(self, cursor, save, ctx):
        """Redact one page of one column; returns the next cursor, None when every column is done."""
        units = self._redaction_units()
        unit, last = cursor.get('unit', 0), cursor.get('last', 0)
        conn = self.get_connection()
        while unit < len(units):
            table, column, rewrite, where, params = units[unit]
            if not self._table_exists(conn, table) or column not in self._get_table_columns(conn, table):
                unit, last = unit + 1, 0
                continue
            rows = conn.execute(
                f"SELECT rowid AS rid, {column} AS value FROM {table} "  # noqa: S608
                f"WHERE rowid > ? AND {column} IS NOT NULL AND ({where}) ORDER BY rowid LIMIT ?",
                (last, *params, REDACT_PAGE_ROWS)).fetchall()
            if not rows:
                unit, last = unit + 1, 0
                continue
            updates = [(redacted, row['rid'], row['value']) for row in rows
                       if isinstance(row['value'], str) and (redacted := rewrite(row['value'])) != row['value']]
            new_cursor = {'unit': unit, 'last': rows[-1]['rid']}
            with self.transaction(immediate=True) as write:
                # The old value guards against overwriting a concurrent update with a redaction of stale text.
                write.executemany(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ? AND {column} = ?", updates)  # noqa: S608
                save(write, new_cursor, len(updates))
            return new_cursor
        return None

    def _advance_backfill(self, cursor, save, ctx):
        """Write match history for one page of episodes, then the fingerprint origins; None when done."""
        conn = self.get_connection()
        if cursor.get('phase') != 'origins':
            if 'rejections' not in ctx:
                if not conn.execute("SELECT EXISTS(SELECT 1 FROM audio_fingerprints)").fetchone()[0]:
                    return None
                ctx['rejections'] = self._stored_rejections(utc_now_iso())
            inserts, last = self._read_stored_match_history(
                cursor.get('after', 0), BACKFILL_PAGE_EPISODES, ctx['rejections'])
            if last is None:
                return {'phase': 'origins'}
            new_cursor = {'after': last}
            with self.transaction(immediate=True) as write:
                written = self._insert_new_episode_matches(write, inserts)
                save(write, new_cursor, len(written))
            ctx['out_of_scope'].update(row[0] for row in written if not row[7])
            return new_cursor
        sources = self._origin_sources(self._stored_match_rows_of_unsourced_patterns())
        with self.transaction(immediate=True) as write:
            write.executemany(
                """UPDATE audio_fingerprints SET source_podcast_slug = ?, source_episode_id = ?,
                     source_start = ?, source_end = ?
                   WHERE pattern_id = ? AND source_episode_id IS NULL""",
                [(*source, pattern_id) for pattern_id, source in sources.items()])
            save(write, cursor, 0)
        return None

    @staticmethod
    def _insert_new_episode_matches(conn, inserts: list[tuple]) -> list[tuple]:
        """Insert rows of episodes that have no history yet; returns the rows written."""
        have = {}
        written = []
        for row in inserts:
            key = (row[2], row[1])
            if key not in have:
                have[key] = conn.execute(
                    "SELECT 1 FROM audio_fingerprint_matches WHERE podcast_slug = ? AND episode_id = ? LIMIT 1",
                    key).fetchone() is not None
            if not have[key]:
                written.append(row)
        conn.executemany(
            """INSERT INTO audio_fingerprint_matches
                 (pattern_id, episode_id, podcast_slug, match_start, match_end, similarity,
                  coverage, in_scope, audio_only, rejected, rejected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", written)
        return written

    def _stored_match_rows_of_unsourced_patterns(self) -> list[tuple]:
        """History rows in the insert column order for patterns whose fingerprint has no source yet."""
        return [tuple(row) for row in self.get_connection().execute(
            """SELECT pattern_id, episode_id, podcast_slug, match_start, match_end, similarity, coverage,
                      in_scope, audio_only, rejected, rejected_at, created_at
               FROM audio_fingerprint_matches
               WHERE pattern_id IN (SELECT pattern_id FROM audio_fingerprints WHERE source_episode_id IS NULL)""")]

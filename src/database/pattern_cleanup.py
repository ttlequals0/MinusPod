"""Pattern cleanup mixin: review runs, suggestions, and review stamps on learned patterns."""
import json

from pattern_cleanup_hash import INVALID_MARKER, review_hash
from utils.time import utc_now_iso

# Learned patterns are the only ones the cleanup reviews.
LEARNED_PATTERN_WHERE = "ap.created_by = 'auto' AND ap.source = 'local' AND ap.is_active = 1"
# Extra rows fetched past batch_size so the caller's safety-net recheck can still fill a batch.
CANDIDATE_ROW_MARGIN = 25

_JSON_FIELDS = ('reasons', 'payload', 'before', 'applied')
_PATTERN_SUMMARY_FIELDS = (
    'scope', 'podcast_id', 'network_id', 'text_template', 'confirmation_count',
    'false_positive_count', 'last_matched_at', 'created_at', 'is_active', 'category')


def _decode_suggestion(row) -> dict:
    out = dict(row)
    for field in _JSON_FIELDS:
        raw = out.get(field)
        out[field] = json.loads(raw) if raw else None
    return out


class PatternCleanupMixin:
    def create_cleanup_run(self, *, forced: bool, trigger: str, started_at: str | None = None) -> int:
        conn = self.get_connection()
        cursor = conn.execute(
            "INSERT INTO pattern_cleanup_runs (forced, trigger, started_at, accounting_version) "
            "VALUES (?, ?, ?, 1)",
            (1 if forced else 0, trigger, started_at or utc_now_iso()))
        conn.commit()
        return cursor.lastrowid

    def fail_running_cleanup_runs(self, error: str) -> int:
        """Mark every row still `running` as failed; call only while holding the run lock."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE pattern_cleanup_runs SET status = 'failed', finished_at = ?, error = ? "
            "WHERE status = 'running'",
            (utc_now_iso(), error))
        conn.commit()
        return cursor.rowcount

    def finish_cleanup_run(self, run_id: int, *, status: str, reviewed: int, suggested: int,
                           skipped: int, error: str | None = None, model: str | None = None,
                           provider: str | None = None, credential_slot: str | None = None,
                           error_count: int = 0) -> None:
        conn = self.get_connection()
        conn.execute(
            """UPDATE pattern_cleanup_runs SET status = ?, finished_at = ?, reviewed_count = ?,
                   suggested_count = ?, skipped_count = ?, error_count = ?, error = ?, model = ?,
                   provider = ?, credential_slot = ?
               WHERE id = ?""",
            (status, utc_now_iso(), reviewed, suggested, skipped, error_count, error, model,
             provider, credential_slot, run_id))
        conn.commit()

    def get_cleanup_runs(self, limit: int = 20) -> list[dict]:
        cursor = self.get_connection().execute(
            "SELECT * FROM pattern_cleanup_runs ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in cursor.fetchall()]

    def get_latest_finished_cleanup_run(self) -> dict | None:
        row = self.get_connection().execute(
            "SELECT * FROM pattern_cleanup_runs WHERE status != 'running' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def upsert_cleanup_suggestion(self, run_id: int | None, pattern_id: int, kind: str,
                                  confidence: float | None, reasons: list, payload: dict,
                                  before: dict, conn=None) -> int:
        """Store a pending suggestion, replacing a pending one of the same kind; `conn` joins a caller transaction."""
        if conn is None:
            with self.transaction(immediate=True) as own:
                return self.upsert_cleanup_suggestion(run_id, pattern_id, kind, confidence,
                                                      reasons, payload, before, conn=own)
        conn.execute(
            "UPDATE pattern_cleanup_suggestions SET superseded_at = ? "
            "WHERE pattern_id = ? AND kind = ? AND status = 'pending' AND superseded_at IS NULL",
            (utc_now_iso(), pattern_id, kind))
        cursor = conn.execute(
            """INSERT INTO pattern_cleanup_suggestions
               (run_id, pattern_id, kind, confidence, reasons, payload, before, created_at,
                pattern_scope, podcast_slug)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?, scope,
                      CASE WHEN scope = 'podcast' THEN podcast_id END
               FROM ad_patterns WHERE id = ?""",
            (run_id, pattern_id, kind, confidence, json.dumps(reasons or []),
             json.dumps(payload or {}), json.dumps(before or {}), utc_now_iso(), pattern_id))
        return cursor.lastrowid

    def supersede_pending(self, pattern_id: int, conn=None) -> int:
        """Drop every pending suggestion of any kind for a pattern."""
        target = conn or self.get_connection()
        cursor = target.execute(
            "UPDATE pattern_cleanup_suggestions SET superseded_at = ? "
            "WHERE pattern_id = ? AND status = 'pending' AND superseded_at IS NULL",
            (utc_now_iso(), pattern_id))
        if conn is None:
            target.commit()
        return cursor.rowcount

    def get_approved_cleanup_suggestions(self, pattern_id: int, conn=None) -> list[dict]:
        rows = (conn or self.get_connection()).execute(
            "SELECT * FROM pattern_cleanup_suggestions WHERE pattern_id = ? AND status = 'approved'",
            (pattern_id,)).fetchall()
        return [_decode_suggestion(row) for row in rows]

    def get_cleanup_suggestion(self, suggestion_id: int, conn=None) -> dict | None:
        row = (conn or self.get_connection()).execute(
            "SELECT * FROM pattern_cleanup_suggestions WHERE id = ? AND superseded_at IS NULL",
            (suggestion_id,)).fetchone()
        return _decode_suggestion(row) if row else None

    def get_cleanup_suggestions(self, status: str | None = None, kind: str | None = None,
                                limit: int = 50, offset: int = 0,
                                before_id: int | None = None) -> list[dict]:
        """Return suggestions newest first; `before_id` pages by id and takes precedence over `offset`."""
        summary_cols = ', '.join(f'ap.{c} AS p_{c}' for c in _PATTERN_SUMMARY_FIELDS)
        query = f"""
            SELECT s.*, {summary_cols}, ks.name AS p_sponsor, pc.title AS p_podcast_title
            FROM pattern_cleanup_suggestions s
            JOIN ad_patterns ap ON ap.id = s.pattern_id
            LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id
            LEFT JOIN podcasts pc ON pc.slug = ap.podcast_id
            WHERE s.superseded_at IS NULL"""  # noqa: S608
        params: list = []
        if status:
            query += " AND s.status = ?"
            params.append(status)
        if kind:
            query += " AND s.kind = ?"
            params.append(kind)
        if before_id is not None:
            query += " AND s.id < ?"
            params.append(int(before_id))
        # Sort by id alone so cursor, offset and sort agree.
        query += " ORDER BY s.id DESC LIMIT ?"
        params.append(int(limit))
        if before_id is None:
            query += " OFFSET ?"
            params.append(int(offset))
        out = []
        for row in self.get_connection().execute(query, params).fetchall():
            raw = dict(row)
            pattern = {'id': raw['pattern_id']}
            for key in list(raw):
                if key.startswith('p_'):
                    pattern[key[2:]] = raw.pop(key)
            item = _decode_suggestion(raw)
            item['pattern'] = pattern
            out.append(item)
        return out

    def get_cleanup_pending_counts(self) -> dict:
        rows = self.get_connection().execute(
            "SELECT kind, COUNT(*) AS n FROM pattern_cleanup_suggestions "
            "WHERE status = 'pending' AND superseded_at IS NULL GROUP BY kind").fetchall()
        by_kind = {row['kind']: row['n'] for row in rows}
        return {'total': sum(by_kind.values()), 'byKind': by_kind}

    def set_cleanup_suggestion_status(self, suggestion_id: int, status: str,
                                      applied: dict | None = None, conn=None) -> None:
        """Set status and reviewed_at; `applied` is written only when given."""
        target = conn or self.get_connection()
        if applied is None:
            target.execute(
                "UPDATE pattern_cleanup_suggestions SET status = ?, reviewed_at = ? WHERE id = ?",
                (status, utc_now_iso(), suggestion_id))
        else:
            target.execute(
                "UPDATE pattern_cleanup_suggestions SET status = ?, reviewed_at = ?, applied = ? "
                "WHERE id = ?",
                (status, utc_now_iso(), json.dumps(applied), suggestion_id))
        if conn is None:
            target.commit()

    def has_pending_cleanup_suggestion(self, pattern_id: int, kind: str | None = None) -> bool:
        query = ("SELECT 1 FROM pattern_cleanup_suggestions "
                 "WHERE pattern_id = ? AND status = 'pending' AND superseded_at IS NULL")
        params: list = [pattern_id]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        return self.get_connection().execute(query + " LIMIT 1", params).fetchone() is not None

    def stamp_pattern_cleanup_reviewed(self, pattern_id: int, review_hash: str, conn=None) -> None:
        target = conn or self.get_connection()
        target.execute(
            "UPDATE ad_patterns SET cleanup_reviewed_at = ?, cleanup_reviewed_hash = ? WHERE id = ?",
            (utc_now_iso(), review_hash, pattern_id))
        if conn is None:
            target.commit()

    def reset_cleanup_force_state(self, conn=None) -> int:
        """Clear completion and pending state for the active learned backlog."""
        if conn is None:
            with self.transaction(immediate=True) as own:
                return self.reset_cleanup_force_state(conn=own)
        cursor = conn.execute(
            "UPDATE ad_patterns SET cleanup_reviewed_at = NULL, cleanup_reviewed_hash = NULL, "
            "cleanup_stats_reviewed = '{}' "
            "WHERE created_by = 'auto' AND source = 'local' AND is_active = 1")
        conn.execute(
            "UPDATE pattern_cleanup_suggestions SET superseded_at = ? "
            "WHERE status = 'pending' AND superseded_at IS NULL AND pattern_id IN ("
            "SELECT id FROM ad_patterns WHERE created_by = 'auto' AND source = 'local' "
            "AND is_active = 1)", (utc_now_iso(),))
        return cursor.rowcount

    def get_cleanup_stats_rows(self, conn=None) -> list[dict]:
        """Return active learned patterns for statistics checks, regardless of review state."""
        rows = (conn or self.get_connection()).execute(
            f"""SELECT ap.*, ks.name AS sponsor, pc.title AS podcast_title
                FROM ad_patterns ap
                LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id
                LEFT JOIN podcasts pc ON pc.slug = ap.podcast_id
                WHERE {LEARNED_PATTERN_WHERE}
                ORDER BY ap.id"""  # noqa: S608
        ).fetchall()
        return [dict(row) for row in rows]

    def get_cleanup_stats_reviewed(self, pattern_id: int, conn=None) -> dict | None:
        row = (conn or self.get_connection()).execute(
            "SELECT cleanup_stats_reviewed FROM ad_patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if not row or row['cleanup_stats_reviewed'] is None:
            return None
        try:
            value = json.loads(row['cleanup_stats_reviewed'])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def set_cleanup_stats_reviewed(self, pattern_id: int, evidence_map: dict, conn=None) -> None:
        target = conn or self.get_connection()
        target.execute(
            "UPDATE ad_patterns SET cleanup_stats_reviewed = ? WHERE id = ?",
            (json.dumps(evidence_map, sort_keys=True), pattern_id))
        if conn is None:
            target.commit()

    def get_cleanup_stat_decisions(self, pattern_id: int, conn=None) -> list[dict]:
        rows = (conn or self.get_connection()).execute(
            "SELECT * FROM pattern_cleanup_suggestions WHERE pattern_id = ? "
            "AND kind IN ('retire', 'flag') AND status IN ('approved', 'rejected', 'undone') "
            "ORDER BY id", (pattern_id,)
        ).fetchall()
        return [_decode_suggestion(row) for row in rows]

    def get_cleanup_candidate_rows(self, *, force: bool = False, batch_size: int = 25) -> list[dict]:
        """Active learned patterns needing model review, oldest review first."""
        conn = self.get_connection()
        conn.create_function('cleanup_review_hash', 2, review_hash)
        conn.create_function('cleanup_review_hash', 3, review_hash)
        pending_model = """EXISTS (
            SELECT 1 FROM pattern_cleanup_suggestions s
            WHERE s.pattern_id = ap.id AND s.status = 'pending' AND s.superseded_at IS NULL
              AND (
                    s.kind NOT IN ('retire', 'flag')
                    OR (s.kind = 'flag' AND (
                        COALESCE(json_extract(s.payload, '$.contaminated'), 0) = 1
                        OR json_extract(s.payload, '$.recommended') = 'trim'
                    ))
              )
              AND (
                    json_type(s.before, '$.text_template') IS NULL
                    OR json_type(s.before, '$.sponsor') IS NULL
                    OR CASE WHEN json_type(s.before, '$.category') IS NULL THEN
                        cleanup_review_hash(json_extract(s.before, '$.text_template'),
                                            json_extract(s.before, '$.sponsor'))
                        = cleanup_review_hash(ap.text_template, ks.name)
                    ELSE cleanup_review_hash(
                        json_extract(s.before, '$.text_template'),
                        json_extract(s.before, '$.sponsor'),
                        json_extract(s.before, '$.category'))
                        = cleanup_review_hash(ap.text_template, ks.name, ap.category)
                    END
              )
        )"""
        pending_filter = '' if force else ' AND NOT (' + pending_model + ')'
        cursor = conn.execute(
            f"""SELECT ap.*, ks.name AS sponsor, pc.title AS podcast_title,
                       {pending_model} AS has_pending_model
                FROM ad_patterns ap
                LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id
                LEFT JOIN podcasts pc ON pc.slug = ap.podcast_id
                WHERE {LEARNED_PATTERN_WHERE}
                  AND (ap.cleanup_reviewed_hash IS NULL
                       OR (ap.cleanup_reviewed_hash != ?
                           AND ap.cleanup_reviewed_hash != cleanup_review_hash(
                               ap.text_template, ks.name, ap.category)))
                  {pending_filter}
                ORDER BY ap.cleanup_reviewed_at IS NOT NULL, ap.cleanup_reviewed_at, ap.id
                LIMIT ?""",  # noqa: S608
            (INVALID_MARKER, batch_size + CANDIDATE_ROW_MARGIN))
        return [dict(row) for row in cursor.fetchall()]


    def record_cleanup_check(self, run_id, pattern, *, stats_checked=False,
                             model_status=None):
        if run_id is None:
            return
        conn = self.get_connection()
        conn.execute("""INSERT INTO pattern_cleanup_checks
            (run_id, pattern_id, pattern_scope, podcast_slug, stats_checked, model_status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, pattern_id) DO UPDATE SET
                stats_checked = MAX(stats_checked, excluded.stats_checked),
                model_status = COALESCE(excluded.model_status, model_status)""",
            (run_id, pattern['id'], pattern['scope'],
             pattern.get('podcast_id') if pattern['scope'] == 'podcast' else None,
             int(stats_checked), model_status))
        conn.commit()

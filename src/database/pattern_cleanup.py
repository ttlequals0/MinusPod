"""Pattern cleanup mixin: review runs, suggestions, and review stamps on learned patterns."""
import json

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
            "INSERT INTO pattern_cleanup_runs (forced, trigger, started_at) VALUES (?, ?, ?)",
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
            "DELETE FROM pattern_cleanup_suggestions "
            "WHERE pattern_id = ? AND kind = ? AND status = 'pending'",
            (pattern_id, kind))
        cursor = conn.execute(
            """INSERT INTO pattern_cleanup_suggestions
               (run_id, pattern_id, kind, confidence, reasons, payload, before, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, pattern_id, kind, confidence, json.dumps(reasons or []),
             json.dumps(payload or {}), json.dumps(before or {}), utc_now_iso()))
        return cursor.lastrowid

    def supersede_pending(self, pattern_id: int, conn=None) -> int:
        """Drop every pending suggestion of any kind for a pattern."""
        target = conn or self.get_connection()
        cursor = target.execute(
            "DELETE FROM pattern_cleanup_suggestions WHERE pattern_id = ? AND status = 'pending'",
            (pattern_id,))
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
            "SELECT * FROM pattern_cleanup_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return _decode_suggestion(row) if row else None

    def get_cleanup_suggestions(self, status: str | None = None, kind: str | None = None,
                                limit: int = 50, offset: int = 0) -> list[dict]:
        """Suggestions newest first, each with a `pattern` summary."""
        summary_cols = ', '.join(f'ap.{c} AS p_{c}' for c in _PATTERN_SUMMARY_FIELDS)
        query = f"""
            SELECT s.*, {summary_cols}, ks.name AS p_sponsor, pc.title AS p_podcast_title
            FROM pattern_cleanup_suggestions s
            JOIN ad_patterns ap ON ap.id = s.pattern_id
            LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id
            LEFT JOIN podcasts pc ON pc.slug = ap.podcast_id
            WHERE 1=1"""  # noqa: S608
        params: list = []
        if status:
            query += " AND s.status = ?"
            params.append(status)
        if kind:
            query += " AND s.kind = ?"
            params.append(kind)
        query += " ORDER BY s.created_at DESC, s.id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
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
            "WHERE status = 'pending' GROUP BY kind").fetchall()
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
                 "WHERE pattern_id = ? AND status = 'pending'")
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

    def clear_cleanup_reviewed(self) -> int:
        """Clear review stamps on every learned pattern (force recheck)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE ad_patterns SET cleanup_reviewed_at = NULL, cleanup_reviewed_hash = NULL "
            "WHERE created_by = 'auto' AND source = 'local'")
        conn.commit()
        return cursor.rowcount

    def get_cleanup_candidate_rows(self, *, force: bool = False, batch_size: int = 25) -> list[dict]:
        """Active learned patterns, never-reviewed first, then oldest review.
        Filters out pending (unless forced) and already-reviewed-unchanged patterns in SQL
        and caps the result to batch_size plus a margin; the caller still rechecks each row."""
        from pattern_cleanup import INVALID_MARKER, review_hash  # deferred: avoid an import cycle
        conn = self.get_connection()
        conn.create_function('cleanup_review_hash', 2, review_hash)
        pending_filter = '' if force else (
            " AND NOT EXISTS (SELECT 1 FROM pattern_cleanup_suggestions s "
            "WHERE s.pattern_id = ap.id AND s.status = 'pending')")
        cursor = conn.execute(
            f"""SELECT ap.*, ks.name AS sponsor, pc.title AS podcast_title,
                       EXISTS(SELECT 1 FROM pattern_cleanup_suggestions s
                              WHERE s.pattern_id = ap.id AND s.status = 'pending') AS has_pending
                FROM ad_patterns ap
                LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id
                LEFT JOIN podcasts pc ON pc.slug = ap.podcast_id
                WHERE {LEARNED_PATTERN_WHERE}
                  AND (ap.cleanup_reviewed_hash IS NULL
                       OR (ap.cleanup_reviewed_hash != ?
                           AND ap.cleanup_reviewed_hash != cleanup_review_hash(ap.text_template, ks.name)))
                  {pending_filter}
                ORDER BY ap.cleanup_reviewed_at IS NOT NULL, ap.cleanup_reviewed_at, ap.id
                LIMIT ?""",  # noqa: S608
            (INVALID_MARKER, batch_size + CANDIDATE_ROW_MARGIN))
        return [dict(row) for row in cursor.fetchall()]

"""Audio fingerprint match history mixin for MinusPod database."""


class FingerprintMatchMixin:
    """Per-pattern record of where a fingerprint matched, and which matches were rejected."""

    def record_fingerprint_match(self, pattern_id: int, episode_id: str, podcast_slug: str,
                                 match_start: float, match_end: float,
                                 similarity: float | None, coverage: float | None,
                                 in_scope: bool, audio_only: bool | None) -> int:
        """Insert one match row. Returns its ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO audio_fingerprint_matches
                 (pattern_id, episode_id, podcast_slug, match_start, match_end,
                  similarity, coverage, in_scope, audio_only)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, episode_id, podcast_slug, match_start, match_end,
             similarity, coverage, int(in_scope),
             None if audio_only is None else int(audio_only))
        )
        conn.commit()
        return cursor.lastrowid

    def delete_fingerprint_matches_for_episode(self, podcast_slug: str, episode_id: str,
                                               keep_rejected: bool = False) -> int:
        """Delete an episode's match rows, optionally sparing rejected ones."""
        sql = ("DELETE FROM audio_fingerprint_matches "
               "WHERE podcast_slug = ? AND episode_id = ?")
        if keep_rejected:
            sql += " AND rejected = 0"
        conn = self.get_connection()
        cursor = conn.execute(sql, (podcast_slug, episode_id))
        conn.commit()
        return cursor.rowcount

    def delete_fingerprint_matches_for_pattern(self, pattern_id: int) -> int:
        """Delete every match row of a pattern."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_fingerprint_matches WHERE pattern_id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount

    def move_fingerprint_matches(self, from_pattern_id: int, to_pattern_id: int) -> int:
        """Re-point match rows from one pattern to another (pattern merge)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE audio_fingerprint_matches SET pattern_id = ? WHERE pattern_id = ?",
            (to_pattern_id, from_pattern_id)
        )
        conn.commit()
        return cursor.rowcount

    def get_fingerprint_matches(self, pattern_id: int, limit: int, offset: int) -> list[dict]:
        """A page of a pattern's matches, newest first."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT * FROM audio_fingerprint_matches WHERE pattern_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (pattern_id, limit, offset)
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_fingerprint_matches(self, pattern_id: int) -> int:
        """Number of match rows for a pattern."""
        conn = self.get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM audio_fingerprint_matches WHERE pattern_id = ?",
            (pattern_id,)
        ).fetchone()[0]

    def get_fingerprint_match(self, match_id: int) -> dict | None:
        """One match row by ID."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM audio_fingerprint_matches WHERE id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def mark_fingerprint_match_rejected(self, pattern_id: int, podcast_slug: str,
                                        episode_id: str, start: float, end: float) -> bool:
        """Reject the match with the largest overlap with [start, end]. False if none overlaps."""
        conn = self.get_connection()
        row = conn.execute(
            """SELECT id FROM audio_fingerprint_matches
               WHERE pattern_id = ? AND podcast_slug = ? AND episode_id = ?
                 AND MIN(match_end, ?) - MAX(match_start, ?) > 0
               ORDER BY MIN(match_end, ?) - MAX(match_start, ?) DESC, id DESC
               LIMIT 1""",
            (pattern_id, podcast_slug, episode_id, end, start, end, start)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """UPDATE audio_fingerprint_matches SET rejected = 1,
                 rejected_at = COALESCE(rejected_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               WHERE id = ?""",
            (row['id'],)
        )
        conn.commit()
        return True

    def get_audio_match_summary(self, pattern_id: int) -> dict:
        """Counts, mean similarity and latest match time over a pattern's match rows."""
        conn = self.get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS match_count,
                      COALESCE(SUM(rejected), 0) AS rejected_count,
                      COALESCE(SUM(1 - in_scope), 0) AS out_of_scope_count,
                      AVG(similarity) AS mean_similarity,
                      MAX(created_at) AS last_matched_at
               FROM audio_fingerprint_matches WHERE pattern_id = ?""",
            (pattern_id,)
        ).fetchone()
        return dict(row)

"""Audio fingerprint mixin for MinusPod database."""
import logging

from database.fingerprint_cleanup import delete_matches_for_patterns
from database.patterns import _row_with_category
from utils.json_fields import load_json_list

_FINGERPRINT_META_COLUMNS = (
    "id, pattern_id, duration, created_at, enabled, "
    "source_podcast_slug, source_episode_id, source_start, source_end")

logger = logging.getLogger(__name__)


class FingerprintMixin:
    """Audio fingerprint management methods."""

    def get_audio_fingerprint(self, pattern_id: int, *, with_blob: bool = True) -> dict | None:
        """Get audio fingerprint for a pattern; with_blob=False skips the fingerprint column."""
        conn = self.get_connection()
        columns = '*' if with_blob else _FINGERPRINT_META_COLUMNS
        cursor = conn.execute(
            f"SELECT {columns} FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)  # noqa: S608
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_audio_fingerprint(self, pattern_id: int, fingerprint: bytes,
                                  duration: float,
                                  source_podcast_slug: str | None = None,
                                  source_episode_id: str | None = None,
                                  source_start: float | None = None,
                                  source_end: float | None = None,
                                  enabled: bool | None = None) -> int:
        """Upsert a fingerprint. enabled=None keeps the stored flag (1 on insert)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO audio_fingerprints
                 (pattern_id, fingerprint, duration, enabled, source_podcast_slug,
                  source_episode_id, source_start, source_end)
               VALUES (?, ?, ?, COALESCE(?, 1), ?, ?, ?, ?)
               ON CONFLICT(pattern_id) DO UPDATE SET
                 fingerprint = excluded.fingerprint,
                 duration = excluded.duration,
                 enabled = COALESCE(?, audio_fingerprints.enabled),
                 source_podcast_slug = excluded.source_podcast_slug,
                 source_episode_id = excluded.source_episode_id,
                 source_start = excluded.source_start,
                 source_end = excluded.source_end""",
            (pattern_id, fingerprint, duration,
             None if enabled is None else int(enabled),
             source_podcast_slug, source_episode_id, source_start, source_end,
             None if enabled is None else int(enabled))
        )
        conn.commit()
        return cursor.lastrowid

    def set_fingerprint_enabled(self, pattern_id: int, enabled: bool) -> bool:
        """Turn audio matching on or off for a pattern. False if it has no fingerprint."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE audio_fingerprints SET enabled = ? WHERE pattern_id = ?",
            (int(enabled), pattern_id)
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_active_fingerprints_with_scope(self) -> list[dict]:
        """Enabled fingerprints of active patterns with scope, sponsor and tags of the sponsor when it is active."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT af.pattern_id, af.fingerprint, af.duration, ks.name AS sponsor,
                      COALESCE(ks.segment_category, ap.category) AS category,
                      ap.scope, ap.network_id, ap.podcast_id, ap.source,
                      ap.sponsor_id, CASE WHEN ks.is_active = 1 THEN ks.tags END AS sponsor_tags
               FROM audio_fingerprints af
               JOIN ad_patterns ap ON af.pattern_id = ap.id
               LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
               WHERE ap.is_active = 1 AND af.enabled = 1"""
        )
        rows = []
        for row in cursor.fetchall():
            item = dict(row)
            item['sponsor_tags'] = load_json_list(item['sponsor_tags'])
            rows.append(_row_with_category(item))
        return rows

    def set_pattern_audio_removed(self, pattern_id: int, removed: bool) -> None:
        """Mark that the user deleted this pattern's audio, so it is not rebuilt."""
        conn = self.get_connection()
        conn.execute(
            "UPDATE ad_patterns SET audio_removed = ? WHERE id = ?",
            (int(removed), pattern_id)
        )
        conn.commit()

    def delete_audio_fingerprint(self, pattern_id: int) -> bool:
        """Delete an audio fingerprint."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_pattern_audio(self, pattern_id: int) -> bool:
        """Atomically delete a pattern's fingerprint and match history and mark its audio removed.

        Returns False, changing nothing, when the pattern has no fingerprint."""
        with self.transaction(immediate=True) as conn:
            deleted = conn.execute(
                "DELETE FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
            ).rowcount > 0
            if not deleted:
                return False
            delete_matches_for_patterns(conn, [pattern_id])
            conn.execute("UPDATE ad_patterns SET audio_removed = 1 WHERE id = ?", (pattern_id,))
        return True

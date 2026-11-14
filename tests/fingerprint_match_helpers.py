"""Insert and read audio_fingerprint_matches rows as fixtures, through the product's own insert."""
from database.fingerprint_matches import FingerprintMatchMixin


def record_fingerprint_match(db, pattern_id, episode_id, podcast_slug, match_start, match_end,
                             similarity, coverage, in_scope, audio_only) -> int:
    """Insert one match row and commit. Returns its ID."""
    conn = db.get_connection()
    row_id = FingerprintMatchMixin._insert_fingerprint_match(
        conn, pattern_id, episode_id, podcast_slug, match_start, match_end,
        similarity, coverage, in_scope, audio_only)
    conn.commit()
    return row_id


def get_fingerprint_matches(db, pattern_id, limit, offset) -> list[dict]:
    """A page of a pattern's match rows, newest first."""
    rows = db.get_connection().execute(
        """SELECT * FROM audio_fingerprint_matches WHERE pattern_id = ?
           ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
        (pattern_id, limit, offset)).fetchall()
    return [dict(row) for row in rows]

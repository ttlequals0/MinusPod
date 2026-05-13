"""Audio fingerprint mixin for MinusPod database."""
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class FingerprintMixin:
    """Audio fingerprint management methods."""

    def get_audio_fingerprint(self, pattern_id: int) -> Optional[Dict]:
        """Get audio fingerprint for a pattern."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_audio_fingerprints(self) -> List[Dict]:
        """Get all audio fingerprints."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM audio_fingerprints")
        return [dict(row) for row in cursor.fetchall()]

    def get_all_fingerprints_with_sponsors(self) -> List[Dict]:
        """Get all audio fingerprints with sponsor names from ad_patterns (single JOIN)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT af.pattern_id, af.fingerprint, af.duration, ks.name AS sponsor
               FROM audio_fingerprints af
               LEFT JOIN ad_patterns ap ON af.pattern_id = ap.id
               LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def create_audio_fingerprint(self, pattern_id: int, fingerprint: bytes,
                                  duration: float) -> int:
        """Create an audio fingerprint. Returns fingerprint ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration)
               VALUES (?, ?, ?)
               ON CONFLICT(pattern_id) DO UPDATE SET
                 fingerprint = excluded.fingerprint,
                 duration = excluded.duration""",
            (pattern_id, fingerprint, duration)
        )
        conn.commit()
        return cursor.lastrowid

    def delete_audio_fingerprint(self, pattern_id: int) -> bool:
        """Delete an audio fingerprint."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

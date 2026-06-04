"""Sponsor management mixin for MinusPod database."""
import json
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class SponsorMixin:
    """Known sponsors and normalization management methods."""

    def get_known_sponsors(self, active_only: bool = True) -> List[Dict]:
        """Get all known sponsors."""
        conn = self.get_connection()
        query = "SELECT * FROM known_sponsors"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY name"
        cursor = conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def get_known_sponsor_by_id(self, sponsor_id: int) -> Optional[Dict]:
        """Get a single sponsor by ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM known_sponsors WHERE id = ?", (sponsor_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_known_sponsor_by_name(self, name: str) -> Optional[Dict]:
        """Get a sponsor by name."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM known_sponsors WHERE LOWER(name) = LOWER(?)", (name,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_known_sponsor(self, name: str, aliases: List[str] = None,
                              category: str = None, common_ctas: List[str] = None,
                              tags: List[str] = None) -> int:
        """Create a known sponsor. Returns sponsor ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO known_sponsors (name, aliases, category, common_ctas, tags)
               VALUES (?, ?, ?, ?, ?)""",
            (name, json.dumps(aliases or []), category,
             json.dumps(common_ctas or []), json.dumps(tags or []))
        )
        conn.commit()
        return cursor.lastrowid

    def update_known_sponsor(self, sponsor_id: int, **kwargs) -> bool:
        """Update a known sponsor."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('name', 'category', 'is_active'):
                fields.append(f"{key} = ?")
                values.append(value)
            elif key in ('aliases', 'common_ctas', 'tags'):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if isinstance(value, list) else value)

        if not fields:
            return False

        values.append(sponsor_id)
        conn.execute(
            f"UPDATE known_sponsors SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def get_sponsors_by_tag(self, tag: str, active_only: bool = True) -> List[Dict]:
        """Return sponsors whose tags JSON array contains the given tag.

        SQLite json_each is used to avoid loading every row into Python.
        """
        conn = self.get_connection()
        query = (
            "SELECT s.* FROM known_sponsors s, json_each(s.tags) j "
            "WHERE j.value = ?"
        )
        params: List = [tag]
        if active_only:
            query += " AND s.is_active = 1"
        query += " ORDER BY s.name"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def hard_delete_known_sponsor(self, sponsor_id: int) -> tuple:
        """Permanently remove a known sponsor. Linked ad_patterns are unlinked
        (sponsor_id set NULL), not deleted, so no pattern data is lost.

        Returns (deleted, unlinked_patterns).
        """
        conn = self.get_connection()
        unlinked = conn.execute(
            "UPDATE ad_patterns SET sponsor_id = NULL WHERE sponsor_id = ?",
            (sponsor_id,)
        ).rowcount
        cursor = conn.execute(
            "DELETE FROM known_sponsors WHERE id = ?", (sponsor_id,)
        )
        conn.commit()
        return cursor.rowcount > 0, unlinked

    # The two stats helpers below query ad_patterns but live here because they
    # exist to enrich sponsor payloads (list/detail), not for pattern analysis.

    def get_sponsor_pattern_stats(self) -> Dict[int, Dict]:
        """Map sponsor_id -> {pattern_count, last_matched_at} aggregated over
        active ad_patterns. One query, used to enrich the sponsor list."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT sponsor_id,
                      COUNT(*) AS pattern_count,
                      MAX(last_matched_at) AS last_matched_at
               FROM ad_patterns
               WHERE is_active = 1 AND sponsor_id IS NOT NULL
               GROUP BY sponsor_id"""
        )
        return {
            row['sponsor_id']: {
                'pattern_count': row['pattern_count'],
                'last_matched_at': row['last_matched_at'],
            }
            for row in cursor
        }

    def get_sponsor_pattern_stats_by_id(self, sponsor_id: int) -> Dict:
        """Pattern stats for a single sponsor via the indexed sponsor_id lookup,
        avoiding a full aggregate scan for the detail endpoint."""
        conn = self.get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS pattern_count,
                      MAX(last_matched_at) AS last_matched_at
               FROM ad_patterns
               WHERE is_active = 1 AND sponsor_id = ?""",
            (sponsor_id,)
        ).fetchone()
        return {
            'pattern_count': row['pattern_count'] if row else 0,
            'last_matched_at': row['last_matched_at'] if row else None,
        }

    # ========== Sponsor Normalizations Methods ==========

    def get_sponsor_normalizations(self, category: str = None,
                                    active_only: bool = True) -> List[Dict]:
        """Get sponsor normalizations."""
        conn = self.get_connection()

        query = "SELECT * FROM sponsor_normalizations WHERE 1=1"
        params = []

        if active_only:
            query += " AND is_active = 1"
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY category, pattern"

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def create_sponsor_normalization(self, pattern: str, replacement: str,
                                      category: str) -> int:
        """Create a sponsor normalization. Returns normalization ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO sponsor_normalizations (pattern, replacement, category)
               VALUES (?, ?, ?)""",
            (pattern, replacement, category)
        )
        conn.commit()
        return cursor.lastrowid

    def update_sponsor_normalization(self, norm_id: int, **kwargs) -> bool:
        """Update a sponsor normalization."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('pattern', 'replacement', 'category', 'is_active'):
                fields.append(f"{key} = ?")
                values.append(value)

        if not fields:
            return False

        values.append(norm_id)
        conn.execute(
            f"UPDATE sponsor_normalizations SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def delete_sponsor_normalization(self, norm_id: int) -> bool:
        """Delete a sponsor normalization (or set inactive)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE sponsor_normalizations SET is_active = 0 WHERE id = ?", (norm_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

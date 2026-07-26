"""Podping host coverage mixin: which feed-URL domains send podpings."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from utils.time import ISO_FORMAT, utc_now_iso

logger = logging.getLogger(__name__)


class PodpingHostMixin:
    """Domains observed sending podpings on the Hive chain."""

    def record_podping_hosts(self, counts: Dict[str, int]) -> None:
        """Upsert a batch of {domain: ping count} in one transaction."""
        if not counts:
            return
        now = utc_now_iso()
        conn = self.get_connection()
        try:
            conn.executemany("""
                INSERT INTO podping_hosts (domain, first_seen_at, last_seen_at, ping_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    ping_count = ping_count + excluded.ping_count
            """, [(domain, now, now, count) for domain, count in counts.items()])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def get_active_podping_domains(self, days: int = 30) -> set:
        """Domains seen within the window; empty set when the table is empty."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(ISO_FORMAT)
        cursor = self.get_connection().execute(
            "SELECT domain FROM podping_hosts WHERE last_seen_at >= ?", (cutoff,))
        return {row['domain'] for row in cursor.fetchall()}

    def get_podping_hosts(self, limit: int = 100) -> List[Dict]:
        """Most recently active domains first."""
        cursor = self.get_connection().execute(
            "SELECT domain, first_seen_at, last_seen_at, ping_count "
            "FROM podping_hosts ORDER BY last_seen_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def count_podping_hosts(self) -> int:
        """Every domain ever recorded, active or not."""
        cursor = self.get_connection().execute(
            "SELECT COUNT(*) AS n FROM podping_hosts")
        return cursor.fetchone()['n']

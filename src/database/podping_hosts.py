"""Podping host coverage mixin: which feed-URL domains send podpings."""
import logging
from datetime import datetime, timedelta, timezone

from config import (PODPING_HOST_ACTIVE_DAYS, PODPING_HOSTS_FLUSH_MAX_DOMAINS,
                    PODPING_HOSTS_MAX_ROWS)
from utils.time import ISO_FORMAT, utc_now_iso

logger = logging.getLogger(__name__)


class PodpingHostMixin:
    """Domains observed sending podpings on the Hive chain."""

    @staticmethod
    def podping_active_cutoff(days: int = PODPING_HOST_ACTIVE_DAYS) -> str:
        """Oldest last_seen_at that still counts as active."""
        return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(ISO_FORMAT)

    def record_podping_hosts(self, counts: dict[str, int]) -> None:
        """Upsert a batch of {domain: ping count} in one transaction, bounded
        against a sender flooding the table with fabricated domains."""
        if not counts:
            return
        if len(counts) > PODPING_HOSTS_FLUSH_MAX_DOMAINS:
            logger.warning(
                "Podping flush trimmed: %d domains, keeping the busiest %d",
                len(counts), PODPING_HOSTS_FLUSH_MAX_DOMAINS)
            counts = dict(sorted(counts.items(), key=lambda kv: kv[1],
                                 reverse=True)[:PODPING_HOSTS_FLUSH_MAX_DOMAINS])
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
            conn.execute("""
                DELETE FROM podping_hosts WHERE domain IN (
                    SELECT domain FROM podping_hosts
                    ORDER BY last_seen_at DESC LIMIT -1 OFFSET ?)
            """, (PODPING_HOSTS_MAX_ROWS,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def is_podping_domain_active(self, domain: str,
                                 days: int = PODPING_HOST_ACTIVE_DAYS) -> bool:
        """Whether one domain was seen inside the window. The single-feed path
        needs this, not the whole active set."""
        if not domain:
            return False
        cutoff = self.podping_active_cutoff(days)
        row = self.get_connection().execute(
            "SELECT 1 FROM podping_hosts WHERE domain = ? AND last_seen_at >= ?",
            (domain, cutoff)).fetchone()
        return row is not None

    def get_active_podping_domains(self, days: int = 30) -> set:
        """Domains seen within the window; empty set when the table is empty."""
        cutoff = self.podping_active_cutoff(days)
        cursor = self.get_connection().execute(
            "SELECT domain FROM podping_hosts WHERE last_seen_at >= ?", (cutoff,))
        return {row['domain'] for row in cursor.fetchall()}

    def count_active_podping_domains(self,
                                     days: int = PODPING_HOST_ACTIVE_DAYS) -> int:
        """How many domains are inside the window, without loading them all."""
        cutoff = self.podping_active_cutoff(days)
        return self.get_connection().execute(
            "SELECT COUNT(*) AS n FROM podping_hosts WHERE last_seen_at >= ?",
            (cutoff,)).fetchone()['n']

    def get_podping_hosts(self, limit: int = 100) -> list[dict]:
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

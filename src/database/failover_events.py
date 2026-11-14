"""Failover events mixin: audit log of provider failover triggers/cancels (#806)."""
from database.error_text import redact_error


class FailoverEventsMixin:
    @staticmethod
    def insert_failover_event(conn, target: str, action: str, source: str, reason: str | None) -> None:
        """Append an audit row on the caller's connection so it joins the caller's transaction."""
        conn.execute(
            "INSERT INTO failover_events (target, action, source, reason) VALUES (?, ?, ?, ?)",
            (target, action, source, redact_error(reason)))

    def get_failover_events(self, limit: int = 50) -> list[dict]:
        """Most recent events first."""
        cursor = self.get_connection().execute(
            "SELECT id, target, action, source, reason, created_at FROM failover_events "
            "ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in cursor.fetchall()]

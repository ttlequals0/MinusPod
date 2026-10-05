"""Failover events mixin: audit log of provider failover triggers/cancels (#806)."""


class FailoverEventsMixin:
    def record_failover_event(self, target: str, action: str, source: str, reason: str | None) -> None:
        """Append one row; reason is truncated to keep the log bounded."""
        conn = self.get_connection()
        conn.execute(
            "INSERT INTO failover_events (target, action, source, reason) VALUES (?, ?, ?, ?)",
            (target, action, source, (reason or '')[:500]))
        conn.commit()

    def get_failover_events(self, limit: int = 50) -> list[dict]:
        """Most recent events first."""
        cursor = self.get_connection().execute(
            "SELECT id, target, action, source, reason, created_at FROM failover_events "
            "ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in cursor.fetchall()]

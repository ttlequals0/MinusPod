"""Failover events mixin: audit log of provider failover triggers/cancels (#806)."""


class FailoverEventsMixin:
    def get_failover_events(self, limit: int = 50) -> list[dict]:
        """Most recent events first."""
        cursor = self.get_connection().execute(
            "SELECT id, target, action, source, reason, created_at FROM failover_events "
            "ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in cursor.fetchall()]

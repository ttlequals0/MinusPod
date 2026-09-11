"""Durable provider spend admission reservations."""

import uuid

from utils.time import utc_now_iso


class ProviderAdmissionMixin:
    def provider_budget_status(self, provider: str) -> dict:
        now = utc_now_iso()
        with self.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE provider_spend_reservations
                   SET status = 'uncertain', updated_at = ?
                   WHERE status = 'reserved' AND expires_at <= ?
                     AND (run_id IS NULL OR NOT EXISTS (
                       SELECT 1 FROM processing_runs pr
                       WHERE pr.run_id = provider_spend_reservations.run_id
                         AND pr.state IN ('running', 'cancel_requested')
                     ))""",
                (now, now),
            )
            row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN status = 'reconciled'
                   THEN actual_microusd ELSE 0 END), 0) AS spent,
                 COALESCE(SUM(CASE WHEN status IN ('reserved', 'uncertain')
                   THEN reserved_microusd ELSE 0 END), 0) AS reserved,
                 COALESCE(SUM(CASE WHEN status = 'reserved' THEN 1 ELSE 0 END), 0) AS active
               FROM provider_spend_reservations
               WHERE provider = ? AND
                 (status IN ('reserved', 'uncertain') OR
                  substr(created_at, 1, 10) = substr(?, 1, 10))""",
                (provider, now),
            ).fetchone()
        return {
            'provider': provider,
            'spentMicrousd': row['spent'],
            'reservedMicrousd': row['reserved'],
            'activeReservations': row['active'],
        }

    def reserve_provider_spend(self, provider: str,
                               estimated_microusd: int | None,
                               run_id: str | None = None) -> dict:
        if not self.get_setting_bool('provider_budget_enabled', False):
            return {'allowed': True, 'reservation_id': None, 'reason': None}
        unknown_action = self.get_setting('provider_budget_unknown_cost') or 'deny'
        if estimated_microusd is None:
            if unknown_action == 'allow':
                amount = 0
            elif unknown_action == 'reserve':
                amount = self.get_setting_int(
                    'provider_budget_unknown_reserve_microusd', 0
                )
                if amount <= 0:
                    return {'allowed': False, 'reservation_id': None,
                            'reason': 'unknown_cost'}
            else:
                return {'allowed': False, 'reservation_id': None,
                        'reason': 'unknown_cost'}
        else:
            amount = max(0, int(estimated_microusd))
        limit = self.get_setting_int('provider_budget_daily_limit_microusd', 0)
        concurrency = max(
            1, self.get_setting_int('provider_budget_max_reservations', 1)
        )
        now = utc_now_iso()
        reservation_id = uuid.uuid4().hex
        with self.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE provider_spend_reservations
                   SET status = 'uncertain', updated_at = ?
                   WHERE status = 'reserved' AND expires_at <= ?
                     AND (run_id IS NULL OR NOT EXISTS (
                       SELECT 1 FROM processing_runs pr
                       WHERE pr.run_id = provider_spend_reservations.run_id
                         AND pr.state IN ('running', 'cancel_requested')
                     ))""",
                (now, now),
            )
            active = conn.execute(
                """SELECT COUNT(*) AS count FROM provider_spend_reservations
                   WHERE provider = ? AND status = 'reserved'""",
                (provider,),
            ).fetchone()['count']
            if active >= concurrency:
                return {'allowed': False, 'reservation_id': None,
                        'reason': 'concurrency'}
            spent = conn.execute(
                """SELECT COALESCE(SUM(CASE
                     WHEN status = 'reconciled' THEN actual_microusd
                     WHEN status IN ('reserved', 'uncertain')
                       THEN reserved_microusd ELSE 0 END), 0)
                   AS amount FROM provider_spend_reservations
                   WHERE provider = ? AND
                     (status IN ('reserved', 'uncertain') OR
                      substr(created_at, 1, 10) = substr(?, 1, 10))""",
                (provider, now),
            ).fetchone()['amount']
            if limit > 0 and spent + amount > limit:
                return {'allowed': False, 'reservation_id': None,
                        'reason': 'daily_budget'}
            conn.execute(
                """INSERT INTO provider_spend_reservations
                   (id, provider, reserved_microusd, status, run_id, created_at,
                    updated_at, expires_at)
                   VALUES (?, ?, ?, 'reserved', ?, ?, ?,
                           strftime('%Y-%m-%dT%H:%M:%SZ', ?, '+2 hours'))""",
                (reservation_id, provider, amount, run_id, now, now, now),
            )
        return {'allowed': True, 'reservation_id': reservation_id, 'reason': None}

    def reconcile_provider_spend(self, reservation_id: str,
                                 actual_microusd: int | None) -> bool:
        status = 'uncertain' if actual_microusd is None else 'reconciled'
        amount = None if actual_microusd is None else max(0, int(actual_microusd))
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE provider_spend_reservations
                   SET status = ?, actual_microusd = ?, updated_at = ?
                   WHERE id = ? AND status IN ('reserved', 'uncertain')""",
                (status, amount, utc_now_iso(), reservation_id),
            )
            return cursor.rowcount == 1

    def release_provider_spend(self, reservation_id: str) -> bool:
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE provider_spend_reservations
                   SET status = 'released', actual_microusd = 0, updated_at = ?
                   WHERE id = ? AND status = 'reserved'""",
                (utc_now_iso(), reservation_id),
            )
            return cursor.rowcount == 1

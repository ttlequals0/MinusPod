from concurrent.futures import ThreadPoolExecutor


def _enable_budget(db, *, limit=1_000_000, concurrency=1,
                   unknown='reserve', reserve=100_000):
    db.set_setting('provider_budget_enabled', 'true')
    db.set_setting('provider_budget_daily_limit_microusd', str(limit))
    db.set_setting('provider_budget_max_reservations', str(concurrency))
    db.set_setting('provider_budget_unknown_cost', unknown)
    db.set_setting('provider_budget_unknown_reserve_microusd', str(reserve))


def test_feed_subscriber_keys_are_scoped_and_revocable(temp_db):
    temp_db.create_podcast('feed-a', 'https://example.com/a.xml', 'A')
    temp_db.create_podcast('feed-b', 'https://example.com/b.xml', 'B')
    created = temp_db.create_feed_subscriber_key('feed-a', 'Phone')

    assert temp_db.verify_feed_subscriber_key('feed-a', created['token'])
    assert not temp_db.verify_feed_subscriber_key('feed-b', created['token'])
    assert 'token' not in temp_db.list_feed_subscriber_keys('feed-a')[0]
    assert temp_db.revoke_feed_subscriber_key('feed-a', created['id'])
    assert not temp_db.verify_feed_subscriber_key('feed-a', created['token'])


def test_feed_subscriber_rejects_malformed_secret(temp_db):
    temp_db.create_podcast('feed-a', 'https://example.com/a.xml', 'A')
    assert not temp_db.verify_feed_subscriber_key('feed-a', '0' * 16 + '.' + 'z' * 64)
    assert not temp_db.verify_feed_subscriber_key('feed-a', None)


def test_provider_admission_serializes_concurrent_reservations(temp_db):
    _enable_budget(temp_db, concurrency=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: temp_db.reserve_provider_spend('anthropic', 100_000),
            range(2),
        ))

    assert sum(result['allowed'] for result in results) == 1
    assert {result['reason'] for result in results} == {None, 'concurrency'}


def test_unknown_outcome_remains_charged_and_accepts_late_reconciliation(temp_db):
    _enable_budget(temp_db, limit=150_000, reserve=100_000)
    result = temp_db.reserve_provider_spend('anthropic', None)
    assert result['allowed']
    assert temp_db.reconcile_provider_spend(result['reservation_id'], None)
    assert temp_db.provider_budget_status('anthropic')['reservedMicrousd'] == 100_000
    assert not temp_db.reserve_provider_spend('anthropic', 100_000)['allowed']
    assert temp_db.reconcile_provider_spend(result['reservation_id'], 50_000)
    status = temp_db.provider_budget_status('anthropic')
    assert status['spentMicrousd'] == 50_000
    assert status['reservedMicrousd'] == 0


def test_release_requires_a_definitive_no_charge_outcome(temp_db):
    _enable_budget(temp_db)
    result = temp_db.reserve_provider_spend('anthropic', 100_000)
    assert temp_db.release_provider_spend(result['reservation_id'])
    assert not temp_db.reconcile_provider_spend(result['reservation_id'], 20_000)
    assert temp_db.provider_budget_status('anthropic')['reservedMicrousd'] == 0


def test_expired_lease_keeps_liability_without_blocking_concurrency(temp_db):
    _enable_budget(temp_db, concurrency=1)
    first = temp_db.reserve_provider_spend('anthropic', 100_000)
    with temp_db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE provider_spend_reservations SET expires_at = ? WHERE id = ?",
            ('2000-01-01T00:00:00Z', first['reservation_id']),
        )

    status = temp_db.provider_budget_status('anthropic')
    assert status['activeReservations'] == 0
    assert status['reservedMicrousd'] == 100_000
    assert temp_db.reserve_provider_spend('anthropic', 100_000)['allowed']


def test_expired_lease_keeps_concurrency_while_run_owner_is_live(temp_db):
    _enable_budget(temp_db, concurrency=1)
    podcast_id = temp_db.create_podcast(
        'active-feed', 'https://example.com/active.xml', 'Active')
    run_id = 'a' * 32
    with temp_db.transaction(immediate=True) as conn:
        conn.execute(
            """INSERT INTO processing_runs
               (run_id, podcast_id, episode_id, owner_pid, state)
               VALUES (?, ?, 'episode', 123, 'running')""",
            (run_id, podcast_id),
        )
    first = temp_db.reserve_provider_spend(
        'anthropic', 100_000, run_id=run_id)
    with temp_db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE provider_spend_reservations SET expires_at = ? WHERE id = ?",
            ('2000-01-01T00:00:00Z', first['reservation_id']),
        )

    assert temp_db.provider_budget_status('anthropic')['activeReservations'] == 1
    denied = temp_db.reserve_provider_spend('anthropic', 100_000)
    assert denied == {
        'allowed': False, 'reservation_id': None, 'reason': 'concurrency'}

    with temp_db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE processing_runs SET state = 'finished' WHERE run_id = ?",
            (run_id,),
        )
    assert temp_db.provider_budget_status('anthropic')['activeReservations'] == 0


def test_existing_provider_reservations_gain_run_ownership(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DROP TABLE provider_spend_reservations")
    conn.execute(
        """CREATE TABLE provider_spend_reservations (
           id TEXT PRIMARY KEY,
           provider TEXT NOT NULL,
           reserved_microusd INTEGER NOT NULL,
           actual_microusd INTEGER,
           status TEXT NOT NULL,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           expires_at TEXT NOT NULL)"""
    )
    conn.execute(
        """INSERT INTO provider_spend_reservations
           (id, provider, reserved_microusd, status, created_at, updated_at,
            expires_at) VALUES (?, 'anthropic', 100000, 'reserved', ?, ?, ?)""",
        ('b' * 32, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
         '2026-01-01T02:00:00Z'),
    )
    conn.commit()

    temp_db._run_schema_migrations()

    columns = {
        row['name'] for row in conn.execute(
            "PRAGMA table_info(provider_spend_reservations)").fetchall()
    }
    indexes = {
        row['name'] for row in conn.execute(
            "PRAGMA index_list(provider_spend_reservations)").fetchall()
    }
    row = conn.execute(
        "SELECT id, run_id FROM provider_spend_reservations").fetchone()
    assert columns >= {'run_id'}
    assert 'idx_provider_spend_run' in indexes
    assert dict(row) == {'id': 'b' * 32, 'run_id': None}

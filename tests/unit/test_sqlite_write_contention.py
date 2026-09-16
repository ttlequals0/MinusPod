"""Write-lock pressure fixes: SQLite allows one writer, so every extra
statement and every no-op commit lands in the queue that made refresh
sweeps and LLM calls stall each other for seconds.
"""
from concurrent.futures import ThreadPoolExecutor


def _begin(temp_db, model='claude-opus-5'):
    return temp_db.begin_llm_attempt(
        run_id='run-contention', podcast_id=None, episode_id=None,
        phase_key='detection', invoking_pass=1, provider_key='anthropic',
        configured_model=model)


def test_token_usage_bumps_three_stats_in_one_statement(temp_db, monkeypatch):
    attempt_id = _begin(temp_db)
    conn = temp_db.get_connection()
    executed = []
    real_execute = conn.execute
    monkeypatch.setattr(conn, 'execute',
                        lambda sql, *a: (executed.append(str(sql)), real_execute(sql, *a))[1])

    temp_db.finalize_llm_attempt(attempt_id, state='success',
                                 input_tokens=1000, output_tokens=500)

    stats_writes = [q for q in executed if 'INSERT INTO stats' in q]
    assert len(stats_writes) == 1


def test_concurrent_finalizes_lose_no_counter_updates(temp_db):
    """Two workers finalizing at once each take the write lock in turn; the
    counters must end up at the sum, not at one writer's value."""
    attempts = [_begin(temp_db), _begin(temp_db)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(temp_db.finalize_llm_attempt, attempts[0],
                            state='success', input_tokens=1000, output_tokens=500),
            executor.submit(temp_db.finalize_llm_attempt, attempts[1],
                            state='success', input_tokens=200, output_tokens=100),
        ]
        for future in futures:
            future.result()

    assert temp_db.get_stat('total_input_tokens') == 1200
    assert temp_db.get_stat('total_output_tokens') == 600
    summary = temp_db.get_token_usage_summary()
    assert summary['totalInputTokens'] == 1200
    assert summary['totalOutputTokens'] == 600
    assert [m['callCount'] for m in summary['models']] == [2]


def test_clean_feed_refresh_takes_no_write_lock(temp_db, monkeypatch):
    """A guarded UPDATE still takes the write lock to evaluate its WHERE, so
    the read has to decide first or a clean feed still queues behind writers."""
    temp_db.create_podcast('show-a', 'https://example.com/a.xml', 'Show A')
    conn = temp_db.get_connection()
    statements = []
    real_execute = conn.execute
    monkeypatch.setattr(conn, 'execute',
                        lambda sql, *a: (statements.append(str(sql)), real_execute(sql, *a))[1])
    commits = []
    monkeypatch.setattr(conn, 'commit', lambda: commits.append(1))

    temp_db.clear_refresh_failure_state('show-a')

    assert not any('UPDATE' in q for q in statements)
    assert commits == []


def test_a_missing_feed_is_a_no_op(temp_db, monkeypatch):
    conn = temp_db.get_connection()
    commits = []
    monkeypatch.setattr(conn, 'commit', lambda: commits.append(1))

    temp_db.clear_refresh_failure_state('no-such-feed')

    assert commits == []


def test_failed_feed_refresh_still_clears_and_commits(temp_db):
    temp_db.create_podcast('show-b', 'https://example.com/b.xml', 'Show B')
    temp_db.update_podcast('show-b', refresh_failure_count=3,
                           last_refresh_error='boom')

    temp_db.clear_refresh_failure_state('show-b')

    row = temp_db.get_podcast_by_slug('show-b')
    assert row['refresh_failure_count'] == 0
    assert row['last_refresh_error'] is None


def test_clean_feed_leaves_no_open_transaction(temp_db):
    """A rollback rather than a commit must not strand the write lock."""
    temp_db.create_podcast('show-c', 'https://example.com/c.xml', 'Show C')
    temp_db.clear_refresh_failure_state('show-c')

    assert temp_db.get_connection().in_transaction is False
    temp_db.update_podcast('show-c', title='Still Writable')
    assert temp_db.get_podcast_by_slug('show-c')['title'] == 'Still Writable'

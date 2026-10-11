"""Tests for Database.get_llm_stats."""
from tests.app_bootstrap import bootstrap

bootstrap('llm_stats_test_')

COLUMNS = (
    'attempt_id, created_at, phase_key, provider_key, configured_model, state, '
    'dispatch_count, dispatch_latency_ms, call_latency_ms, input_tokens, output_tokens, '
    'cache_read_tokens, cache_write_tokens, reasoning_tokens, cost_usd'
)
INSERT_SQL = f'INSERT INTO llm_call_usage ({COLUMNS}) VALUES ({",".join("?" * 15)})'  # noqa: S608


def _seed(db):
    rows = [
        ('a1', '2026-01-01T10:00:00Z', 'detection', 'openai', 'm1', 'success',
         2, 200, 1000, 100, 10, 5, 1, 2, '0.5'),
        ('a2', '2026-01-02T10:00:00Z', 'review', 'openai', 'm1', 'success',
         1, 100, 3000, 50, 5, 0, 0, 0, '0.25'),
        ('a3', '2026-01-03T10:00:00Z', 'detection', 'anthropic', 'm2', 'failure',
         1, 60, None, None, None, None, None, None, None),
        ('a4', '2026-01-02T10:00:00Z', 'detection', 'typesafe', 'jev', 'success',
         1, 10, 10, 999, 999, 0, 0, 0, '9'),
    ]
    conn = db.get_connection()
    for row in rows:
        conn.execute(INSERT_SQL, row)
    conn.commit()


def test_llm_stats_aggregates_and_excludes_system_one(temp_db):
    _seed(temp_db)
    stats = temp_db.get_llm_stats()
    assert stats['calls'] == 3
    assert stats['requests'] == 4
    assert stats['outcomes'] == {'success': 2, 'failure': 1, 'inconclusive': 0, 'inFlight': 0}
    assert stats['callLatencyMsAverage'] == 2000
    assert stats['dispatchLatencyMsAverage'] == 90
    assert stats['tokens'] == {
        'input': 150, 'output': 15, 'cacheRead': 5, 'cacheWrite': 1,
        'reasoning': 2, 'unknownRequestCount': 1,
    }
    assert stats['costUsd'] == '0.75'
    assert stats['unknownCostRequestCount'] == 1
    assert list(stats['byPhase']) == ['detection', 'review']
    assert stats['byPhase']['detection'] == {'calls': 2, 'costUsd': '0.5'}
    assert stats['byPhase']['review'] == {'calls': 1, 'costUsd': '0.25'}


def test_llm_stats_empty_has_null_averages(temp_db):
    stats = temp_db.get_llm_stats()
    assert stats['calls'] == 0
    assert stats['callLatencyMsAverage'] is None
    assert stats['dispatchLatencyMsAverage'] is None
    assert stats['byPhase'] == {}
    assert stats['costUsd'] == '0'


def test_llm_stats_date_and_provider_filters(temp_db):
    _seed(temp_db)
    assert temp_db.get_llm_stats(from_date='2026-01-02', to_date='2026-01-02')['calls'] == 1
    assert temp_db.get_llm_stats(from_date='2026-01-02')['calls'] == 2
    assert temp_db.get_llm_stats(provider='openai')['calls'] == 2
    assert temp_db.get_llm_stats(model='m2')['calls'] == 1
    assert temp_db.get_llm_stats(provider='typesafe')['calls'] == 0

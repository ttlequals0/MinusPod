"""Cleanup attribution, durable suggestions, and historical coverage."""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import pattern_cleanup
import run_context
from database import Database
from llm_client import ProviderRateLimitedError
from pattern_cleanup import SuggestionNotFoundError, apply_suggestion, reject_suggestion, undo_suggestion
from tests.integration.test_stats_lists_api import _authed
from tests.unit.test_pattern_cleanup_service import AD, LIVE, ORIGINAL_ROUTE, _pattern, _reply


def _attempt(db, run_id, pattern, *, state='failure', known=False, provider='anthropic'):
    attempt = db.reserve_llm_attempt(
        run_id=None, podcast_id=None, episode_id=None, phase_key='pattern_cleanup',
        invoking_pass=None, provider_key=provider, configured_model='test-model',
        cleanup_run_id=run_id, cleanup_pattern_id=pattern['id'])['attempt_id']
    db.finalize_llm_attempt(attempt, state=state,
                            input_tokens=17 if known else None,
                            output_tokens=0 if known else None)
    return attempt


def test_cleanup_reviews_attribute_each_attempt_and_restore_context(temp_db, monkeypatch):
    temp_db.create_podcast('show-a', 'https://example.com/feed.xml', 'Example')
    pattern = _pattern(temp_db, text=AD)
    client = MagicMock()
    client.uses_per_request_dispatch = False
    client.messages_create.return_value = _reply()
    monkeypatch.setattr(pattern_cleanup, 'client_for_route', lambda _route: client)
    monkeypatch.setattr(pattern_cleanup, 'resolve_route', lambda _phase: ORIGINAL_ROUTE)
    monkeypatch.setattr(pattern_cleanup, '_live_route', lambda _route: LIVE)
    summary = pattern_cleanup.run_cleanup(temp_db)
    row = temp_db.get_connection().execute('SELECT * FROM llm_call_usage').fetchone()
    assert summary['status'] == 'completed'
    assert row['run_id'] is None
    assert (row['cleanup_run_id'], row['cleanup_pattern_id']) == (summary['runId'], pattern['id'])
    assert row['podcast_id'] == temp_db.get_podcast_by_slug('show-a')['id']
    assert row['episode_id'] is None
    assert run_context.current_cleanup_review() == {}
    stats = temp_db.get_cleanup_stats(podcast_slug='show-a')
    assert stats['usage']['requests'] == 1
    assert stats['usage']['unknownUsageRequestCount'] == 1
    assert stats['patterns']['checked'] == stats['patterns']['modelReviewed'] == 1


def test_cleanup_counts_checks_once_and_retains_failed_unknown_attempts(temp_db):
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    temp_db.record_cleanup_check(run_id, pattern, stats_checked=True)
    temp_db.record_cleanup_check(run_id, pattern, model_status='running')
    _attempt(temp_db, run_id, pattern)
    _attempt(temp_db, run_id, pattern, state='cancelled', known=True)
    temp_db.record_cleanup_check(run_id, pattern, model_status='failed')
    stats = temp_db.get_cleanup_stats()
    assert stats['runs']['running'] == 1
    assert stats['patterns']['checked'] == stats['patterns']['distinctChecked'] == 1
    assert stats['patterns']['modelReviewed'] == 0
    assert stats['usage']['requests'] == 2
    assert stats['usage']['inputTokens'] == 17
    assert stats['usage']['unknownUsageRequestCount'] == 1
    assert stats['usage']['unknownCostRequestCount'] == 2
    assert Decimal(stats['usage']['knownCostUsd']) == 0


@pytest.mark.parametrize('operation', [apply_suggestion, reject_suggestion, undo_suggestion])
def test_superseded_ids_cannot_change_patterns(temp_db, operation):
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=True, trigger='manual')
    first = temp_db.upsert_cleanup_suggestion(run_id, pattern['id'], 'trim', 0.9, [], {}, {})
    second = temp_db.upsert_cleanup_suggestion(run_id, pattern['id'], 'trim', 0.9, [], {}, {})
    with pytest.raises(SuggestionNotFoundError):
        operation(temp_db, first)
    assert temp_db.get_cleanup_pending_counts()['total'] == 1
    rows = temp_db.get_connection().execute(
        'SELECT id, superseded_at FROM pattern_cleanup_suggestions ORDER BY id').fetchall()
    assert rows[0]['superseded_at'] is not None
    assert rows[1]['id'] == second and rows[1]['superseded_at'] is None
    assert temp_db.get_cleanup_stats()['actions']['trim']['proposed'] == 1
    temp_db.reset_cleanup_force_state()
    assert temp_db.get_cleanup_pending_counts()['total'] == 0
    assert temp_db.get_cleanup_stats()['actions']['trim']['proposed'] == 1


def test_cleanup_applied_and_reverted_are_distinct_from_proposals(temp_db):
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    temp_db.record_cleanup_check(run_id, pattern, stats_checked=True)
    suggestion = temp_db.upsert_cleanup_suggestion(
        run_id, pattern['id'], 'category', 0.9, [], {'category': 'self_promo'},
        pattern_cleanup._before_snapshot(pattern))
    apply_suggestion(temp_db, suggestion)
    stats = temp_db.get_cleanup_stats()
    assert stats['patterns']['proposed'] == stats['patterns']['changed'] == 1
    assert stats['actions']['category'] == {'proposed': 1, 'accepted': 1, 'applied': 1, 'reverted': 0}
    undo_suggestion(temp_db, suggestion)
    stats = temp_db.get_cleanup_stats()
    assert stats['patterns']['changed'] == 1
    assert stats['actions']['category'] == {'proposed': 1, 'accepted': 1, 'applied': 0, 'reverted': 1}


def test_cleanup_feed_scope_and_run_cohort_do_not_infer_global_spend(temp_db):
    scoped = _pattern(temp_db, text=AD)
    global_pattern = _pattern(temp_db)
    temp_db.update_ad_pattern(global_pattern['id'], scope='global')
    global_pattern = temp_db.get_ad_pattern_by_id(global_pattern['id'])
    run_id = temp_db.create_cleanup_run(
        forced=False, trigger='manual', started_at='2026-09-01T23:59:00Z')
    for pattern in (scoped, global_pattern):
        temp_db.record_cleanup_check(run_id, pattern, model_status='completed')
        _attempt(temp_db, run_id, pattern, known=True)
    legacy = _attempt(temp_db, None, scoped)
    conn = temp_db.get_connection()
    conn.execute("UPDATE llm_call_usage SET created_at = '2026-09-02T00:01:00Z'")
    conn.commit()
    stats = temp_db.get_cleanup_stats(from_date='2026-09-01', to_date='2026-09-01',
                                      podcast_slug='show-a')
    assert stats['patterns']['checked'] == stats['usage']['requests'] == 1
    assert stats['unattributedUsage'] is None
    assert temp_db.get_cleanup_stats(to_date='2026-09-01')['usage']['requests'] == 2
    assert temp_db.get_cleanup_stats(from_date='2026-09-02')['usage']['requests'] == 0
    assert temp_db.get_cleanup_stats(from_date='2026-09-02')['unattributedUsage']['requests'] == 1
    assert conn.execute('SELECT cleanup_run_id FROM llm_call_usage WHERE attempt_id = ?',
                        (legacy,)).fetchone()[0] is None
    assert temp_db.get_cleanup_stats(provider='openai')['runs']['total'] == 0


@pytest.mark.parametrize('snapshot_scope', [False, True])
def test_cleanup_accounting_migration_preserves_rows_and_history(temp_dir, snapshot_scope):
    db = object.__new__(Database)
    db._initialized = False
    Database.__init__(db, data_dir=temp_dir)
    pattern = _pattern(db)
    run_id = db.create_cleanup_run(forced=False, trigger='manual')
    before = {'scope': 'podcast', 'podcast_id': 'show-a'} if snapshot_scope else {}
    suggestion = db.upsert_cleanup_suggestion(run_id, pattern['id'], 'trim', 0.9, [], {}, before)
    attempt = _attempt(db, run_id, pattern)
    conn = db.get_connection()
    conn.execute("UPDATE ad_patterns SET scope = 'global' WHERE id = ?", (pattern['id'],))
    conn.execute('DROP TRIGGER archive_cleanup_actions_on_pattern_delete')
    conn.execute('DROP INDEX idx_cleanup_suggestions_pending')
    for column in ('superseded_at', 'pattern_scope', 'podcast_slug'):
        conn.execute(f'ALTER TABLE pattern_cleanup_suggestions DROP COLUMN {column}')
    conn.execute('DROP INDEX idx_llm_usage_cleanup_run')
    for column in ('cleanup_run_id', 'cleanup_pattern_id'):
        conn.execute(f'ALTER TABLE llm_call_usage DROP COLUMN {column}')
    conn.execute('ALTER TABLE pattern_cleanup_runs DROP COLUMN accounting_version')
    conn.execute("UPDATE pattern_cleanup_runs SET reviewed_count = 7, status = 'failed'")
    conn.execute("DELETE FROM schema_migrations WHERE name = 'pattern_cleanup_accounting_v1'")
    conn.commit()
    db._run_schema_migrations()
    restored = db.get_cleanup_suggestion(suggestion)
    assert restored['pattern_scope'] == ('podcast' if snapshot_scope else None)
    assert restored['podcast_slug'] == ('show-a' if snapshot_scope else None)
    assert db.get_cleanup_stats(podcast_slug='show-a')['actions']['trim']['proposed'] == int(snapshot_scope)
    assert restored['status'] == 'pending'
    assert conn.execute('SELECT cleanup_run_id FROM llm_call_usage WHERE attempt_id = ?',
                        (attempt,)).fetchone()[0] is None
    stats = db.get_cleanup_stats()
    assert stats['runs']['legacy'] == stats['coverage']['historicalRunCountWithUnknownSpend'] == 1
    assert stats['patterns']['checked'] == stats['usage']['requests'] == 0
    assert stats['patterns']['legacyReportedReviews'] == 7
    assert stats['unattributedUsage']['requests'] == 1
    assert stats['actions']['trim']['proposed'] == 1
    replacement = db.upsert_cleanup_suggestion(run_id, pattern['id'], 'trim', 0.9, [], {}, {})
    assert replacement > suggestion
    assert conn.execute('SELECT COUNT(*) FROM pattern_cleanup_suggestions').fetchone()[0] == 2


def test_cleanup_stats_api_filters_and_date_validation(app_client, temp_db):
    _authed(app_client)
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    temp_db.record_cleanup_check(run_id, pattern, model_status='completed')
    _attempt(temp_db, run_id, pattern)
    response = app_client.get('/api/v1/stats/cleanup?podcastSlug=show-a&provider=anthropic&model=test-model')
    assert response.status_code == 200
    assert response.get_json()['usage']['unknownCostRequestCount'] == 1
    assert app_client.get('/api/v1/stats/cleanup?from=invalid').status_code == 400


def test_cleanup_feed_deletion_detaches_checks_without_losing_global_usage(temp_db):
    temp_db.create_podcast('show-a', 'https://example.com/feed.xml', 'Example')
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    temp_db.record_cleanup_check(run_id, pattern, stats_checked=True)
    _attempt(temp_db, run_id, pattern)
    assert temp_db.delete_podcast('show-a')
    temp_db.create_podcast('show-a', 'https://example.com/other.xml', 'Example')
    assert temp_db.get_cleanup_stats(podcast_slug='show-a')['patterns']['checked'] == 0
    assert temp_db.get_cleanup_stats()['patterns']['checked'] == 1
    assert temp_db.get_cleanup_stats()['usage']['requests'] == 1


@pytest.mark.parametrize('error', [RuntimeError('review failed'),
                                  ProviderRateLimitedError('held', 60.0, provider_key='anthropic')])
def test_cleanup_error_stats_do_not_commit_leaked_pattern_edits(temp_db, monkeypatch, error):
    pattern = _pattern(temp_db, text=AD)
    monkeypatch.setattr(pattern_cleanup, 'resolve_route', lambda _phase: ORIGINAL_ROUTE)
    monkeypatch.setattr(pattern_cleanup, '_live_route', lambda _route: LIVE)

    def fail_with_uncommitted_edit(*args, **kwargs):
        temp_db.get_connection().execute('UPDATE ad_patterns SET text_template = ? WHERE id = ?',
                                        (AD + AD, pattern['id']))
        raise error

    monkeypatch.setattr(pattern_cleanup, '_process_pattern', fail_with_uncommitted_edit)
    result = pattern_cleanup.run_cleanup(temp_db)
    assert temp_db.get_ad_pattern_by_id(pattern['id'])['text_template'] == AD
    check = temp_db.get_connection().execute('SELECT model_status FROM pattern_cleanup_checks').fetchone()
    assert check['model_status'] == 'failed'
    assert result['status'] == ('failed' if isinstance(error, ProviderRateLimitedError) else 'completed')


@pytest.mark.parametrize('undone', [False, True])
@pytest.mark.parametrize('foreign_keys', [False, True])
def test_cleanup_pattern_deletion_preserves_compact_action_history(temp_db, undone, foreign_keys):
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    suggestion = temp_db.upsert_cleanup_suggestion(
        run_id, pattern['id'], 'category', 0.9, [], {'category': 'self_promo'},
        pattern_cleanup._before_snapshot(pattern))
    apply_suggestion(temp_db, suggestion)
    if undone:
        undo_suggestion(temp_db, suggestion)
    before = temp_db.get_cleanup_stats()['actions']
    conn = temp_db.get_connection()
    conn.execute(f'PRAGMA foreign_keys = {int(foreign_keys)}')
    conn.execute('DELETE FROM ad_patterns WHERE id = ?', (pattern['id'],))
    conn.commit()
    assert temp_db.get_cleanup_stats()['actions'] == before
    assert temp_db.get_cleanup_suggestion(suggestion) is None
    with pytest.raises(SuggestionNotFoundError):
        apply_suggestion(temp_db, suggestion)
    archived = conn.execute('SELECT * FROM pattern_cleanup_deleted_actions').fetchone()
    assert archived['suggestion_id'] == suggestion and archived['pattern_id'] == pattern['id']
    assert set(archived.keys()) == {
        'suggestion_id', 'run_id', 'pattern_id', 'pattern_scope', 'podcast_slug', 'kind', 'status',
    }
    recreated = _pattern(temp_db)
    replacement = temp_db.upsert_cleanup_suggestion(run_id, recreated['id'], 'category', 0.9, [], {}, {})
    assert replacement > suggestion and recreated['id'] != pattern['id']
    assert temp_db.get_cleanup_stats()['actions']['category']['proposed'] == 2


def test_cleanup_feed_delete_keeps_actions_unallocated_on_reused_slug(temp_db):
    temp_db.create_podcast('show-a', 'https://example.com/feed.xml', 'Example')
    pattern = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='manual')
    suggestion = temp_db.upsert_cleanup_suggestion(run_id, pattern['id'], 'trim', 0.9, [], {}, {})
    temp_db.delete_podcast('show-a')
    temp_db.create_podcast('show-a', 'https://example.com/other.xml', 'Example')
    assert temp_db.get_cleanup_stats()['actions']['trim']['proposed'] == 1
    assert temp_db.get_cleanup_stats(podcast_slug='show-a')['actions']['trim']['proposed'] == 0
    row = temp_db.get_connection().execute('SELECT * FROM pattern_cleanup_deleted_actions').fetchone()
    assert row['suggestion_id'] == suggestion and row['podcast_slug'] is None

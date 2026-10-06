"""Pattern cleanup storage: tables, columns, suggestion upsert and listing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402


def _pattern(db, text='This episode is brought to you by Acme widgets today.',
             created_by='auto', source='local', podcast_id='show-a'):
    pid = db.create_ad_pattern(scope='podcast', text_template=text,
                               podcast_id=podcast_id, created_by=created_by,
                               source=source)
    return pid


def _cols(db, table):
    return {r[1] for r in db.get_connection().execute(f"PRAGMA table_info({table})")}


def test_fresh_database_has_cleanup_tables_and_columns(temp_db):
    assert {'cleanup_reviewed_at', 'cleanup_reviewed_hash', 'cleanup_stats_reviewed'} <= _cols(temp_db, 'ad_patterns')
    assert {'id', 'started_at', 'finished_at', 'status', 'forced', 'trigger', 'model',
            'provider', 'credential_slot', 'reviewed_count', 'suggested_count',
            'skipped_count', 'error'} <= _cols(temp_db, 'pattern_cleanup_runs')
    assert {'id', 'run_id', 'pattern_id', 'kind', 'status', 'confidence', 'reasons',
            'payload', 'before', 'applied', 'created_at',
            'reviewed_at'} <= _cols(temp_db, 'pattern_cleanup_suggestions')


def _isolated(data_dir):
    db = object.__new__(Database)
    db._initialized = False
    Database.__init__(db, data_dir=data_dir)
    return db


def test_existing_database_gains_tables_and_columns_without_data_loss(temp_dir):
    db = _isolated(temp_dir)
    pids = [_pattern(db, text=f'legacy cleanup row {i}') for i in range(2)]
    conn = db.get_connection()
    conn.execute("DROP TABLE pattern_cleanup_suggestions")
    conn.execute("DROP TABLE pattern_cleanup_runs")
    conn.execute("UPDATE ad_patterns SET cleanup_reviewed_hash = 'preserved'")
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_stats_reviewed")
    conn.commit()
    conn.close()

    db2 = _isolated(temp_dir)
    assert 'cleanup_reviewed_hash' in _cols(db2, 'ad_patterns')
    assert 'cleanup_stats_reviewed' in _cols(db2, 'ad_patterns')
    assert _cols(db2, 'pattern_cleanup_runs')
    assert _cols(db2, 'pattern_cleanup_suggestions')
    for pid in pids:
        restored = db2.get_ad_pattern_by_id(pid)
        assert restored['text_template'].startswith('legacy cleanup row')
        assert restored['cleanup_reviewed_hash'] == 'preserved'
        assert restored['cleanup_stats_reviewed'] is None


def test_existing_database_restores_old_cleanup_stamp_columns(temp_dir):
    db = _isolated(temp_dir)
    pid = _pattern(db)
    conn = db.get_connection()
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_reviewed_at")
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_reviewed_hash")
    conn.commit()
    conn.close()

    migrated = _isolated(temp_dir)
    row = migrated.get_ad_pattern_by_id(pid)
    assert {'cleanup_reviewed_at', 'cleanup_reviewed_hash'} <= _cols(migrated, 'ad_patterns')
    assert row['text_template'].startswith('This episode')
    assert row['cleanup_reviewed_at'] is None
    assert row['cleanup_reviewed_hash'] is None


def test_stats_column_migration_preserves_decisions_and_review_stamps(temp_dir):
    db = _isolated(temp_dir)
    pid = _pattern(db)
    db.stamp_pattern_cleanup_reviewed(pid, 'reviewed')
    approved = db.upsert_cleanup_suggestion(
        None, pid, 'retire', 1.0, [], {'unused_days': 90}, {'text_template': 'old', 'sponsor': None})
    db.set_cleanup_suggestion_status(approved, 'approved')
    rejected = db.upsert_cleanup_suggestion(
        None, pid, 'flag', 1.0, [], {'recommended': 'disable'}, {'text_template': 'old', 'sponsor': None})
    db.set_cleanup_suggestion_status(rejected, 'rejected')
    conn = db.get_connection()
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_stats_reviewed")
    conn.commit()
    conn.close()

    migrated = _isolated(temp_dir)
    row = migrated.get_ad_pattern_by_id(pid)
    assert row['cleanup_reviewed_hash'] == 'reviewed'
    assert row['cleanup_stats_reviewed'] is None
    decisions = migrated.get_cleanup_stat_decisions(pid)
    assert [(item['id'], item['status']) for item in decisions] == [
        (approved, 'approved'), (rejected, 'rejected'),
    ]


def test_run_lifecycle(temp_db):
    run_id = temp_db.create_cleanup_run(forced=True, trigger='manual')
    temp_db.finish_cleanup_run(run_id, status='completed', reviewed=3, suggested=2,
                               skipped=1, model='m', provider='anthropic',
                               credential_slot='primary')
    run = temp_db.get_cleanup_runs(limit=5)[0]
    assert run['id'] == run_id
    assert run['status'] == 'completed'
    assert run['forced'] == 1 and run['trigger'] == 'manual'
    assert (run['reviewed_count'], run['suggested_count'], run['skipped_count']) == (3, 2, 1)
    assert run['finished_at']
    assert run['model'] == 'm'


def test_upsert_replaces_pending_of_same_kind_only(temp_db):
    pid = _pattern(temp_db)
    run_id = temp_db.create_cleanup_run(forced=False, trigger='schedule')
    first = temp_db.upsert_cleanup_suggestion(run_id, pid, 'trim', 0.9, ['a'], {'text': 'x'}, {})
    other = temp_db.upsert_cleanup_suggestion(run_id, pid, 'rename', 0.8, [], {'sponsor': 'Acme'}, {})
    second = temp_db.upsert_cleanup_suggestion(run_id, pid, 'trim', 0.7, ['b'], {'text': 'y'}, {})
    assert temp_db.get_cleanup_suggestion(first) is None
    kept = temp_db.get_cleanup_suggestion(second)
    assert kept['payload'] == {'text': 'y'} and kept['reasons'] == ['b']
    assert temp_db.get_cleanup_suggestion(other)['kind'] == 'rename'


def test_upsert_keeps_decided_suggestions(temp_db):
    pid = _pattern(temp_db)
    first = temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})
    temp_db.set_cleanup_suggestion_status(first, 'rejected')
    second = temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})
    assert temp_db.get_cleanup_suggestion(first)['status'] == 'rejected'
    assert temp_db.get_cleanup_suggestion(second)['status'] == 'pending'
    assert temp_db.get_cleanup_suggestion(first)['reviewed_at']


def test_list_filters_and_pattern_summary(temp_db):
    temp_db.create_podcast('show-a', 'http://example.com/feed', title='The Daily Tech Show')
    pid = _pattern(temp_db)
    temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})
    temp_db.upsert_cleanup_suggestion(None, pid, 'retire', 1.0, [], {'unused_days': 90}, {})
    rows = temp_db.get_cleanup_suggestions(status='pending', kind='trim')
    assert len(rows) == 1
    row = rows[0]
    assert row['pattern']['id'] == pid
    assert row['pattern']['podcast_title'] == 'The Daily Tech Show'
    assert row['pattern']['scope'] == 'podcast'
    assert 'confirmation_count' in row['pattern']
    assert len(temp_db.get_cleanup_suggestions(status='pending')) == 2
    assert temp_db.get_cleanup_suggestions(status='approved') == []
    assert temp_db.get_cleanup_pending_counts() == {'total': 2, 'byKind': {'trim': 1, 'retire': 1}}


def test_candidate_rows_scope_and_order(temp_db):
    a = _pattern(temp_db, text='alpha')
    b = _pattern(temp_db, text='beta')
    _pattern(temp_db, text='manual', created_by='user')
    _pattern(temp_db, text='community', source='community')
    disabled = _pattern(temp_db, text='off')
    temp_db.update_ad_pattern(disabled, is_active=0)
    temp_db.stamp_pattern_cleanup_reviewed(a, 'h')
    ids = [r['id'] for r in temp_db.get_cleanup_candidate_rows()]
    assert ids == [b, a]


def test_candidate_rows_capped_to_batch_size_plus_margin(temp_db):
    from database.pattern_cleanup import CANDIDATE_ROW_MARGIN
    for i in range(500):
        _pattern(temp_db, text=f'pattern number {i} unique ad copy here today')
    rows = temp_db.get_cleanup_candidate_rows(force=False, batch_size=25)
    assert len(rows) <= 25 + CANDIDATE_ROW_MARGIN


def test_force_reset_is_atomic_unbounded_and_preserves_history_and_nontargets(temp_db):
    learned = [_pattern(temp_db, text=f'learned ad pattern {i}') for i in range(40)]
    retained_id = None
    for i, pid in enumerate(learned):
        temp_db.stamp_pattern_cleanup_reviewed(pid, f'review-{pid}')
        temp_db.set_cleanup_stats_reviewed(pid, {'retire': f'evidence-{pid}'})
        sid = temp_db.upsert_cleanup_suggestion(
            None, pid, 'trim', 0.9, [], {'text': 'new copy'},
            {'text_template': f'learned ad pattern {i}', 'sponsor': None})
        if pid == learned[0]:
            temp_db.set_cleanup_suggestion_status(sid, 'rejected')
            retained_id = sid
            temp_db.upsert_cleanup_suggestion(
                None, pid, 'trim', 0.9, [], {'text': 'pending copy'},
                {'text_template': f'learned ad pattern {i}', 'sponsor': None})
            temp_db.upsert_cleanup_suggestion(
                None, pid, 'retire', 1.0, [], {'unused_days': 90},
                {'text_template': f'learned ad pattern {i}', 'sponsor': None})

    manual = _pattern(temp_db, text='manual pattern', created_by='user')
    community = _pattern(temp_db, text='community pattern', source='community')
    inactive = _pattern(temp_db, text='inactive pattern')
    temp_db.update_ad_pattern(inactive, is_active=0)
    for pid in (manual, community, inactive):
        temp_db.stamp_pattern_cleanup_reviewed(pid, f'preserve-{pid}')
        temp_db.set_cleanup_stats_reviewed(pid, {'flag': f'preserve-{pid}'})
        temp_db.upsert_cleanup_suggestion(
            None, pid, 'trim', 0.9, [], {'text': 'copy'}, {})

    with pytest.raises(RuntimeError):
        with temp_db.transaction(immediate=True) as conn:
            assert temp_db.reset_cleanup_force_state(conn=conn) == 40
            raise RuntimeError('rollback')

    for pid in learned:
        row = temp_db.get_ad_pattern_by_id(pid)
        assert row['cleanup_reviewed_hash'] == f'review-{pid}'
        assert row['cleanup_stats_reviewed'] == f'{{"retire": "evidence-{pid}"}}'
        assert temp_db.has_pending_cleanup_suggestion(pid)

    assert temp_db.reset_cleanup_force_state() == 40
    for pid in learned:
        row = temp_db.get_ad_pattern_by_id(pid)
        assert row['cleanup_reviewed_at'] is None
        assert row['cleanup_reviewed_hash'] is None
        assert temp_db.get_cleanup_stats_reviewed(pid) == {}
        assert not temp_db.has_pending_cleanup_suggestion(pid)
    assert temp_db.get_cleanup_suggestion(retained_id)['status'] == 'rejected'
    for pid in (manual, community, inactive):
        row = temp_db.get_ad_pattern_by_id(pid)
        assert row['cleanup_reviewed_hash'] == f'preserve-{pid}'
        assert temp_db.get_cleanup_stats_reviewed(pid) == {'flag': f'preserve-{pid}'}
        assert temp_db.has_pending_cleanup_suggestion(pid)


def test_stats_rows_and_acknowledgments_ignore_review_stamps_and_pending(temp_db):
    learned = _pattern(temp_db)
    other = _pattern(temp_db, text='other learned copy')
    manual = _pattern(temp_db, text='manual', created_by='user')
    temp_db.update_ad_pattern(other, is_active=0)
    temp_db.stamp_pattern_cleanup_reviewed(learned, 'done')
    temp_db.upsert_cleanup_suggestion(None, learned, 'retire', 1.0, [], {}, {})

    rows = temp_db.get_cleanup_stats_rows()
    assert [row['id'] for row in rows] == [learned]
    assert rows[0]['cleanup_reviewed_hash'] == 'done'
    assert temp_db.get_cleanup_stats_reviewed(learned) is None
    temp_db.set_cleanup_stats_reviewed(learned, {})
    assert temp_db.get_cleanup_stats_reviewed(learned) == {}
    temp_db.set_cleanup_stats_reviewed(learned, {'retire': 'abc'})
    assert temp_db.get_cleanup_stats_reviewed(learned) == {'retire': 'abc'}
    assert temp_db.get_cleanup_stats_reviewed(manual) is None


def test_candidate_rows_distinguish_stats_pending_from_current_model_pending(temp_db):
    same_model = _pattern(temp_db, text='same model content')
    stat_retire = _pattern(temp_db, text='retirement content')
    stat_flag = _pattern(temp_db, text='statistics flag content')
    model_flag = _pattern(temp_db, text='contaminated flag content')
    stale_model = _pattern(temp_db, text='changed model content')
    legacy_model = _pattern(temp_db, text='legacy pending content')

    def pending(pid, kind, payload, text):
        temp_db.upsert_cleanup_suggestion(
            None, pid, kind, 0.9, [], payload,
            {'text_template': text, 'sponsor': None})

    pending(same_model, 'trim', {'text': 'trimmed'}, 'same model content')
    pending(stat_retire, 'retire', {'unused_days': 90}, 'retirement content')
    pending(stat_flag, 'flag', {
        'contaminated': False, 'recommended': 'disable',
    }, 'statistics flag content')
    pending(model_flag, 'flag', {
        'contaminated': True, 'recommended': 'disable',
    }, 'contaminated flag content')
    pending(stale_model, 'split', {'pieces': []}, 'old model content')
    temp_db.upsert_cleanup_suggestion(None, legacy_model, 'trim', 0.9, [], {'text': 'copy'}, {})

    rows = {row['id']: row for row in temp_db.get_cleanup_candidate_rows()}
    assert same_model not in rows
    assert model_flag not in rows
    assert legacy_model not in rows
    assert rows[stat_retire]['has_pending_model'] == 0
    assert rows[stat_flag]['has_pending_model'] == 0
    assert rows[stale_model]['has_pending_model'] == 0


def test_get_cleanup_stat_decisions_returns_only_retained_stat_decisions(temp_db):
    pid = _pattern(temp_db)
    expected = []
    for status in ('approved', 'rejected', 'undone'):
        sid = temp_db.upsert_cleanup_suggestion(
            None, pid, 'retire', 1.0, [], {'unused_days': 90},
            {'text_template': 'reviewed version', 'sponsor': None})
        temp_db.set_cleanup_suggestion_status(sid, status)
        expected.append(sid)
    model_id = temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})

    decisions = temp_db.get_cleanup_stat_decisions(pid)
    assert [row['id'] for row in decisions] == expected
    assert all(row['kind'] == 'retire' for row in decisions)
    assert model_id not in [row['id'] for row in decisions]


def test_suggestions_cascade_with_pattern_delete(temp_db):
    pid = _pattern(temp_db)
    sid = temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM ad_patterns WHERE id = ?", (pid,))
    conn.commit()
    assert temp_db.get_cleanup_suggestion(sid) is None

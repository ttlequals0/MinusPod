"""Pattern cleanup storage: tables, columns, suggestion upsert and listing."""
import os
import sys

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
    assert {'cleanup_reviewed_at', 'cleanup_reviewed_hash'} <= _cols(temp_db, 'ad_patterns')
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
    pid = _pattern(db)
    conn = db.get_connection()
    conn.execute("DROP TABLE pattern_cleanup_suggestions")
    conn.execute("DROP TABLE pattern_cleanup_runs")
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_reviewed_at")
    conn.execute("ALTER TABLE ad_patterns DROP COLUMN cleanup_reviewed_hash")
    conn.commit()
    conn.close()

    db2 = _isolated(temp_dir)
    assert 'cleanup_reviewed_hash' in _cols(db2, 'ad_patterns')
    assert _cols(db2, 'pattern_cleanup_runs')
    assert _cols(db2, 'pattern_cleanup_suggestions')
    assert db2.get_ad_pattern_by_id(pid)['text_template'].startswith('This episode')


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


def test_reviewed_stamp_and_clear_only_touch_learned_patterns(temp_db):
    learned = _pattern(temp_db)
    manual = _pattern(temp_db, created_by='user')
    temp_db.stamp_pattern_cleanup_reviewed(learned, 'h1')
    temp_db.stamp_pattern_cleanup_reviewed(manual, 'h2')
    temp_db.clear_cleanup_reviewed()
    assert temp_db.get_ad_pattern_by_id(learned)['cleanup_reviewed_hash'] is None
    assert temp_db.get_ad_pattern_by_id(manual)['cleanup_reviewed_hash'] == 'h2'


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


def test_suggestions_cascade_with_pattern_delete(temp_db):
    pid = _pattern(temp_db)
    sid = temp_db.upsert_cleanup_suggestion(None, pid, 'trim', 0.9, [], {'text': 'x'}, {})
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM ad_patterns WHERE id = ?", (pid,))
    conn.commit()
    assert temp_db.get_cleanup_suggestion(sid) is None

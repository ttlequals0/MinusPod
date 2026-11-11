from database import Database


def test_sqlite_diagnostics_reports_scoped_metrics_without_path(tmp_path):
    db = Database(tmp_path)
    diagnostics = db.sqlite_diagnostics()

    assert diagnostics['journalMode'] == 'wal'
    assert diagnostics['databaseBytes'] > 0
    assert diagnostics['walBytes'] >= 0
    assert diagnostics['instrumentation']['scope'] == 'worker_process'
    assert diagnostics['instrumentation']['processId'] > 0
    assert str(tmp_path) not in repr(diagnostics)


def test_passive_checkpoint_returns_sqlite_counts(tmp_path):
    db = Database(tmp_path)
    db.get_connection().execute('CREATE TABLE IF NOT EXISTS checkpoint_probe (x INTEGER)')
    db.get_connection().execute('INSERT INTO checkpoint_probe VALUES (1)')
    db.get_connection().commit()

    result = db.checkpoint_wal()

    assert set(result) == {'mode', 'busy', 'logPages', 'checkpointedPages', 'durationMs'}
    assert result['mode'] == 'passive'
    assert result['busy'] in (0, 1)
    assert result['logPages'] >= 0
    assert result['checkpointedPages'] >= 0
    assert result['durationMs'] >= 0


def test_journal_size_limit_is_64mb_on_a_new_connection(tmp_path):
    db = Database(tmp_path)
    limit = db.get_connection().execute('PRAGMA journal_size_limit').fetchone()[0]
    assert limit == 64 * 1024 * 1024


def test_truncate_checkpoint_reports_its_mode(tmp_path):
    db = Database(tmp_path)
    db.get_connection().execute('CREATE TABLE t (x)')
    db.get_connection().commit()
    result = db.checkpoint_wal(mode='truncate')
    assert result['mode'] == 'truncate'
    assert set(result) >= {'busy', 'logPages', 'checkpointedPages', 'durationMs', 'mode'}


def test_truncate_checkpoint_empties_the_wal_file(tmp_path):
    db = Database(tmp_path)
    conn = db.get_connection()
    conn.execute('CREATE TABLE wal_fill (x)')
    conn.executemany('INSERT INTO wal_fill VALUES (?)', [('x' * 1000,)] * 200)
    conn.commit()
    wal = db.db_path.with_name(db.db_path.name + '-wal')
    assert wal.stat().st_size > 0
    db.checkpoint_wal(mode='truncate')
    assert not wal.exists() or wal.stat().st_size == 0


def test_unknown_checkpoint_mode_runs_passive(tmp_path):
    db = Database(tmp_path)
    assert db.checkpoint_wal(mode='bogus')['mode'] == 'passive'

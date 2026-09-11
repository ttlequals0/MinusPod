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

    assert set(result) == {'busy', 'logPages', 'checkpointedPages', 'durationMs'}
    assert result['busy'] in (0, 1)
    assert result['logPages'] >= 0
    assert result['checkpointedPages'] >= 0
    assert result['durationMs'] >= 0

"""Error text stored by older builds is redacted once, in place, on an existing database."""
import json
import logging
import re
import threading

import pytest

import database.schema.deferred as deferred
from tests.conftest import _new_isolated_database
from tests.unit.test_scrub_query_strings import _CONTINUED_SECRETS, _PLANTED, _message_corpus, _min_times
from utils.http import redact_log_text, redact_stored_text

GATE = 'redact_stored_error_text'
RAW = 'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed'
CLEAN = 'GET https://<redacted>@cdn.example.com/a.mp3?<redacted> failed'
FEED_KEY = 'ab' * 32
RAW_KEY = f'refresh failed for /feed/{FEED_KEY}?k=SIG'
CLEAN_KEY = 'refresh failed for /feed/[redacted-feed-key]?<redacted>'
UNTOUCHED_URL = 'https://cdn.example.com/a.mp3?X-Amz-Signature=KEEP'


def _seed(db):
    conn = db.get_connection()
    conn.execute('PRAGMA foreign_keys = OFF')
    db.create_podcast('example-podcast', 'https://feeds.example.com/f.xml?key=KEEP', 'Example')
    pid = db.get_podcast_by_slug('example-podcast')['id']
    db.upsert_episode('example-podcast', 'a1b2c3d4e5f6', original_url=UNTOUCHED_URL, title='One', status='failed')
    db.upsert_episode('example-podcast', 'b1b2c3d4e5f6', original_url=UNTOUCHED_URL, title='Two', status='failed')
    eid = conn.execute("SELECT id FROM episodes WHERE episode_id = 'a1b2c3d4e5f6'").fetchone()['id']
    conn.execute("UPDATE episodes SET error_message = ?, chapters_regen_error = ?, sb_last_error = ? "
                 "WHERE episode_id = 'a1b2c3d4e5f6'", (RAW, RAW_KEY, RAW))
    conn.execute("UPDATE episodes SET error_message = 'Transcription failed' WHERE episode_id = 'b1b2c3d4e5f6'")
    conn.execute("UPDATE podcasts SET last_refresh_error = ? WHERE id = ?", (RAW_KEY, pid))
    conn.execute("INSERT INTO processing_history (podcast_id, podcast_slug, episode_id, status, error_message) "
                 "VALUES (?, 'example-podcast', 'a1b2c3d4e5f6', 'failed', ?)", (pid, RAW))
    conn.execute("INSERT INTO auto_process_queue (podcast_id, episode_id, original_url, status, error_message) "
                 "VALUES (?, 'a1b2c3d4e5f6', ?, 'failed', ?)", (pid, UNTOUCHED_URL, RAW))
    conn.execute("INSERT INTO pattern_cleanup_runs (status, error) VALUES ('failed', ?)", (RAW,))
    conn.execute("INSERT INTO cue_candidate_scans (podcast_id, episode_id, status, error) VALUES (?, 'a', 'error', ?)",
                 (pid, RAW))
    conn.execute("INSERT INTO cue_threshold_scans (podcast_id, episode_id, status, error) VALUES (?, 'a', 'error', ?)",
                 (pid, RAW))
    conn.execute("INSERT INTO cue_cross_episode_scans (podcast_id, episode_set_hash, status, error) "
                 "VALUES (?, 'h', 'error', ?)", (pid, RAW))
    conn.execute("INSERT INTO cue_window_optimize_scans (template_id, status, error) VALUES (999, 'error', ?)", (RAW,))
    conn.execute("INSERT INTO failover_events (target, action, source, reason) "
                 "VALUES ('llm:primary', 'trigger', 'auto', ?)", (RAW,))
    conn.execute("INSERT INTO episode_details (episode_id, dai_differential_json, upstream_transcript_json) "
                 "VALUES (?, ?, ?)", (eid, json.dumps({'status': 'error', 'regions': [], 'error': RAW}),
                                      json.dumps({'status': 'error', 'source_url': UNTOUCHED_URL, 'error': RAW})))
    conn.commit()
    conn.execute('PRAGMA foreign_keys = ON')
    db.set_setting('youtube_ytdlp_last_error', RAW, is_default=False)
    db.set_setting('community_sync_last_error', RAW_KEY, is_default=False)
    db.set_setting('db_backup_last_error', 'disk full', is_default=False)
    db.set_setting('failover_state:llm:primary', json.dumps(
        {'active': True, 'source': 'auto', 'since': '2026-11-11T00:00:00Z', 'reason': RAW}), is_default=False)
    db.set_setting('podping_node_health', json.dumps(
        {'https://node.example.com': {'consecutive_failures': 1, 'last_failure_reason': RAW}}), is_default=False)
    db.set_setting('feed_user_agent', UNTOUCHED_URL, is_default=False)
    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('example_last_error', ?)", (RAW,))
    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('example_note', ?)", (RAW,))
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()


def _upgrade(db, run=True):
    """Restart the schema init as the next start would, then run the background step."""
    db._init_schema()
    if run:
        assert db.run_deferred_upgrade_steps()


def _snapshot(db):
    conn = db.get_connection()
    tables = [r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "AND name NOT IN ('schema_migrations', 'search_index', 'deferred_upgrade_steps') AND name NOT LIKE 'search_index_%'")]
    out = {}
    for table in tables:
        for row in conn.execute(f'SELECT rowid AS rid, * FROM "{table}"'):  # noqa: S608
            for key in row.keys():
                if key != 'rid':
                    out[(table, row['rid'], key)] = row[key]
    return out


def _changed(before, after):
    return {cell: (before.get(cell), after[cell]) for cell in after if before.get(cell) != after[cell]}


EXPECTED_TEXT = {
    ('episodes', 'error_message'): CLEAN, ('episodes', 'chapters_regen_error'): CLEAN_KEY,
    ('episodes', 'sb_last_error'): CLEAN, ('podcasts', 'last_refresh_error'): CLEAN_KEY,
    ('processing_history', 'error_message'): CLEAN, ('auto_process_queue', 'error_message'): CLEAN,
    ('pattern_cleanup_runs', 'error'): CLEAN, ('cue_candidate_scans', 'error'): CLEAN,
    ('cue_threshold_scans', 'error'): CLEAN, ('cue_cross_episode_scans', 'error'): CLEAN,
    ('cue_window_optimize_scans', 'error'): CLEAN, ('failover_events', 'reason'): CLEAN,
}


def test_an_existing_database_is_scrubbed_and_nothing_else_changes(temp_db):
    _seed(temp_db)
    before = _snapshot(temp_db)
    _upgrade(temp_db)
    changed = _changed(before, _snapshot(temp_db))
    cells = {(table, column): new for (table, _rid, column), (_old, new) in changed.items()}
    assert {k: v for k, v in cells.items() if k[0] not in ('settings', 'system_settings', 'episode_details')} \
        == EXPECTED_TEXT
    assert json.loads(cells[('episode_details', 'dai_differential_json')])['error'] == CLEAN
    upstream = json.loads(cells[('episode_details', 'upstream_transcript_json')])
    assert upstream['error'] == CLEAN and upstream['source_url'] == UNTOUCHED_URL
    settings = {temp_db.get_connection().execute(
        f'SELECT key FROM "{table}" WHERE rowid = ?', (rid,)).fetchone()['key']: new  # noqa: S608
        for (table, rid, column), (_old, new) in changed.items() if table in ('settings', 'system_settings')}
    assert settings['youtube_ytdlp_last_error'] == CLEAN
    assert settings['community_sync_last_error'] == CLEAN_KEY
    assert settings['example_last_error'] == CLEAN
    assert json.loads(settings['failover_state:llm:primary'])['reason'] == CLEAN
    assert json.loads(settings['podping_node_health'])['https://node.example.com']['last_failure_reason'] == CLEAN
    assert set(settings) == {'youtube_ytdlp_last_error', 'community_sync_last_error', 'example_last_error',
                             'failover_state:llm:primary', 'podping_node_health'}
    assert len(changed) == len(EXPECTED_TEXT) + 2 + len(settings)
    assert temp_db.get_connection().execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_a_second_run_changes_nothing(temp_db):
    _seed(temp_db)
    _upgrade(temp_db)
    first = _snapshot(temp_db)
    temp_db.get_connection().execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    temp_db.get_connection().commit()
    _upgrade(temp_db)
    assert _snapshot(temp_db) == first


def test_the_gate_stops_a_second_worker(temp_db, monkeypatch):
    _seed(temp_db)
    _upgrade(temp_db)

    def fail(*_args, **_kwargs):
        raise AssertionError('ran twice')

    monkeypatch.setattr(type(temp_db), '_advance_redaction', fail)
    assert temp_db.run_deferred_upgrade_steps()


class Killed(BaseException):
    """Stands in for a worker killed between two pages; nothing in the step may catch it."""


def _kill_after(pages):
    seen = []

    def pause():
        seen.append(1)
        if len(seen) >= pages:
            raise Killed
    return pause


def _stable(db):
    """The snapshot of the redacted columns, without the start-time stamps elsewhere."""
    columns = set(EXPECTED_TEXT) | {('episode_details', 'dai_differential_json'),
                                    ('episode_details', 'upstream_transcript_json'), ('settings', 'value')}
    return {cell: value for cell, value in _snapshot(db).items() if (cell[0], cell[2]) in columns}


def _seeded_copy(tmp_path, name):
    db = _new_isolated_database(str(tmp_path / name))
    _seed(db)
    db._init_schema()
    return db


def test_a_worker_killed_at_any_page_boundary_is_finished_by_the_next_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(deferred, 'REDACT_PAGE_ROWS', 1)
    reference_db = _seeded_copy(tmp_path, 'reference')
    pages = []
    reference_db.run_deferred_upgrade_steps(pause=lambda: pages.append(1))
    reference = _stable(reference_db)
    assert len(pages) > 3
    for kill_at in range(1, len(pages)):
        db = _seeded_copy(tmp_path, f'kill{kill_at}')
        with pytest.raises(Killed):
            db.run_deferred_upgrade_steps(clock=lambda: 1000.0, pause=_kill_after(kill_at))
        conn = db.get_connection()
        conn.rollback()
        assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
        # a fresh heartbeat keeps a second worker out, a stale one lets it resume
        assert db.run_deferred_upgrade_steps(clock=lambda: 1001.0)
        assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
        assert db.run_deferred_upgrade_steps(clock=lambda: 1000.0 + deferred.STALE_HEARTBEAT_SECONDS + 1)
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
        assert _stable(db) == reference, kill_at


def test_the_second_worker_resumes_at_the_saved_cursor(temp_db, monkeypatch):
    monkeypatch.setattr(deferred, 'REDACT_PAGE_ROWS', 1)
    _seed(temp_db)
    temp_db._init_schema()
    with pytest.raises(Killed):
        temp_db.run_deferred_upgrade_steps(clock=lambda: 5.0, pause=_kill_after(4))
    temp_db.get_connection().rollback()
    row = temp_db.get_connection().execute(
        "SELECT state, cursor, rows_done FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()
    assert row['state'] == 'running' and json.loads(row['cursor']) and row['rows_done'] > 0
    seen = []
    real = type(temp_db)._advance_redaction

    def spy(self, cursor, save, ctx):
        seen.append(dict(cursor))
        return real(self, cursor, save, ctx)

    monkeypatch.setattr(type(temp_db), '_advance_redaction', spy)
    temp_db.run_deferred_upgrade_steps(clock=lambda: 5.0 + deferred.STALE_HEARTBEAT_SECONDS + 1)
    assert seen[0] == json.loads(row['cursor'])


def test_a_step_that_fails_is_released_and_logged_once(temp_db, monkeypatch, caplog):
    _seed(temp_db)
    temp_db._init_schema()

    def boom(self, cursor, save, ctx):
        raise RuntimeError('disk full')

    monkeypatch.setattr(type(temp_db), '_advance_redaction', boom)
    with caplog.at_level(logging.WARNING):
        assert temp_db.run_deferred_upgrade_steps() is False
    failures = [r.getMessage() for r in caplog.records if 'failed with' in r.getMessage()]
    assert len(failures) == 1 and 'RuntimeError' in failures[0] and 'disk full' not in failures[0]
    conn = temp_db.get_connection()
    assert conn.execute("SELECT state FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()[0] == 'pending'
    assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_startup_only_records_the_step_as_pending(temp_db):
    _seed(temp_db)
    before = _snapshot(temp_db)
    temp_db._init_schema()
    conn = temp_db.get_connection()
    assert conn.execute("SELECT state FROM deferred_upgrade_steps WHERE name = ?", (GATE,)).fetchone()[0] == 'pending'
    assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert [c for c in _changed(before, _snapshot(temp_db)) if c[0] != 'deferred_upgrade_steps'] == []


def test_the_step_logs_once_at_start_and_once_at_finish(temp_db, caplog):
    _seed(temp_db)
    temp_db._init_schema()
    with caplog.at_level(logging.INFO):
        temp_db.run_deferred_upgrade_steps()
    lines = [r.getMessage() for r in caplog.records if GATE in r.getMessage()]
    assert len(lines) == 2 and 'started' in lines[0]
    assert re.search(r'finished: \d+ row\(s\) in \d+\.\d+s', lines[1])


def test_a_fresh_database_records_the_gate_and_changes_nothing(temp_db):
    conn = temp_db.get_connection()
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    before = _stable(temp_db)
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    _upgrade(temp_db)
    assert _stable(temp_db) == before


@pytest.mark.parametrize('text', [RAW, RAW_KEY, 'plain error', 'x?a=1&b=2', 'line\nhttps://u:p@h/x?k=v'])
def test_stored_text_redaction_matches_the_log_redaction(text):
    assert redact_stored_text(text) == redact_log_text(text)


def test_a_long_stored_value_is_not_cut():
    text = 'w ' * 40000 + RAW
    out = redact_stored_text(text)
    assert out == 'w ' * 40000 + CLEAN


def test_the_migration_redacts_the_multi_line_corpus_like_every_other_path(temp_db):
    corpus = _message_corpus()
    conn = temp_db.get_connection()
    conn.executemany("INSERT INTO pattern_cleanup_runs (status, error) VALUES ('failed', ?)", [(t,) for t in corpus])
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    _upgrade(temp_db)
    stored = [row[0] for row in conn.execute("SELECT error FROM pattern_cleanup_runs ORDER BY rowid")]
    assert stored == [redact_log_text(t) for t in corpus]
    for out in stored:
        assert not [s for s in _PLANTED if s in out] and not _CONTINUED_SECRETS.findall(out), out
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    _upgrade(temp_db)
    assert [row[0] for row in conn.execute("SELECT error FROM pattern_cleanup_runs ORDER BY rowid")] == stored


@pytest.mark.parametrize('text', ['w ' * 40000 + RAW, ('x?a=1\r\n&k=S\u2028' + RAW + '\n') * 20000])
def test_uncapped_stored_redaction_is_idempotent_on_its_own_output(text):
    once = redact_stored_text(text)
    assert 'PASS' not in once and 'SIG' not in once and '&k=S' not in once
    assert redact_stored_text(once) == once


_STORED_SHAPES = {
    'words': lambda n: 'w ' * n,
    'continued-queries': lambda n: 'https://u:p@h/x?a=1\r\n&b=2\u2028c ' * n,
    'line-run': lambda n: 'a\n' * n,
    'query-across-lines': lambda n: '/x?' + 'a\n' * n,
    'userinfo-across-lines': lambda n: '//' + 'a\n' * n,
    'hex-run': lambda n: 'a' * n,
}


@pytest.mark.parametrize('name', sorted(_STORED_SHAPES))
def test_uncapped_stored_redaction_is_linear_on_multi_megabyte_input(name):
    build = _STORED_SHAPES[name]
    n = -(-2_000_000 // (len(build(2)) - len(build(1))))
    small, large = _min_times([build(n), build(2 * n)], redact_stored_text, runs=3)
    assert len(build(2 * n)) >= 4_000_000
    assert large < 4.0, (name, small, large)
    assert large / max(small, 1e-3) < 3, (name, small, large)


def test_redact_exc_matches_redact_log_text_and_cuts():
    from utils.http import redact_exc
    exc = ValueError('failed at https://user:pw@example.com/a?token=SECRET123 ' + 'x' * 400)
    assert redact_exc(exc) == redact_log_text(str(exc))
    assert redact_exc(exc, 50) == redact_log_text(str(exc))[:50]
    assert 'SECRET123' not in redact_exc(exc) and 'pw@' not in redact_exc(exc)


def _compose_on_another_connection(db):
    """A writer that builds a new value from the old raw text, as the deferred-episode expiry does."""
    def write():
        conn = db.get_connection()
        conn.execute("UPDATE episodes SET error_message = error_message || ' | again https://u:PW2@h/x?k=SECRET2' "
                     "WHERE episode_id = 'a1b2c3d4e5f6'")
        conn.commit()
    worker = threading.Thread(target=write)
    worker.start()
    worker.join()


def test_a_row_changed_between_the_page_read_and_write_is_still_redacted(temp_db, monkeypatch):
    _seed(temp_db)
    temp_db._init_schema()
    real = type(temp_db)._redaction_units
    fired = []

    def units():
        out = []
        for table, column, rewrite, where, params in real():
            if (table, column) == ('episodes', 'error_message'):
                def hooked(value, inner=rewrite):
                    if not fired:
                        fired.append(1)
                        _compose_on_another_connection(temp_db)
                    return inner(value)
                rewrite = hooked
            out.append((table, column, rewrite, where, params))
        return out

    monkeypatch.setattr(type(temp_db), '_redaction_units', staticmethod(units))
    assert temp_db.run_deferred_upgrade_steps()
    stored = temp_db.get_connection().execute(
        "SELECT error_message FROM episodes WHERE episode_id = 'a1b2c3d4e5f6'").fetchone()[0]
    assert fired and 'again' in stored
    assert 'SECRET2' not in stored and 'PW2' not in stored and 'SIG' not in stored and 'PASS' not in stored


def test_a_step_whose_owner_lost_the_claim_cannot_mark_itself_done(temp_db, monkeypatch):
    _seed(temp_db)
    temp_db._init_schema()

    def finish_after_losing_the_claim(self, cursor, save, ctx):
        conn = self.get_connection()
        conn.execute("UPDATE deferred_upgrade_steps SET owner = 'someone-else' WHERE name = ?", (GATE,))
        conn.commit()
        return None

    monkeypatch.setattr(type(temp_db), '_advance_redaction', finish_after_losing_the_claim)
    assert temp_db.run_deferred_upgrade_steps()
    conn = temp_db.get_connection()
    assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert conn.execute("SELECT owner, state FROM deferred_upgrade_steps WHERE name = ?",
                        (GATE,)).fetchone()[:] == ('someone-else', 'running')

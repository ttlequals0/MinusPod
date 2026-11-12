"""Stored error text is redacted where it is written, once, in the accessor."""
import json
import re
import time
from pathlib import Path

import pytest

from database.error_text import (
    STORED_ERROR_COLUMNS, redact_error, redact_json_error, redact_setting_value,
)
from tests.unit.db_test_utils import _seed
from database.error_text import redact_json_fields, redaction_units
from utils.http import redact_log_text, redact_stored_text

KEY = 'a' * 64
RAW = (f'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed '
       f'feed https://example.com/feed/{KEY}.xml')
LEAKS = ('PASS', 'SIG', KEY, 'alice')
SLUG, EPISODE = 'origin-test', 'a1b2c3d4e5f6'


def _assert_clean(text):
    assert isinstance(text, str) and 'cdn.example.com' in text
    for leak in LEAKS:
        assert leak not in text


def _write_episode_error(db):
    db.upsert_episode(SLUG, EPISODE, error_message=RAW)


def _write_detection_degraded(db):
    db.upsert_episode(SLUG, EPISODE, detection_degraded=RAW)


def _write_chapters_regen_error(db):
    stamp = db.claim_chapters_regen(SLUG, EPISODE)
    db.finish_chapters_regen(SLUG, EPISODE, stamp, error=RAW)


def _write_sb_error(db):
    db.update_episode_sponsorblock(SLUG, EPISODE, sb_last_error=RAW)


def _write_history_error(db):
    podcast = db.get_podcast_by_slug(SLUG)
    db.record_processing_history(podcast['id'], SLUG, 'T', EPISODE, 'E', 'failed', error_message=RAW)


def _write_queue_error(db):
    db.upsert_episode_for_processing(SLUG, EPISODE, 'https://example.com/ep.mp3')
    claimed = db.claim_next_queued_episode()
    db.close_claimed_queue_row(claimed['id'], 'failed', RAW)


def _write_refresh_error(db):
    db.update_podcast(SLUG, last_refresh_error=RAW)


def _write_cleanup_error(db):
    run = db.create_cleanup_run(forced=False, trigger='manual')
    db.finish_cleanup_run(run, status='failed', reviewed=0, suggested=0, skipped=0, error=RAW)


def _write_cleanup_running_error(db):
    db.create_cleanup_run(forced=False, trigger='manual')
    db.fail_running_cleanup_runs(RAW)


def _write_candidate_scan_error(db):
    pid = db.get_podcast_by_slug(SLUG)['id']
    db.claim_cue_candidate_scan(pid, EPISODE, 60)
    db.save_cue_candidate_scan_error(pid, EPISODE, RAW)


def _write_threshold_scan_error(db):
    pid = db.get_podcast_by_slug(SLUG)['id']
    db.claim_cue_threshold_scan(pid, EPISODE, 60)
    db.save_cue_threshold_scan_error(pid, EPISODE, RAW)


def _write_cross_scan_error(db):
    pid = db.get_podcast_by_slug(SLUG)['id']
    db.claim_cue_cross_episode_scan(pid, 'hash1', 60)
    db.save_cue_cross_episode_scan_error(pid, 'hash1', RAW)


def _write_window_scan_error(db):
    pid = db.get_podcast_by_slug(SLUG)['id']
    template = db.create_cue_template(pid, 'intro', EPISODE, 0.0, 1.0, 16000, 13, b'x')
    db.claim_cue_window_optimize_scan(template, 60)
    db.save_cue_window_optimize_scan_error(template, RAW)


def _write_failover_reason(db):
    conn = db.get_connection()
    db.insert_failover_event(conn, 'primary', 'trigger', 'auto', RAW)
    conn.commit()


# One public write path per stored error column; a new column without an entry fails the coverage test.
WRITERS = {
    ('episodes', 'error_message'): _write_episode_error,
    ('episodes', 'chapters_regen_error'): _write_chapters_regen_error,
    ('episodes', 'sb_last_error'): _write_sb_error,
    ('processing_history', 'error_message'): _write_history_error,
    ('auto_process_queue', 'error_message'): _write_queue_error,
    ('podcasts', 'last_refresh_error'): _write_refresh_error,
    ('pattern_cleanup_runs', 'error'): _write_cleanup_error,
    ('cue_candidate_scans', 'error'): _write_candidate_scan_error,
    ('cue_threshold_scans', 'error'): _write_threshold_scan_error,
    ('cue_cross_episode_scans', 'error'): _write_cross_scan_error,
    ('cue_window_optimize_scans', 'error'): _write_window_scan_error,
    ('failover_events', 'reason'): _write_failover_reason,
    ('episodes', 'detection_degraded'): _write_detection_degraded,
}


def test_every_stored_error_column_has_a_writer():
    assert set(WRITERS) == set(STORED_ERROR_COLUMNS)


@pytest.mark.parametrize('table,column', STORED_ERROR_COLUMNS)
def test_accessor_stores_the_redacted_form(temp_db, table, column):
    _seed(temp_db)
    WRITERS[(table, column)](temp_db)
    row = temp_db.get_connection().execute(
        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL").fetchone()  # noqa: S608
    _assert_clean(row[0])


def test_cleanup_running_sweep_redacts(temp_db):
    _write_cleanup_running_error(temp_db)
    row = temp_db.get_connection().execute("SELECT error FROM pattern_cleanup_runs").fetchone()
    _assert_clean(row[0])


@pytest.mark.parametrize('key', ['db_backup_last_error', 'youtube_ytdlp_last_error'])
def test_last_error_settings_are_redacted_on_write(temp_db, key):
    temp_db.set_setting(key, RAW)
    _assert_clean(temp_db.get_setting(key))


def test_last_error_system_setting_is_redacted_on_write(temp_db):
    temp_db.set_system_setting('x_last_error', RAW)
    _assert_clean(temp_db.get_system_setting('x_last_error'))


def test_failover_state_reason_is_redacted_on_write(temp_db):
    temp_db.set_setting('failover_state:primary', json.dumps({'active': True, 'reason': RAW}))
    _assert_clean(json.loads(temp_db.get_setting('failover_state:primary'))['reason'])


def test_failover_probe_detail_is_redacted_on_write(temp_db):
    temp_db.set_setting('failover_probe:primary', json.dumps({'reachable': False, 'detail': RAW}))
    _assert_clean(json.loads(temp_db.get_setting('failover_probe:primary'))['detail'])


def test_probe_lease_and_request_keys_are_left_alone(temp_db):
    value = json.dumps({'detail': RAW})
    temp_db.set_setting('failover_probe_lease:primary', value)
    assert temp_db.get_setting('failover_probe_lease:primary') == value


def test_node_health_failure_reason_is_redacted_on_write(temp_db):
    temp_db.set_setting('podping_node_health', json.dumps({'node': {'last_failure_reason': RAW}}))
    _assert_clean(json.loads(temp_db.get_setting('podping_node_health'))['node']['last_failure_reason'])


@pytest.mark.parametrize('save,read', [
    ('save_episode_dai_differential', 'get_episode_dai_differential'),
    ('save_episode_upstream_transcript', 'get_episode_upstream_transcript'),
])
def test_differential_json_error_is_redacted_on_write(temp_db, save, read):
    _seed(temp_db)
    payload = {'status': 'error', 'error': RAW}
    getattr(temp_db, save)(SLUG, EPISODE, payload if 'upstream' in save else json.dumps(payload))
    stored = getattr(temp_db, read)(SLUG, EPISODE)
    stored = json.loads(stored) if isinstance(stored, str) else stored
    _assert_clean(stored['error'])


def test_processing_assets_upstream_transcript_error_is_redacted(temp_db):
    _seed(temp_db)
    temp_db.save_processing_assets(SLUG, EPISODE, {'upstream_transcript': {'error': RAW}})
    _assert_clean(temp_db.get_episode_upstream_transcript(SLUG, EPISODE)['error'])


def test_redaction_is_idempotent():
    for once in (redact_error(RAW), redact_log_text(RAW), redact_stored_text(RAW)):
        assert redact_error(once) == once
        assert redact_stored_text(once) == once
    assert redact_error(None) is None
    blob = json.dumps({'error': RAW})
    assert redact_json_error(redact_json_error(blob)) == redact_json_error(blob)
    value = json.dumps({'reason': RAW})
    once = redact_setting_value('failover_state:x', value)
    assert redact_setting_value('failover_state:x', once) == once


def test_redact_error_cuts_after_redacting():
    text = redact_error('x ' + 'https://example.com/p?' + 'q' * 600, 500)
    assert len(text) <= 500 and 'qqqq' not in text


# Product code outside the accessors must not write a stored error column with raw SQL.
_ACCESSOR_DIRS = ('database',)
_COLUMN_WRITE = re.compile(
    r"\b(?:" + '|'.join(sorted({c for _, c in STORED_ERROR_COLUMNS})) + r")\s*=\s*(?!NULL\b)", re.I)
_TABLE_INSERT = re.compile(
    r"(?:UPDATE|INSERT\s+(?:OR\s+\w+\s+)?INTO)\s+(?:" + '|'.join(sorted({t for t, _ in STORED_ERROR_COLUMNS}))
    + r")\b", re.I)


def test_no_raw_sql_writes_a_stored_error_column_outside_the_accessors():
    root = Path(__file__).resolve().parents[2] / 'src'
    offenders = []
    for path in root.rglob('*.py'):
        rel = path.relative_to(root)
        if rel.parts[0] in _ACCESSOR_DIRS:
            continue
        text = path.read_text()
        for match in _TABLE_INSERT.finditer(text):
            statement = text[match.start():match.start() + 600].split('"""')[0].split("',")[0]
            if _COLUMN_WRITE.search(statement) and 'error_message = \'Reset' not in statement:
                offenders.append(f'{rel}: {statement[:80]!r}')
    assert not offenders, offenders


def test_redaction_units_keep_their_persisted_order():
    units = redaction_units()
    assert [(u[0], u[1]) for u in units[-3:]] == [
        ('episodes', 'detection_degraded'), ('settings', 'value'), ('processing_history', 'processing_stats_json')]
    assert [(u[0], u[1]) for u in units[:12]] == list(STORED_ERROR_COLUMNS[:12])
    assert units[-2][4] == ('failover\\_probe:%',)


def test_stats_json_error_fields_are_redacted_on_write(temp_db):
    _seed(temp_db)
    podcast = temp_db.get_podcast_by_slug(SLUG)
    temp_db.record_processing_history(
        podcast['id'], SLUG, 'T', EPISODE, 'E', 'failed',
        processing_stats={'transcription': {'error': RAW}, 'chapters_degraded_reason': RAW})
    stored = json.loads(temp_db.get_connection().execute(
        "SELECT processing_stats_json FROM processing_history").fetchone()[0])
    _assert_clean(stored['transcription']['error'])
    _assert_clean(stored['chapters_degraded_reason'])


def test_backfill_redacts_the_copied_error(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    conn.execute("UPDATE episodes SET status = 'failed', error_message = ?", (RAW,))
    conn.commit()
    assert temp_db.backfill_processing_history() == 1
    _assert_clean(conn.execute("SELECT error_message FROM processing_history").fetchone()[0])


def test_json_fields_are_redacted_at_any_depth():
    nested = json.dumps({'error': {'msg': RAW}, 'regions': [{'error': RAW}, {'ok': 1}],
                         'deep': {'a': [{'error': RAW}]}})
    once = redact_json_fields(nested, ('error',))
    data = json.loads(once)
    _assert_clean(data['regions'][0]['error'])
    _assert_clean(data['deep']['a'][0]['error'])
    assert data['error']['msg'] == RAW
    assert redact_json_fields(once, ('error',)) == once


def test_json_object_valued_error_field_is_walked_for_nested_fields():
    value = json.dumps({'x': {'error': RAW}})
    _assert_clean(json.loads(redact_json_fields(value, ('error',)))['x']['error'])


def test_json_fields_leave_invalid_and_non_object_json_alone():
    for value in ('not json error', '[{"error": "x"}]', '"error"', '', None, 5):
        assert redact_json_fields(value, ('error',)) == value


def test_two_megabyte_blob_redacts_quickly():
    """Worst case: every region carries an error that needs redacting."""
    error = 'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed'
    blob = json.dumps({'regions': [{'error': error, 'text': 'x' * 100}] * 10200})
    assert len(blob) > 2_000_000
    start = time.perf_counter()
    redact_json_fields(blob, ('error',))
    elapsed = time.perf_counter() - start
    print(f'2 MB redact_json_fields: {elapsed * 1000:.1f} ms')
    assert elapsed < 0.05


_COPY_STATEMENT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s*\([^)]*\)\s*SELECT[^;]*?\bFROM\b", re.I | re.S)
# schema/ copies and rewrites existing rows during migrations; every other copy must redact in Python.
_COPY_ALLOWLIST_DIRS = ('src/database/schema/',)


def test_no_database_statement_copies_a_stored_error_column_outside_the_accessors():
    root = Path(__file__).resolve().parents[2]
    columns = {column for _, column in STORED_ERROR_COLUMNS}
    offenders = []
    for path in (root / 'src' / 'database').rglob('*.py'):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(_COPY_ALLOWLIST_DIRS):
            continue
        for match in _COPY_STATEMENT.finditer(path.read_text()):
            selected = match.group(0).split('SELECT', 1)[1]
            if any(re.search(rf"\b{column}\b", selected) for column in columns):
                offenders.append(f'{rel}: {match.group(0)[:80]!r}')
    assert not offenders, offenders

import json
import logging
from datetime import datetime, timedelta, timezone

import diagnostic_log
import pytest
from diagnostic_log import DiagnosticHandler, export


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 3, tzinfo=tz)


@pytest.fixture(autouse=True)
def freeze_diagnostic_clock(monkeypatch):
    monkeypatch.setattr(diagnostic_log, 'datetime', FrozenDateTime)


def test_handler_stores_metadata_without_message(tmp_path):
    handler = DiagnosticHandler(tmp_path)
    record = logging.LogRecord(
        'podcast.api', logging.ERROR, __file__, 12, 'secret %s', ('value',), None)

    handler.emit(record)

    line = (tmp_path / 'logs' / 'diagnostics' / f'diagnostic-{record.process}.jsonl').read_text()
    event = json.loads(line)
    assert event['level'] == 'ERROR'
    assert event['category'] == 'api'
    assert 'message' not in event
    assert 'secret' not in line


def test_handler_skips_llm_io(tmp_path):
    handler = DiagnosticHandler(tmp_path)
    record = logging.LogRecord('podcast.llm_io', logging.INFO, __file__, 12, 'prompt', (), None)

    handler.emit(record)

    assert not (tmp_path / 'logs' / 'diagnostics').exists()


def test_export_filters_time_and_skips_symlinks(tmp_path):
    directory = tmp_path / 'logs' / 'diagnostics'
    directory.mkdir(parents=True)
    handler = DiagnosticHandler(tmp_path)
    record = logging.LogRecord('podcast.feed', logging.INFO, __file__, 12, 'ignored', (), None)
    record.created = datetime(2026, 1, 2, 12, tzinfo=timezone.utc).timestamp()
    handler.emit(record)
    (directory / 'diagnostic-out-of-range.jsonl').write_text(json.dumps({
        'ts': '2026-01-02T10:00:00Z', 'level': 'INFO', 'category': 'feed',
        'source': 'diagnostic_log.py', 'line': 1,
    }) + '\n')
    try:
        (directory / 'diagnostic-symlink.jsonl').symlink_to(
            directory / f'diagnostic-{record.process}.jsonl')
    except OSError:
        pytest.skip('symlinks unavailable')
    start = datetime(2026, 1, 2, 11, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, 13, tzinfo=timezone.utc)

    result = export(tmp_path, start, end)

    assert len(result['events']) == 1
    assert result['events'][0]['category'] == 'feed'
    assert result['truncated'] is False


def test_export_canonicalizes_untrusted_fields(tmp_path):
    directory = tmp_path / 'logs' / 'diagnostics'
    directory.mkdir(parents=True)
    (directory / 'diagnostic-1.jsonl').write_text(json.dumps({
        'ts': '2026-01-02T12:00:00Z', 'level': 'secret',
        'category': 'secret', 'source': '/private/path', 'line': 'bad',
        'message': 'must not export',
    }) + '\n')

    with (directory / 'diagnostic-2.jsonl').open('w') as stream:
        stream.write('null\n[]\n')

    result = export(
        tmp_path,
        datetime(2026, 1, 2, 11, tzinfo=timezone.utc),
        datetime(2026, 1, 2, 13, tzinfo=timezone.utc),
    )

    assert result['events'] == [{
        'ts': '2026-01-02T12:00:00.000Z', 'level': 'OTHER',
        'category': 'external', 'source': 'external', 'line': 0, 'version': 'unknown',
    }]
    assert result['coverage']['firstEvent'] == '2026-01-02T12:00:00.000Z'
    assert result['coverage']['lastEvent'] == '2026-01-02T12:00:00.000Z'
    assert result['coverage']['version']


def test_export_keeps_newest_events_when_record_cap_is_reached(tmp_path):
    directory = tmp_path / 'logs' / 'diagnostics'
    directory.mkdir(parents=True)
    path = directory / 'diagnostic-1.jsonl'
    with path.open('w') as stream:
        for index in range(10_005):
            stream.write(json.dumps({
                'ts': (datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
                       + timedelta(seconds=index)).isoformat().replace('+00:00', 'Z'),
                'level': 'INFO', 'category': 'api',
                'source': 'diagnostic_log.py', 'line': 1,
            }) + '\n')

    result = export(
        tmp_path,
        datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        datetime(2026, 1, 2, 15, tzinfo=timezone.utc),
    )

    assert result['truncated'] is True
    assert len(result['events']) == 10_000
    assert result['events'][0]['ts'] == '2026-01-02T12:00:05.000Z'
    assert result['coverage']['firstEvent'] == result['events'][0]['ts']
    assert result['coverage']['lastEvent'] == result['events'][-1]['ts']


def test_handler_recovers_after_prune_race(tmp_path, monkeypatch):
    handler = DiagnosticHandler(tmp_path)
    record = logging.LogRecord('podcast.api', logging.INFO, __file__, 12, 'ignored', (), None)
    handler._last_prune = -100

    calls = 0
    def race():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FileNotFoundError('rotated by another worker')

    monkeypatch.setattr(handler, '_prune', race)
    handler.emit(record)
    monkeypatch.undo()
    handler.emit(record)

    assert calls == 1
    path = tmp_path / 'logs' / 'diagnostics' / f'diagnostic-{record.process}.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(events) == 2


def test_export_excludes_events_older_than_retention_window(tmp_path):
    directory = tmp_path / 'logs' / 'diagnostics'
    directory.mkdir(parents=True)
    path = directory / 'diagnostic-retention.jsonl'
    path.write_text('\n'.join(json.dumps({
        'ts': timestamp, 'level': 'INFO', 'category': 'api',
        'source': 'diagnostic_log.py', 'line': 1,
    }) for timestamp in ('2026-01-02T12:00:00Z', '2025-12-20T12:00:00Z')) + '\n')

    result = export(
        tmp_path,
        datetime(2025, 12, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    assert [event['ts'] for event in result['events']] == ['2026-01-02T12:00:00.000Z']


def test_rotation_keeps_newest_records_and_continues_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_log, 'MAX_FILE_BYTES', 180)
    monkeypatch.setattr(diagnostic_log, 'MAX_FILES', 2)
    handler = DiagnosticHandler(tmp_path)
    for index in range(4):
        record = logging.LogRecord('podcast.api', logging.INFO, __file__, 12, 'ignored', (), None)
        record.created = (datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
                          + timedelta(seconds=index)).timestamp()
        handler.emit(record)

    paths = sorted((tmp_path / 'logs' / 'diagnostics').glob('*.jsonl'))
    events = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
    assert len(events) == 2
    assert max(event['ts'] for event in events) == '2026-01-02T12:00:03.000Z'


def test_install_skips_unavailable_diagnostic_directory(tmp_path, monkeypatch):
    previous = diagnostic_log._handler
    diagnostic_log._handler = None
    def unavailable(_):
        raise OSError('diagnostic path unavailable')

    monkeypatch.setattr(
        diagnostic_log,
        '_diagnostic_directory',
        unavailable,
    )
    try:
        assert diagnostic_log.install(tmp_path) is None
        assert diagnostic_log._handler is None
    finally:
        diagnostic_log._handler = previous


def test_export_skips_invalid_utf8_file(tmp_path):
    directory = tmp_path / 'logs' / 'diagnostics'
    directory.mkdir(parents=True)
    (directory / 'diagnostic-invalid.jsonl').write_bytes(b'\xff\xfe\n')

    result = export(
        tmp_path,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    assert result['events'] == []
    assert result['coverage']['firstEvent'] is None

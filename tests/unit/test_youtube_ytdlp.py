import os
import stat
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from utils.time import utc_now_iso
from youtube import ytdlp


@pytest.fixture(autouse=True)
def _reset_module_state():
    ytdlp._baked_version = None
    ytdlp._booted = False
    ytdlp._fallback_for = None
    yield
    ytdlp._baked_version = None
    ytdlp._booted = False
    ytdlp._fallback_for = None


class FakeStorage:
    def __init__(self, tmp_path):
        self.data_dir = tmp_path


def _fake_binary(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo {version}; exit 0; fi\necho ok\n')
    path.chmod(0o755)


def test_ensure_copies_newer_baked_binary(tmp_path):
    baked = tmp_path / 'baked' / 'yt-dlp'
    _fake_binary(baked, '2026.10.26')
    storage = FakeStorage(tmp_path / 'data')
    db = MagicMock()
    with patch.object(ytdlp, 'BAKED_PATH', str(baked)):
        path = ytdlp.ensure_runtime_binary(storage, db)
    assert path == ytdlp.runtime_path(storage)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o755
    assert ytdlp.binary_version(path) == '2026.10.26'


def test_ensure_never_downgrades_self_updated_copy(tmp_path):
    baked = tmp_path / 'baked' / 'yt-dlp'
    _fake_binary(baked, '2026.10.01')
    storage = FakeStorage(tmp_path / 'data')
    _fake_binary(ytdlp.runtime_path(storage), '2026.10.26')
    with patch.object(ytdlp, 'BAKED_PATH', str(baked)):
        ytdlp.ensure_runtime_binary(storage, MagicMock())
    assert ytdlp.binary_version(ytdlp.runtime_path(storage)) == '2026.10.26'


def test_ensure_falls_back_to_baked_when_runtime_cannot_execute(tmp_path):
    baked = tmp_path / 'baked' / 'yt-dlp'
    _fake_binary(baked, '2026.10.26')
    storage = FakeStorage(tmp_path / 'data')
    runtime = ytdlp.runtime_path(storage)
    _fake_binary(runtime, '2026.10.26')
    db = MagicMock()
    with patch.object(ytdlp, 'BAKED_PATH', str(baked)), \
            patch.object(ytdlp, 'binary_version', side_effect=lambda p: None if str(p) == str(runtime) else '2026.10.26'):
        path = ytdlp.ensure_runtime_binary(storage, db)
    assert str(path) == str(baked)
    db.set_setting.assert_any_call('youtube_ytdlp_last_error', ytdlp.RUNTIME_NOT_EXECUTABLE, is_default=False)


def test_trimmed_error_and_needs_update():
    err = 'WARNING: x\nERROR: [youtube] abc: Sign in to confirm you\'re not a bot. Use --cookies\n'
    assert ytdlp.trimmed_error(err).startswith('ERROR: [youtube] abc: Sign in')
    assert ytdlp.needs_update(err) is False
    assert ytdlp.needs_update('ERROR: Unable to extract uploader id; please report this issue') is True


def test_update_tick_respects_gates(tmp_path):
    db = MagicMock()
    db.get_setting_bool.side_effect = lambda k, d=False: {'youtube_enabled': False}.get(k, d)
    with patch.object(ytdlp, 'update_binary') as upd:
        ytdlp.update_tick(db)
    upd.assert_not_called()


def test_update_tick_spawns_nothing_when_throttle_not_due(tmp_path):
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_setting.return_value = utc_now_iso()
    ytdlp._booted = True
    stub = MagicMock(storage=FakeStorage(tmp_path))
    with patch.dict('sys.modules', {'main_app': stub}), \
            patch.object(ytdlp.subprocess, 'run') as spawn, \
            patch.object(ytdlp, 'ensure_runtime_binary') as ensure:
        ytdlp.update_tick(db)
    spawn.assert_not_called()
    ensure.assert_not_called()


def test_status_spawns_at_most_once_for_the_baked_version(tmp_path):
    db = MagicMock()
    db.get_setting.return_value = '2026.10.26'
    with patch.object(ytdlp, 'binary_version', return_value='2026.10.20') as probe:
        first = ytdlp.status(db)
        second = ytdlp.status(db)
    assert first == second == {'bakedVersion': '2026.10.20', 'version': '2026.10.26'}
    assert probe.call_count == 1


def test_update_binary_records_version(tmp_path):
    storage = FakeStorage(tmp_path / 'data')
    _fake_binary(ytdlp.runtime_path(storage), '2026.10.26')
    db = MagicMock()
    done = subprocess.CompletedProcess(['yt-dlp', '-U'], 0, stdout='Updated yt-dlp to stable@2026.10.27\n', stderr='')
    with patch.object(ytdlp, 'run', return_value=done), \
            patch.object(ytdlp, 'binary_version', return_value='2026.10.27'):
        result = ytdlp.update_binary(storage, db)
    assert result == {'updated': True, 'version': '2026.10.27', 'error': None}
    db.set_setting.assert_any_call('youtube_ytdlp_version', '2026.10.27', is_default=False)


def test_ensure_leaves_no_temp_files(tmp_path):
    baked = tmp_path / 'baked' / 'yt-dlp'
    _fake_binary(baked, '2026.10.26')
    storage = FakeStorage(tmp_path / 'data')
    with patch.object(ytdlp, 'BAKED_PATH', str(baked)):
        ytdlp.ensure_runtime_binary(storage, MagicMock())
    assert [p.name for p in ytdlp.runtime_dir(storage).iterdir()] == ['yt-dlp']


def test_update_tick_swallows_errors_and_records(tmp_path):
    db = MagicMock()
    db.get_setting_bool.return_value = True
    stub = MagicMock(storage=FakeStorage(tmp_path))
    with patch.dict('sys.modules', {'main_app': stub}), \
            patch.object(ytdlp, 'ensure_runtime_binary', side_effect=PermissionError('denied')):
        ytdlp.update_tick(db)
    db.set_setting.assert_any_call('youtube_ytdlp_last_error', 'denied', is_default=False)


def test_baked_fallback_is_decided_once_per_process(tmp_path):
    baked = tmp_path / 'baked' / 'yt-dlp'
    _fake_binary(baked, '2026.10.26')
    storage = FakeStorage(tmp_path / 'data')
    runtime = ytdlp.runtime_path(storage)
    _fake_binary(runtime, '2026.10.26')
    db = MagicMock()
    with patch.object(ytdlp, 'BAKED_PATH', str(baked)), \
            patch.object(ytdlp, 'binary_version', side_effect=lambda p: None if str(p) == str(runtime) else '2026.10.26') as probe:
        ytdlp.ensure_runtime_binary(storage, db)
        probes, writes = probe.call_count, db.set_setting.call_count
        assert str(ytdlp.ensure_runtime_binary(storage, db)) == str(baked)
    assert probe.call_count == probes and db.set_setting.call_count == writes


def test_update_reports_a_failed_version_probe(tmp_path):
    storage = FakeStorage(tmp_path / 'data')
    _fake_binary(ytdlp.runtime_path(storage), '2026.10.26')
    db = MagicMock()
    done = subprocess.CompletedProcess(['yt-dlp', '-U'], 0, stdout='ok\n', stderr='')
    with patch.object(ytdlp, 'run', return_value=done), \
            patch.object(ytdlp, 'binary_version', side_effect=['2026.10.26', None]):
        result = ytdlp.update_binary(storage, db, ytdlp.runtime_path(storage))
    assert result['updated'] is False and result['error'] == 'version probe failed after update'


def test_second_concurrent_update_returns_without_running(tmp_path):
    db = MagicMock()
    db.get_setting.return_value = '2026.10.26'
    with patch.object(ytdlp, 'run') as run:
        assert ytdlp._update_lock.acquire(blocking=False)
        try:
            result = ytdlp.update_binary(FakeStorage(tmp_path), db)
        finally:
            ytdlp._update_lock.release()
    assert result == {'updated': False, 'version': '2026.10.26', 'error': 'update already running'}
    run.assert_not_called()

import os
import stat
import subprocess
from unittest.mock import MagicMock, patch

from youtube import ytdlp


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

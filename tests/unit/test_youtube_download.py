import json
import os
import subprocess
from unittest.mock import patch

import pytest

from youtube import download


def _fake_run(tmp_dir_holder, stderr='', code=0, captions=True, chapters=None):
    def run(args, timeout, cwd=None, **kw):
        tmp_dir_holder.append(cwd)
        if code:
            return subprocess.CompletedProcess(args, code, stdout='', stderr=stderr)
        vid = args[-1].split('v=')[1]
        open(os.path.join(cwd, f'{vid}.mp3'), 'wb').write(b'ID3')
        info = {'id': vid, 'title': 't', 'duration': 100, 'chapters': chapters or []}
        json.dump(info, open(os.path.join(cwd, f'{vid}.info.json'), 'w'))
        if captions:
            json.dump({'events': []}, open(os.path.join(cwd, f'{vid}.en.json3'), 'w'))
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    return run


def test_download_returns_paths_and_passes_flags(tmp_path):
    dirs = []
    with patch.object(download.ytdlp, 'run', side_effect=_fake_run(dirs, chapters=[{'title': 'Intro', 'start_time': 0, 'end_time': 10}])) as run, \
            patch.object(download, '_impersonate_target', return_value='chrome'), \
            patch.object(download, 'cookies_path', return_value=None), \
            patch.object(download, 'resolve_bitrate', return_value='128k'):
        result = download.download('vid000000001', want_captions=True, language='en')
    args = run.call_args.args[0]
    assert result.audio_path.endswith('vid000000001.mp3') and os.path.exists(result.audio_path)
    assert result.captions_path.endswith('vid000000001.en.json3')
    assert result.chapters == [{'title': 'Intro', 'start': 0.0, 'end': 10.0}]
    for flag in ('-x', '--audio-format', 'mp3', '--audio-quality', '128k', '--no-playlist', '--write-info-json',
                 '--embed-chapters', '--write-subs', '--write-auto-subs', '--sub-format', 'json3',
                 '--impersonate', 'chrome'):
        assert flag in args
    assert '--cookies' not in args
    assert args[args.index('--sub-langs') + 1] == 'en,en-orig,-live_chat'


def test_download_without_captions_or_impersonation(tmp_path):
    dirs = []
    with patch.object(download.ytdlp, 'run', side_effect=_fake_run(dirs, captions=False)) as run, \
            patch.object(download, '_impersonate_target', return_value=None), \
            patch.object(download, 'cookies_path', return_value='/data/youtube/cookies.txt'), \
            patch.object(download, 'resolve_bitrate', return_value='96k'):
        result = download.download('vid000000001', want_captions=False, language='en')
    args = run.call_args.args[0]
    assert result.captions_path is None
    assert '--write-subs' not in args and '--impersonate' not in args
    assert args[args.index('--cookies') + 1] == '/data/youtube/cookies.txt'


def test_download_error_carries_trimmed_line_and_update_hint():
    bot = "ERROR: [youtube] vid000000001: Sign in to confirm you're not a bot. Use --cookies"
    with patch.object(download.ytdlp, 'run', side_effect=_fake_run([], stderr=bot, code=1)), \
            patch.object(download, '_impersonate_target', return_value=None), \
            patch.object(download, 'cookies_path', return_value=None), \
            patch.object(download, 'resolve_bitrate', return_value='128k'):
        with pytest.raises(download.DownloadError) as exc:
            download.download('vid000000001', want_captions=False, language='en')
    assert str(exc.value).startswith('ERROR: [youtube] vid000000001: Sign in')
    assert exc.value.needs_update is False
    stale = 'ERROR: [youtube] vid000000001: Unable to extract player version; please report this issue'
    with patch.object(download.ytdlp, 'run', side_effect=_fake_run([], stderr=stale, code=1)), \
            patch.object(download, '_impersonate_target', return_value=None), \
            patch.object(download, 'cookies_path', return_value=None), \
            patch.object(download, 'resolve_bitrate', return_value='128k'):
        with pytest.raises(download.DownloadError) as exc:
            download.download('vid000000001', want_captions=False, language='en')
    assert exc.value.needs_update is True

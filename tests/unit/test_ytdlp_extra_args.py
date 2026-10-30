import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from youtube import ytdlp  # noqa: E402


def test_empty_is_no_args():
    assert ytdlp.parse_extra_args('') == []
    assert ytdlp.parse_extra_args('   ') == []


def test_normal_and_quoted_values():
    assert ytdlp.parse_extra_args('--proxy socks5://p:1080 --limit-rate 2M') == [
        '--proxy', 'socks5://p:1080', '--limit-rate', '2M']
    assert ytdlp.parse_extra_args('--user-agent "Mozilla 5.0 test"') == ['--user-agent', 'Mozilla 5.0 test']


@pytest.mark.parametrize('text,flag', [
    ('--exec "rm x"', '--exec'),
    ('--proxy x -o foo', '-o'),
    ('--output=%(id)s', '--output'),
    ('--print=id', '--print'),
])
def test_denied_flags(text, flag):
    with pytest.raises(ValueError, match=f'{flag} is not allowed'):
        ytdlp.parse_extra_args(text)


def test_unbalanced_quotes():
    with pytest.raises(ValueError, match='unbalanced quotes'):
        ytdlp.parse_extra_args('--user-agent "oops')


def test_too_long():
    with pytest.raises(ValueError, match='at most 1000 characters'):
        ytdlp.parse_extra_args('--x ' + 'a' * 1000)


def _run(stored, **kwargs):
    db = MagicMock()
    db.get_setting.return_value = stored
    with patch.object(ytdlp, 'ensure_runtime_binary', return_value=Path('/bin/yt-dlp')), \
            patch.object(ytdlp, '_ensured_path', None), \
            patch.object(ytdlp.subprocess, 'run') as run:
        ytdlp.run(['--dump-single-json', 'u'], timeout=5, storage=MagicMock(), db=db, **kwargs)
    return run.call_args[0][0]


def test_run_places_extras_before_caller_args():
    assert _run('--proxy p') == ['/bin/yt-dlp', '--proxy', 'p', '--dump-single-json', 'u']


def test_run_extra_false_skips_them():
    assert _run('--proxy p', extra=False) == ['/bin/yt-dlp', '--dump-single-json', 'u']


def test_run_ignores_bad_stored_value():
    assert _run('--exec x') == ['/bin/yt-dlp', '--dump-single-json', 'u']

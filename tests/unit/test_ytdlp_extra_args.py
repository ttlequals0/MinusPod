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
    assert ytdlp.parse_extra_args('--sleep-requests 1 --limit-rate 2M') == [
        '--sleep-requests', '1', '--limit-rate', '2M']
    assert ytdlp.parse_extra_args('--user-agent "Mozilla 5.0 test"') == ['--user-agent', 'Mozilla 5.0 test']


@pytest.mark.parametrize('text,flag', [
    ('--exec "rm x"', '--exec'),
    ('--proxy x -o foo', '-o'),
    ('--output=%(id)s', '--output'),
    ('--print=id', '--print'),
    ('--update-to x@y', '--update-to'),
    ('--ffmpeg-location /tmp/x', '--ffmpeg-location'),
    ('-U', '-U'),
    ('--exe x', '--exe'),
    ('--ffmpeg-loc /tmp/x', '--ffmpeg-loc'),
    ('--update-t x', '--update-t'),
    ('--p x', '--p'),
    ('--', '--'),
    ('--username x', '--username'),
    ('-p x', '-p'),
    ('--video-password=x', '--video-password'),
])
def test_denied_flags(text, flag):
    with pytest.raises(ValueError, match=f'{flag} is not allowed'):
        ytdlp.parse_extra_args(text)


def test_allowed_options_still_pass():
    assert ytdlp.parse_extra_args('--proxy p --limit-rate 2M --extractor-args youtube:player_client=web') == [
        '--proxy', 'p', '--limit-rate', '2M', '--extractor-args', 'youtube:player_client=web']


def test_unbalanced_quotes():
    with pytest.raises(ValueError, match='unbalanced quotes'):
        ytdlp.parse_extra_args('--user-agent "oops')


def test_too_long():
    with pytest.raises(ValueError, match='at most 1000 characters'):
        ytdlp.parse_extra_args('--x ' + 'a' * 1000)


def _run(stored, url='', **kwargs):
    db = MagicMock()
    db.get_setting.side_effect = lambda k: {'youtube_ytdlp_extra_args': stored, 'youtube_proxy_url': url}.get(k)
    with patch.object(ytdlp, 'ensure_runtime_binary', return_value=Path('/bin/yt-dlp')), \
            patch.object(ytdlp, '_ensured_path', None), \
            patch.object(ytdlp.subprocess, 'run') as run:
        ytdlp.run(['--dump-single-json', 'u'], timeout=5, storage=MagicMock(), db=db, **kwargs)
    return run.call_args[0][0]


def test_run_places_extras_before_caller_args():
    assert _run('--proxy p') == ['/bin/yt-dlp', '--proxy', 'p', '--dump-single-json', 'u']


def test_run_extra_false_skips_them():
    assert _run('--limit-rate 2M', extra_args=False) == ['/bin/yt-dlp', '--dump-single-json', 'u']


def test_run_ignores_bad_stored_value():
    assert _run('--exec x') == ['/bin/yt-dlp', '--dump-single-json', 'u']


@pytest.mark.parametrize('text', ['-ofile', '-xo file'])
def test_bundled_short_options_rejected(text):
    with pytest.raises(ValueError, match='use the long form'):
        ytdlp.parse_extra_args(text)


def test_update_ignores_extra_args():
    db = MagicMock()
    with patch.object(ytdlp, 'run', return_value=MagicMock(returncode=1, stderr='x', stdout='')) as run:
        ytdlp.update_binary(MagicMock(), db, target=Path('/data/yt-dlp'))
    assert run.call_args.args[0] == ['-U']
    assert run.call_args.kwargs['extra_args'] is False


def test_run_places_proxy_before_extras():
    cmd = _run('--limit-rate 2M', url='socks5h://proxy:1080')
    assert cmd == ['/bin/yt-dlp', '--proxy', 'socks5h://proxy:1080', '--limit-rate', '2M', '--dump-single-json', 'u']


def test_run_extra_false_skips_proxy():
    assert _run('', url='http://p:1', proxy=False) == ['/bin/yt-dlp', '--dump-single-json', 'u']


def test_run_ignores_invalid_stored_proxy():
    assert _run('', url='ftp://p') == ['/bin/yt-dlp', '--dump-single-json', 'u']


@pytest.mark.parametrize('text', ['http://p:8080', 'https://u:pw@p', 'socks4://p:1', 'socks5://p:1080',
                                  'SOCKS5H://p:1080'])
def test_proxy_valid(text):
    assert ytdlp.parse_proxy_url(f'  {text} ') == text


def test_proxy_empty():
    assert ytdlp.parse_proxy_url('  ') == ''


@pytest.mark.parametrize('text', ['ftp://p:1', 'p:1080', 'socks5://', 'http://p:abc', 'http://a b', 'http:///x'])
def test_proxy_invalid(text):
    with pytest.raises(ValueError, match='must start with'):
        ytdlp.parse_proxy_url(text)


def test_proxy_too_long_and_no_credentials_in_error():
    with pytest.raises(ValueError, match='at most 500'):
        ytdlp.parse_proxy_url('http://u:SECRET@h/' + 'a' * 500)
    with pytest.raises(ValueError) as err:
        ytdlp.parse_proxy_url('ftp://u:SECRET@h')
    assert 'SECRET' not in str(err.value)


@pytest.mark.parametrize('text,token', [
    ('https://example.com/x --limit-rate 2M', 'https://example.com/x'),
    ('--limit-rate 2M 3M', '3M'),
    ('--limit-rate=2M 3M', '3M'),
    ('--proxy socks5://p:1', 'socks5://p:1'),
    ('--x=http://p', '--x=http://p'),
])
def test_unexpected_bare_values_rejected(text, token):
    with pytest.raises(ValueError, match=f'unexpected value {token}'):
        ytdlp.parse_extra_args(text)


def test_extractor_args_then_option_accepted():
    assert ytdlp.parse_extra_args('--extractor-args youtube:player_client=web --limit-rate 2M') == [
        '--extractor-args', 'youtube:player_client=web', '--limit-rate', '2M']


def test_scrub_proxy_masks_url_and_password():
    url = 'socks5h://u:S%40CRET@host:1080'
    text = f'ERROR: proxy {url} failed; auth S%40CRET and S@CRET rejected'
    out = ytdlp.scrub_proxy(text, url)
    assert 'CRET' not in out and 'u:***@host:1080' in out
    assert ytdlp.scrub_proxy('plain', 'http://h:1') == 'plain'


def test_run_scrubs_proxy_credentials_from_output():
    db = MagicMock()
    db.get_setting.side_effect = lambda k: {'youtube_proxy_url': 'http://u:SECRET@p:1'}.get(k)
    done = MagicMock(stdout='out http://u:SECRET@p:1', stderr='ERROR: via http://u:SECRET@p:1 SECRET')
    with patch.object(ytdlp, 'ensure_runtime_binary', return_value=Path('/bin/yt-dlp')), \
            patch.object(ytdlp, '_ensured_path', None), patch.object(ytdlp.subprocess, 'run', return_value=done):
        result = ytdlp.run(['x'], timeout=5, storage=MagicMock(), db=db)
    assert 'SECRET' not in result.stdout and 'SECRET' not in result.stderr
    assert 'u:***@p:1' in result.stderr


def test_restore_proxy_password():
    stored = 'http://u:real@h:1'
    assert ytdlp.restore_proxy_password('http://u:***@h:2', stored) == 'http://u:real@h:2'
    assert ytdlp.restore_proxy_password('http://u:new@h:2', stored) == 'http://u:new@h:2'
    assert ytdlp.restore_proxy_password('http://u:***@h:2', 'http://h:1') == 'http://u:***@h:2'
    assert ytdlp.restore_proxy_password('http://u:***@H:1', stored) == 'http://u:real@H:1'
    for bad in ('http://u:***@other:1', 'http://x:***@h:1', 'socks5://u:***@h:1'):
        with pytest.raises(ValueError, match='host or user changed'):
            ytdlp.restore_proxy_password(bad, stored)

"""Unit tests for differential_fetcher UA pool and DAI-likelihood heuristic (Layer 3)."""
import os
import sys
from unittest.mock import patch

import requests
import requests.exceptions

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import BROWSER_USER_AGENT
from differential_fetcher import (
    REFETCH_USER_AGENTS,
    fetch_and_diff,
    is_likely_dai_feed,
    pick_refetch_user_agent,
)


def test_pool_has_five_distinct_client_strings():
    assert len(REFETCH_USER_AGENTS) == 5
    assert len(set(REFETCH_USER_AGENTS)) == 5
    joined = ' '.join(REFETCH_USER_AGENTS)
    for client in ('Podcasts/', 'Overcast', 'PocketCasts', 'AntennaPod', 'Castro'):
        assert client in joined


def test_pick_excludes_first_ua_and_returns_pool_member():
    for first in (None, '', REFETCH_USER_AGENTS[0], 'SomeOtherBot/1.0',
                  BROWSER_USER_AGENT):
        for _ in range(100):
            picked = pick_refetch_user_agent(first)
            assert picked in REFETCH_USER_AGENTS
            assert picked != first


def test_pick_rotates_across_calls_with_same_first_ua():
    picks = {pick_refetch_user_agent(BROWSER_USER_AGENT) for _ in range(100)}
    assert len(picks) >= 2


def test_dai_domain_in_direct_host():
    assert is_likely_dai_feed(['https://traffic.megaphone.fm/GLT1234.mp3']) is True


def test_dai_domain_in_prefix_chain_path():
    url = ('https://pdst.fm/e/chrt.fm/track/12345/'
           'traffic.megaphone.fm/EP99.mp3')
    assert is_likely_dai_feed([url]) is True


def test_plain_cdn_is_not_dai():
    assert is_likely_dai_feed(['https://cdn.example.com/ep1.mp3']) is False


def test_empty_and_none_inputs():
    assert is_likely_dai_feed([]) is False
    assert is_likely_dai_feed(None) is False
    assert is_likely_dai_feed([None, '']) is False


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield from self._chunks

    def close(self):
        self.closed = True


def _run_file(tmp_path, size=1000):
    path = tmp_path / 'run.mp3'
    path.write_bytes(b'\x00' * size)
    return str(path)


def test_fetch_and_diff_uses_rotated_ua(tmp_path):
    run_file = _run_file(tmp_path)
    aligned = {'status': 'ok', 'regions': [
        {'start_s': 1.0, 'end_s': 5.0, 'kind': 'differential', 'corr': 0.0}]}
    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])) as mock_get, \
         patch('differential_fetcher.get_audio_duration', return_value=42.0), \
         patch('differential_fetcher.align_and_diff',
               return_value=aligned) as mock_align:
        result = fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                run_file, str(tmp_path))

    ua = mock_get.call_args.kwargs['headers']['User-Agent']
    assert ua in REFETCH_USER_AGENTS
    assert ua != BROWSER_USER_AGENT
    assert result['status'] == 'ok'
    assert result['regions'] == aligned['regions']
    assert result['error'] is None
    assert result['refetch_meta']['ua'] == ua
    assert result['refetch_meta']['size'] == 100
    assert result['refetch_meta']['duration'] == 42.0
    run_arg, _, work_arg = mock_align.call_args.args
    assert run_arg == run_file
    assert work_arg == str(tmp_path)


def test_fetch_and_diff_enforces_1_5x_size_cap(tmp_path):
    run_file = _run_file(tmp_path, size=1000)
    # 1600 bytes streamed exceeds the 1500-byte cap (1.5x the 1000-byte run file).
    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 800, b'x' * 800])):
        result = fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                run_file, str(tmp_path))
    assert result['status'] == 'error'
    assert result['regions'] == []
    assert result['error'] is not None
    assert not os.path.exists(os.path.join(str(tmp_path), 'refetch_audio'))


def test_fetch_and_diff_network_error_is_nonfatal(tmp_path):
    run_file = _run_file(tmp_path)
    with patch('differential_fetcher.safe_get',
               side_effect=requests.exceptions.ConnectionError('boom')):
        result = fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                run_file, str(tmp_path))
    assert result['status'] == 'error'
    assert 'boom' in result['error']


def test_fetch_and_diff_cleans_up_refetch_file(tmp_path):
    run_file = _run_file(tmp_path)
    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=1.0), \
         patch('differential_fetcher.align_and_diff',
               return_value={'status': 'no_differential', 'regions': []}):
        fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                       run_file, str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), 'refetch_audio'))


def test_fetch_and_diff_runtime_error_is_nonfatal(tmp_path):
    # numpy FFT/linalg internals raise RuntimeError; the never-raises boundary
    # must degrade to 'error' and still clean up the temp refetch file.
    run_file = _run_file(tmp_path)
    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=1.0), \
         patch('differential_fetcher.align_and_diff',
               side_effect=RuntimeError('SVD did not converge')):
        result = fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                run_file, str(tmp_path))
    assert result['status'] == 'error'
    assert 'SVD did not converge' in result['error']
    assert not os.path.exists(os.path.join(str(tmp_path), 'refetch_audio'))

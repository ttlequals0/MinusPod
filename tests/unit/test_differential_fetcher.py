"""Unit tests for differential_fetcher UA pool, DAI-likelihood heuristic,
and the dense per-block probing engine (Layer 3)."""
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import requests
import requests.exceptions

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import differential_fetcher as df
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


def test_decode_pcm_uses_tracked_run(tmp_path):
    """Finding 7: _decode_pcm must use tracked_run so worker SIGTERM propagates
    to the in-flight ffmpeg child via terminate_all(), preventing orphaned procs."""
    import differential_fetcher as df

    # Before the fix, tracked_run is not imported in the module.
    assert hasattr(df, 'tracked_run'), (
        "_decode_pcm must import tracked_run from utils.subprocess_registry"
    )

    def _fake_tracked_run(cmd, **kw):
        # Write one s16le zero-sample so np.fromfile succeeds.
        pcm_path = cmd[-1]
        with open(pcm_path, 'wb') as fh:
            fh.write(b'\x00\x00')
        return MagicMock(returncode=0)

    with patch.object(df, 'tracked_run', side_effect=_fake_tracked_run) as mock_tr:
        data = df._decode_pcm(str(tmp_path / 'audio.mp3'), str(tmp_path), 'test')

    mock_tr.assert_called_once()
    cmd = mock_tr.call_args.args[0]
    assert cmd[0] == 'ffmpeg'
    assert 'pcm_s16le' in cmd
    assert isinstance(data, np.ndarray)


# --- Dense per-block probing (2.76.0) ---------------------------------------
#
# Array-level tests against _align_and_diff_pcm: deterministic numpy PCM
# fixtures (seeded noisy sine bursts separated by digital silence), no ffmpeg.

RATE = 8000  # must equal df.PCM_RATE; asserted in test_rate_matches_module


def test_rate_matches_module():
    assert df.PCM_RATE == RATE


def _burst(seconds, seed, freq=None):
    """Deterministic non-periodic burst: sine carrier + seeded noise.

    The noise term breaks the sine's periodicity so NCC peaks only at the
    true alignment. freq=None means pure seeded noise (used for the DAI
    replacement fill so it shares no carrier with the original block).
    """
    n = int(seconds * RATE)
    rng = np.random.default_rng(seed)
    sig = 0.4 * rng.standard_normal(n)
    if freq is not None:
        t = np.arange(n) / RATE
        sig = sig * 0.5 + 0.4 * np.sin(2 * np.pi * freq * t)
    return sig.astype(np.float32)


def _assemble(parts):
    """Concatenate bursts and ('sil', seconds) gaps; return (pcm, marks).

    marks mirrors _silence_marks: file edges + silence midpoints.
    """
    pcm = []
    marks = [0.0]
    t = 0.0
    for part in parts:
        if isinstance(part, tuple) and part[0] == 'sil':
            dur = part[1]
            pcm.append(np.zeros(int(dur * RATE), dtype=np.float32))
            marks.append(t + dur / 2.0)
            t += dur
        else:
            pcm.append(part)
            t += len(part) / RATE
    marks.append(t)
    return np.concatenate(pcm), marks


def _blocks(seeds_freqs):
    return {name: _burst(sec, seed, freq)
            for name, sec, seed, freq in seeds_freqs}


def test_every_block_probed_and_replaced_block_measured_differential():
    # Four 8s blocks; refetch replaces C with pure noise of the same length.
    # EVERY region must carry a numeric measured corr (no hard-coded 0.0);
    # the replaced block scores below 0.5 and kind 'differential'; the
    # untouched blocks score identical.
    seg = _blocks([('a', 8, 1, 220.0), ('b', 8, 2, 330.0),
                   ('c', 8, 3, 440.0), ('d', 8, 4, 550.0),
                   ('c_repl', 8, 99, None)])
    run_pcm, run_marks = _assemble(
        [seg['a'], ('sil', 0.4), seg['b'], ('sil', 0.4),
         seg['c'], ('sil', 0.4), seg['d']])
    ref_pcm, ref_marks = _assemble(
        [seg['a'], ('sil', 0.4), seg['b'], ('sil', 0.4),
         seg['c_repl'], ('sil', 0.4), seg['d']])

    result = df._align_and_diff_pcm(run_pcm, ref_pcm, run_marks, ref_marks)

    assert result['status'] == 'ok'
    regions = result['regions']
    assert regions
    # Every emitted region is measured: numeric corr, no 'unknown'.
    for r in regions:
        assert r['kind'] in ('identical', 'differential')
        assert isinstance(r['corr'], float)
    diffs = [r for r in regions if r['kind'] == 'differential']
    assert len(diffs) == 1
    # Replaced block spans silence midpoints 16.6s-25.0s.
    assert abs(diffs[0]['start_s'] - 16.6) <= 0.5
    assert abs(diffs[0]['end_s'] - 25.0) <= 0.5
    assert diffs[0]['corr'] < 0.5
    identical = [r for r in regions if r['kind'] == 'identical']
    assert identical
    assert all(r['corr'] >= df.XCORR_MIN_CORR for r in identical)


def test_shifted_identical_block_recovered_by_widened_reprobe(monkeypatch):
    # The refetch inserts 2.5s of un-marked filler between A and C (its
    # bracketing silence went undetected on the refetch), so C is unmatched
    # by the chain and probed with the neighbor offset 0. The true offset
    # (+2.5s) lies outside the base +-2s search window but inside the
    # doubled +-4s retry window: the drift re-probe must recover C as
    # identical instead of scoring it different.
    seg = _blocks([('a', 8, 11, 220.0), ('c', 8, 12, 440.0),
                   ('pad', 2.5, 77, None)])
    run_pcm, run_marks = _assemble([seg['a'], ('sil', 0.4), seg['c']])
    ref_pcm, ref_marks = _assemble(
        [seg['a'], ('sil', 0.4), np.concatenate([seg['pad'], seg['c']])])

    searches = []
    real = df._block_correlation

    def spy(*args, **kwargs):
        searches.append(kwargs.get('search_s', df.XCORR_SEARCH_S))
        return real(*args, **kwargs)

    monkeypatch.setattr(df, '_block_correlation', spy)
    result = df._align_and_diff_pcm(run_pcm, ref_pcm, run_marks, ref_marks)

    # The widened retry actually ran.
    assert df.XCORR_SEARCH_S * 2 in searches
    c_regions = [r for r in result['regions']
                 if r['start_s'] <= 8.5 <= r['end_s'] or r['start_s'] >= 8.0]
    assert result['status'] == 'no_differential'
    assert all(r['kind'] == 'identical' for r in result['regions'])
    assert c_regions
    tail = result['regions'][-1]
    assert tail['kind'] == 'identical'
    assert tail['corr'] >= df.XCORR_MIN_CORR


def test_unprobeable_sliver_yields_unknown():
    # A sub-second block between two silences (0.4s burst; midpoint-to-
    # midpoint 0.8s) is too short to probe: it must be emitted as kind
    # 'unknown' with corr None, and it must not merge with the identical
    # regions on either side.
    seg = _blocks([('a', 8, 21, 220.0), ('s', 0.4, 22, None),
                   ('b', 8, 23, 330.0)])
    run_pcm, run_marks = _assemble(
        [seg['a'], ('sil', 0.4), seg['s'], ('sil', 0.4), seg['b']])

    result = df._align_and_diff_pcm(run_pcm, run_pcm, run_marks, run_marks)

    kinds = [r['kind'] for r in result['regions']]
    assert kinds == ['identical', 'unknown', 'identical']
    unknown = result['regions'][1]
    assert unknown['corr'] is None
    assert abs(unknown['start_s'] - 8.2) <= 0.1
    assert abs(unknown['end_s'] - 9.0) <= 0.1
    assert result['status'] == 'no_differential'


def test_reencode_guard_fractions_computed_over_measured_regions(monkeypatch):
    # Blocks: 1.5s identical / 20s unknown (probe unusable) / 10s
    # differential. Over MEASURED duration (11.5s) the fractions are
    # diff 0.87 (> 0.70) and identical 0.13 (< 0.15): both guard conditions
    # hold and the result is discarded. Over the whole run (31.5s) diff
    # would be only 0.32 and the guard would (wrongly) keep it -- pins the
    # measured-duration denominator.
    marks = [0.0, 1.5, 21.5, 31.5]
    pcm = np.zeros(int(31.5 * RATE), dtype=np.float32)

    def scripted(run_pcm, ref_pcm, run_t, offset, **kwargs):
        if run_t < 1.5:
            return 0.9
        if run_t < 21.5:
            return None
        return 0.2

    monkeypatch.setattr(df, '_block_correlation', scripted)
    result = df._align_and_diff_pcm(pcm, pcm, marks, marks)

    assert result['status'] == 'unreliable_reencode'
    assert result['regions'] == []


def test_adjacent_differential_blocks_merge_into_one_region(monkeypatch):
    # A multi-ad break spanning several silence-delimited blocks must come
    # out as ONE differential region (otherwise each sub-block could fall
    # under the hold duration floor downstream). Merged corr is the max of
    # the members: the least-different block gates candidacy.
    marks = [0.0, 8.0, 14.0, 20.0, 28.0]

    def scripted(run_pcm, ref_pcm, run_t, offset, **kwargs):
        if run_t < 8.0:
            return 0.9
        if run_t < 14.0:
            return 0.2
        if run_t < 20.0:
            return 0.4
        return 0.95

    pcm = np.zeros(int(28.0 * RATE), dtype=np.float32)
    monkeypatch.setattr(df, '_block_correlation', scripted)
    result = df._align_and_diff_pcm(pcm, pcm, marks, marks)

    assert result['status'] == 'ok'
    diffs = [r for r in result['regions'] if r['kind'] == 'differential']
    assert len(diffs) == 1
    assert diffs[0]['start_s'] == 8.0
    assert diffs[0]['end_s'] == 20.0
    assert diffs[0]['corr'] == 0.4

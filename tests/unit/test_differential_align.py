"""Alignment engine tests on synthetic ffmpeg fixtures (Layer 3).

Two 'episodes' share seeded-noise content blocks separated by digital
silence; only the inserted fill differs (6s vs 9s, different seeds).
align_and_diff must recover the fill span as a differential region within
0.5s and report matched content as identical regions. No network involved.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import differential_fetcher as df
from differential_fetcher import align_and_diff

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='ffmpeg not installed')

RATE = 8000


def _noise(path, seconds, seed):
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi',
         '-i', f'anoisesrc=color=pink:r={RATE}:seed={seed}:amplitude=0.5',
         '-t', str(seconds), '-ac', '1', '-acodec', 'pcm_s16le', path],
        check=True, capture_output=True)


def _silence(path, seconds):
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi',
         '-i', f'anullsrc=r={RATE}:cl=mono',
         '-t', str(seconds), '-ac', '1', '-acodec', 'pcm_s16le', path],
        check=True, capture_output=True)


def _concat(work, name, seg_paths):
    list_path = os.path.join(work, f'{name}.txt')
    with open(list_path, 'w') as fh:
        for p in seg_paths:
            fh.write(f"file '{p}'\n")
    out = os.path.join(work, f'{name}.wav')
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
         '-c', 'copy', out],
        check=True, capture_output=True)
    return out


@pytest.fixture
def episode_pair(tmp_path):
    work = str(tmp_path)
    seg = {}
    for name, seconds, seed in [
        ('a', 10, 101), ('b', 10, 202), ('c', 10, 303),
        ('fill_run', 6, 901), ('fill_ref', 9, 902),
    ]:
        seg[name] = os.path.join(work, f'{name}.wav')
        _noise(seg[name], seconds, seed)
    seg['sil'] = os.path.join(work, 'sil.wav')
    _silence(seg['sil'], 0.3)

    run_file = _concat(work, 'run', [
        seg['a'], seg['sil'], seg['fill_run'], seg['sil'],
        seg['b'], seg['sil'], seg['c']])
    ref_file = _concat(work, 'refetch', [
        seg['a'], seg['sil'], seg['fill_ref'], seg['sil'],
        seg['b'], seg['sil'], seg['c']])
    return work, run_file, ref_file


def test_differential_region_recovered_within_half_second(episode_pair):
    work, run_file, ref_file = episode_pair
    result = align_and_diff(run_file, ref_file, work)

    assert result['status'] == 'ok'
    diffs = [r for r in result['regions'] if r['kind'] == 'differential']
    assert len(diffs) == 1
    # The run-file fill spans 10.3s-16.3s.
    assert abs(diffs[0]['start_s'] - 10.3) <= 0.5
    assert abs(diffs[0]['end_s'] - 16.3) <= 0.5


def test_identical_regions_carry_high_correlation(episode_pair):
    work, run_file, ref_file = episode_pair
    result = align_and_diff(run_file, ref_file, work)

    identical = [r for r in result['regions'] if r['kind'] == 'identical']
    assert identical
    assert all(r['corr'] >= 0.75 for r in identical)


def test_identical_files_report_no_differential(episode_pair):
    work, run_file, _ = episode_pair
    result = align_and_diff(run_file, run_file, work)

    assert result['status'] == 'no_differential'
    assert all(r['kind'] == 'identical' for r in result['regions'])


def test_whole_file_reencode_is_discarded(tmp_path):
    # FIX 1 (#541): when nearly the whole run reads as differential (two fully
    # different files -- the CDN re-encode case), the aligner is unreliable and
    # the differential is discarded rather than cut. status 'unreliable_reencode'
    # with zero regions.
    work = str(tmp_path)
    run_file = os.path.join(work, 'run.wav')
    ref_file = os.path.join(work, 'ref.wav')
    _noise(run_file, 30, 111)
    _noise(ref_file, 30, 222)  # different seed: nothing correlates
    result = align_and_diff(run_file, ref_file, work)
    assert result['status'] == 'unreliable_reencode'
    assert result['regions'] == []


@pytest.fixture
def three_fill_pair(tmp_path):
    # Content blocks a..d (10s each) with three short differing fills between
    # them. Total differential ~= 3 * ~2s / ~46s < 40%, so it is a normal DAI
    # case and must NOT be discarded by the fraction guard.
    work = str(tmp_path)
    seg = {}
    for name, seconds, seed in [
        ('a', 10, 101), ('b', 10, 202), ('c', 10, 303), ('d', 10, 404),
        ('f1_run', 2, 901), ('f1_ref', 3, 902),
        ('f2_run', 2, 903), ('f2_ref', 3, 904),
        ('f3_run', 2, 905), ('f3_ref', 3, 906),
    ]:
        seg[name] = os.path.join(work, f'{name}.wav')
        _noise(seg[name], seconds, seed)
    seg['sil'] = os.path.join(work, 'sil.wav')
    _silence(seg['sil'], 0.3)
    run_file = _concat(work, 'run', [
        seg['a'], seg['sil'], seg['f1_run'], seg['sil'],
        seg['b'], seg['sil'], seg['f2_run'], seg['sil'],
        seg['c'], seg['sil'], seg['f3_run'], seg['sil'], seg['d']])
    ref_file = _concat(work, 'refetch', [
        seg['a'], seg['sil'], seg['f1_ref'], seg['sil'],
        seg['b'], seg['sil'], seg['f2_ref'], seg['sil'],
        seg['c'], seg['sil'], seg['f3_ref'], seg['sil'], seg['d']])
    return work, run_file, ref_file


def test_three_discrete_fills_not_discarded(three_fill_pair):
    work, run_file, ref_file = three_fill_pair
    result = align_and_diff(run_file, ref_file, work)
    assert result['status'] == 'ok'
    diffs = [r for r in result['regions'] if r['kind'] == 'differential']
    assert len(diffs) == 3


@pytest.fixture
def high_diff_aligned_pair(tmp_path):
    # ~60% differential but the aligner locks on: two 10s identical content
    # blocks bracket one long differing fill (30s run / 27s ref, so the
    # duration mismatch splits the offset groups and the content is probed
    # separately from the fill). differential_fraction ~0.59 (below the 0.70
    # cap) with identical_fraction ~0.40 (well above the 0.15 floor). The OLD
    # 0.5 OR-threshold would have discarded this; the retuned AND guard must
    # NOT -- a correctly-aligned ad-heavy episode keeps its ads (#541).
    work = str(tmp_path)
    seg = {}
    for name, seconds, seed in [
        ('a', 10, 101), ('b', 10, 202),
        ('fill_run', 30, 901), ('fill_ref', 27, 902),
    ]:
        seg[name] = os.path.join(work, f'{name}.wav')
        _noise(seg[name], seconds, seed)
    seg['sil'] = os.path.join(work, 'sil.wav')
    _silence(seg['sil'], 0.3)
    run_file = _concat(work, 'run', [
        seg['a'], seg['sil'], seg['fill_run'], seg['sil'], seg['b']])
    ref_file = _concat(work, 'refetch', [
        seg['a'], seg['sil'], seg['fill_ref'], seg['sil'], seg['b']])
    return work, run_file, ref_file


def test_high_differential_but_aligned_not_discarded(high_diff_aligned_pair):
    work, run_file, ref_file = high_diff_aligned_pair
    result = align_and_diff(run_file, ref_file, work)
    assert result['status'] == 'ok'
    diffs = [r for r in result['regions'] if r['kind'] == 'differential']
    # The 30s fill dominates differential coverage but the guard keeps it.
    assert any(r['end_s'] - r['start_s'] >= 25.0 for r in diffs)


def test_reencode_discards_when_both_conditions_hold(episode_pair, monkeypatch):
    # FIX 1 re-encode guard (AND semantics): force correlation to never confirm,
    # so no identical coverage is recorded. identical_fraction 0.0 (< 0.15) and
    # ~all of the run then reads as differential (> 0.70) -- both conditions hold
    # -> discard as a likely whole-file re-encode.
    work, run_file, ref_file = episode_pair
    monkeypatch.setattr(df, '_block_correlation', lambda *a, **k: 0.0)
    result = align_and_diff(run_file, ref_file, work)
    assert result['status'] == 'unreliable_reencode'
    assert result['regions'] == []


def test_decode_pcm_cleans_temp_on_read_failure(tmp_path, monkeypatch):
    work = str(tmp_path)
    wav = os.path.join(work, 'tiny.wav')
    _noise(wav, 1, 555)

    def boom(*a, **k):
        raise ValueError('simulated PCM read failure')

    monkeypatch.setattr(df.np, 'fromfile', boom)

    with pytest.raises(ValueError):
        df._decode_pcm(wav, work, 'run')

    leftover = [f for f in os.listdir(work) if f.endswith('.pcm')]
    assert leftover == []

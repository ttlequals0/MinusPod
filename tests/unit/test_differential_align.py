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

"""Fixtures for replaying saved production episodes through the pipeline code."""
import copy
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

FIXTURES_ENV = 'MINUSPOD_PROD_FIXTURES'
_SUMMARY_KEY = pytest.StashKey[list]()
# The export omits text_snippet, so an auto-filed confirm's hold reason comes from the run log.
_CORROBORATES_RE = re.compile(r'corroborates (\S+) hold ([\d.]+)s-([\d.]+)s')


def fixtures_dir():
    raw = os.environ.get(FIXTURES_ENV)
    return Path(raw).expanduser() if raw else None


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def load_episode(root, episode_id):
    """Saved markers, corrections and word-timed segments for one episode; a fresh copy per call."""
    return copy.deepcopy(_load_episode_raw(root, episode_id))


@lru_cache(maxsize=None)
def _load_episode_raw(root, episode_id):
    base = root / 'episodes' / episode_id
    data = load_json(base / 'replay_input.json')
    data['segments'] = load_json(base / 'original_segments.json')['segments']
    log = base / 'full.log.txt'
    data['auto_filed_reasons'] = {
        (float(lo), float(hi)): reason
        for reason, lo, hi in _CORROBORATES_RE.findall(log.read_text() if log.exists() else '')}
    return data


@pytest.fixture(scope='session')
def prod_root():
    root = fixtures_dir()
    if root is None:
        pytest.skip(f'{FIXTURES_ENV} not set')
    return root


@pytest.fixture(scope='session')
def replay_out(prod_root):
    out = prod_root / 'replay_out'
    out.mkdir(exist_ok=True)
    return out


@pytest.fixture
def summary_rows(request):
    rows = request.config.stash.setdefault(_SUMMARY_KEY, [])
    return rows


def pytest_terminal_summary(terminalreporter, config):
    rows = config.stash.get(_SUMMARY_KEY, None)
    if not rows:
        return
    tr = terminalreporter
    tr.section('prod replay: rendered ad clusters (report only)')
    tr.write_line(f"{'episode':<14} {'cuts':>4} {'rejects':>7} {'holds':>5} {'expected':>8}  flag")
    for row in rows:
        tr.write_line(f"{row['episode']:<14} {row['cuts']:>4} {row['restored_rejects']:>7} "
                      f"{row['holds']:>5} {row['expected']:>8}  {row['flag']}")
    tr.write_line(f"total cuts {sum(r['cuts'] for r in rows)}, "
                  f"holds {sum(r['holds'] for r in rows)}, "
                  f"expected {sum(r['expected'] for r in rows)}")

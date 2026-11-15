"""precise_cut_edges off: no word snap, no audio read, and pauses a flag-on run stored are dropped."""
import json
import pathlib

from tests.app_bootstrap import bootstrap

bootstrap('precise_cut_edges_off_test_')

import pytest

from main_app.pipeline import cuts

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'cut_edges'
CASES = ('case1_acme_health_leadin.json', 'case3_example_opal_tail_splice.json',
         'case4_example_cleanslate_sun.json')


def _load(path):
    return json.loads(path.read_text())


def _reader(case, calls=None):
    profile = case['energy'][0]
    t0, frames = profile['t0'], profile['rms_dbfs']

    def read(a, b):
        if calls is not None:
            calls.append((a, b))
        i, j = round((a - t0) / 0.01), round((b - t0) / 0.01)
        return frames[i:j] if 0 <= i < j <= len(frames) else None
    return read


@pytest.fixture
def no_save(monkeypatch):
    monkeypatch.setattr(cuts.storage, 'save_combined_ads', lambda *a, **k: None)


@pytest.fixture
def flag(monkeypatch):
    def set_flag(on):
        monkeypatch.setattr(cuts, 'resolve_precise_cut_edges', lambda db: on)
    return set_flag


@pytest.mark.parametrize('name', CASES)
def test_word_snap_off_is_a_no_op(name, no_save, flag):
    flag(False)
    case = _load(FIXTURES / name)
    marker = dict(case['marker'])
    calls = []
    cut_list = [marker]
    out = cuts._snap_cut_edges_to_words('slug', 'ep', cut_list, [marker], case['segments'], None,
                                        4000.0, energy_reader=_reader(case, calls))
    assert out is cut_list and marker == case['marker']
    assert calls == []


def test_flag_off_drops_pauses_a_flag_on_run_stored(no_save, flag):
    flag(False)
    cut = {'start': 100.0, 'end': 160.0, 'edge_pause_s': {'end': 0.09}, 'edge_snap': {}}
    (out,) = cuts._snap_cut_edges_to_words('slug', 'ep', [cut], [cut], [], None, 4000.0)
    assert 'edge_pause_s' not in out and out['edge_snap'] == {}
    assert cut['edge_pause_s'] == {'end': 0.09}

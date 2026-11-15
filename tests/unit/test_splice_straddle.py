"""Straddle mode of the splice snaps: one segment spans a strong splice, so the edge moves onto it."""
import json
import pathlib
from types import SimpleNamespace

from tests.app_bootstrap import bootstrap

bootstrap('splice_straddle_test_')

import pytest

from ad_detector.boundaries import snap_extended_ad_tails_to_splice, snap_terminal_ad_to_splice
from main_app.pipeline import cuts
from tests.unit.reviewer_test_utils import _worded

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'cut_edges'


def _case(name):
    return json.loads((FIXTURES / name).read_text())


def _mirror():
    segments = [_worded(80.0, 98.0, 'and that is how the garden grows every single year in spring'),
                _worded(98.0, 112.0, 'it blooms before Acme is great for your garden shop now'),
                _worded(112.2, 130.0, 'Acme makes the best tools use the code garden today')]
    marker = {'start': 112.0, 'end': 130.0, 'sponsor': 'Acme', 'reason': 'Acme sponsor read'}
    events = [{'type': 'digital_silence', 'time': 103.0, 'end_time': 104.0, 'depth_dbfs': -120.0,
               'duration_s': 1.0}]
    return marker, segments, events


TERMINAL = {
    'segments': [_worded(0.0, 880.0, 'show talk ' * 200),
                 _worded(905.0, 930.0, 'visit acme.com today for twenty percent off acme')],
    'events': [{'type': 'digital_silence', 'time': 897.0, 'end_time': 897.6, 'depth_dbfs': -120.0},
               {'type': 'deep_silence', 'time': 899.5, 'end_time': 900.0, 'depth_dbfs': -70.0}],
    'ads': [{'start': 902.0, 'end': 931.0, 'reason': 'acme sponsor read'},
            {'start': 300.0, 'end': 360.0, 'reason': 'mid roll'}],
    'duration': 932.0,
}


@pytest.fixture
def no_save(monkeypatch):
    monkeypatch.setattr(cuts.storage, 'save_combined_ads', lambda *a, **k: None)


@pytest.fixture
def flag(monkeypatch):
    def set_flag(on):
        monkeypatch.setattr(cuts, 'resolve_precise_cut_edges', lambda db: on)
    return set_flag


def _run(marker, segments, events, status='cold_start'):
    audio = SimpleNamespace(splice_evidence={'events': events, 'calibration': {'status': status}})
    master = dict(marker)
    out = cuts._snap_straddled_edges('slug', 'ep', [master], [master], segments, audio)
    return out[0], master


def _variants():
    c3 = _case('case3_example_opal_tail_splice.json')
    base = {**c3['marker'], 'sponsor': 'Opal'}
    deep = [{**c3['splice_events'][0], 'type': 'deep_silence'}]
    shallow = [{**c3['splice_events'][0], 'depth_dbfs': -60.0}]
    seg, ev = c3['segments'], c3['splice_events']
    return {
        'case3': (base, seg, ev, 'cold_start'),
        'case3_locked_after': ({**base, 'reviewer_locked_end': 1443.5}, seg, ev, 'cold_start'),
        'case3_locked_inside': ({**base, 'reviewer_locked_end': 1437.5}, seg, ev, 'cold_start'),
        'case3_confirmed': ({**base, 'validation': {'user_confirmed': True}}, seg, ev, 'cold_start'),
        'case3_deep_cold': (base, seg, deep, 'cold_start'),
        'case3_deep_calibrated': (base, seg, deep, 'calibrated'),
        'case3_shallow': (base, seg, shallow, 'cold_start'),
        'mirror': (*_mirror(), 'cold_start'),
    }


@pytest.mark.parametrize('label', sorted(_variants()))
def test_flag_off_leaves_the_cut_list_alone(label, no_save, flag):
    flag(False)
    marker, segments, events, status = _variants()[label]
    out, master = _run(marker, segments, events, status)
    assert out == marker and master == marker


def test_case3_straddling_segment_extends_end_to_splice(no_save, flag):
    flag(True)
    case = _case('case3_example_opal_tail_splice.json')
    out, _ = _run(*_variants()['case3'])
    lo, hi = case['expected']['expected_end_tolerance']
    assert lo <= out['end'] <= hi
    assert out['reviewer_locked_end'] == out['end']
    assert out['start'] == case['expected']['expected_start']
    assert out['edge_splice_anchor'] == ['end']


def test_straddle_mode_on_the_upstream_functions_directly():
    c3 = _case('case3_example_opal_tail_splice.json')
    ad = {**c3['marker'], 'sponsor': 'Opal'}
    (out,) = snap_extended_ad_tails_to_splice([ad], c3['segments'], c3['splice_events'], straddle=True)
    assert out['end'] == pytest.approx(1442.93) and out is not ad
    marker, segments, events = _mirror()
    (out,) = snap_terminal_ad_to_splice([marker], segments, events, 0.0, 0.0, straddle=True)
    assert out['start'] == pytest.approx(103.0) and out['edge_splice_anchor'] == ['start']


def test_a_later_marker_bars_the_straddle(no_save, flag):
    flag(True)
    marker, segments, events, _ = _variants()['case3']
    barrier = {'start': 1440.0, 'end': 1500.0}
    audio = SimpleNamespace(splice_evidence={'events': events, 'calibration': {'status': 'cold_start'}})
    out = cuts._snap_straddled_edges('slug', 'ep', [dict(marker)], [dict(marker), barrier], segments, audio)
    assert out[0]['end'] == marker['end']

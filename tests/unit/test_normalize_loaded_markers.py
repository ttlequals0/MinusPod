"""Legacy marker normalization at load."""
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
# The api imports build Storage from this env var.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='normalize_markers_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from api.episodes import _markers_from_row  # noqa: E402
from api.patterns import _insert_manual_marker, _load_episode_markers  # noqa: E402
from main_app import processing  # noqa: E402
from utils.markers import (DAI_PROBE_SPANS, clip_dai_core_spans,  # noqa: E402
                           normalize_loaded_markers, parse_ad_markers)


def _legacy(start=0.0, end=73.2):
    return {'start': start, 'end': end, 'detection_stage': 'dai_differential',
            'dai_core_spans': [{'start': start, 'end': end}]}


def _modern(start=100.0, end=160.0):
    return dict(_legacy(start, end), **{DAI_PROBE_SPANS: [{'start': start + 0.5, 'end': start + 4.5}]})


def test_normalize_leaves_recorded_probes_and_plain_markers_alone():
    plain = {'start': 5.0, 'end': 20.0}
    empty_probes = dict(_legacy(), **{DAI_PROBE_SPANS: []})
    markers = [plain, _modern(), empty_probes, 'not-a-dict']
    before = json.dumps(markers)
    assert normalize_loaded_markers(markers) is markers
    assert json.dumps(markers) == before


def test_parse_ad_markers_normalizes_and_rejects_bad_input():
    parsed = parse_ad_markers(json.dumps([_legacy()]))
    assert parsed[0][DAI_PROBE_SPANS] == [{'start': 0.0, 'end': 4.5}]
    assert parse_ad_markers(None) is None
    assert parse_ad_markers('') is None
    assert parse_ad_markers('{bad json') is None
    assert parse_ad_markers('{"start": 1}') is None


def test_clip_without_probes_does_not_invent_them():
    marker = _legacy()
    clip_dai_core_spans(marker, 10.0, 40.0)
    assert marker['dai_core_spans'] == [{'start': 10.0, 'end': 40.0}]
    assert DAI_PROBE_SPANS not in marker


def test_loaders_return_normalized_markers():
    raw = json.dumps([_legacy()])
    episode = {'ad_markers_json': raw}
    expected = [{'start': 0.0, 'end': 4.5}]

    assert _markers_from_row(episode)[0][DAI_PROBE_SPANS] == expected

    class _Db:
        def get_episode(self, slug, episode_id):
            return dict(episode)
    _, markers = _load_episode_markers(_Db(), 'example-podcast', 'a1b2c3d4e5f6')
    assert markers[0][DAI_PROBE_SPANS] == expected

    spliced = _insert_manual_marker(dict(episode), 200.0, 230.0, 'Acme', '')
    legacy = next(m for m in spliced if m.get('detection_stage') == 'dai_differential')
    assert legacy[DAI_PROBE_SPANS] == expected


def test_restore_saved_markers_logs_unreadable_json(monkeypatch, caplog):
    saved = []
    monkeypatch.setattr(processing.storage, 'save_combined_ads',
                        lambda *a: saved.append(a))
    with caplog.at_level(logging.ERROR, logger='podcast.audio'):
        processing._restore_saved_markers('example-podcast', 'a1b2c3d4e5f6',
                                          {'ad_markers_json': '{bad json'})
        processing._restore_saved_markers('example-podcast', 'a1b2c3d4e5f6',
                                          {'ad_markers_json': None})
    assert saved == []
    assert [r.getMessage() for r in caplog.records].count(
        '[example-podcast:a1b2c3d4e5f6] Could not restore markers after a failed run: '
        'unreadable ad_markers_json') == 1


def test_legacy_partial_cut_spans_expand_on_load():
    legacy = {'start': 60.0, 'end': 120.0, 'was_cut': False, 'category': 'sponsor',
              'validation': {'decision': 'ACCEPT', 'flags': []},
              'partial_cut_spans': [{'start': 60.0, 'end': 90.0}, {'start': 100.0, 'end': 110.0}]}
    cut = {'start': 200.0, 'end': 230.0, 'was_cut': True}
    markers = [legacy, cut]
    assert normalize_loaded_markers(markers) is markers
    assert [(m['start'], m['end'], m['was_cut']) for m in markers] == [
        (60.0, 90.0, True), (90.0, 100.0, False), (100.0, 110.0, True),
        (110.0, 120.0, False), (200.0, 230.0, True)]
    assert all(m['carved_from'] == {'start': 60.0, 'end': 120.0} for m in markers[:4])
    assert all('partial_cut_spans' not in m for m in markers)
    assert all(m['validation']['flags'] == [] for m in markers[:4])
    assert normalize_loaded_markers(markers) == markers


def _held_legacy():
    return {'start': 60.0, 'end': 120.0, 'was_cut': False, 'held_for_review': True,
            'hold_reason': 'no_cue_evidence',
            'partial_cut_spans': [{'start': 60.0, 'end': 90.0}]}


def test_held_legacy_partial_cut_stays_whole_on_load():
    markers = [_held_legacy()]
    normalize_loaded_markers(markers)
    expected = _held_legacy()
    expected.pop('partial_cut_spans')
    assert len(markers[0].pop('hold_id')) == 12
    assert markers == [expected]

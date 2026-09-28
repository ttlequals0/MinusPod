"""Legacy marker normalization at load and the one-shot at-rest migration."""
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

GATE = 'normalize_legacy_dai_probe_spans_once'


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


def _seed(temp_db, markers, slug='example-podcast', episode_id='a1b2c3d4e5f6'):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Example Podcast')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    temp_db.save_episode_details(slug, episode_id, ad_markers=markers)
    return slug, episode_id


def _run(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._normalize_legacy_dai_probe_spans(conn)
    return conn


def _raw(conn):
    return conn.execute("SELECT ad_markers_json FROM episode_details").fetchone()[0]


def test_migration_rewrites_legacy_rows_and_sets_gate(temp_db):
    slug, eid = _seed(temp_db, [_legacy(), _modern()])

    conn = _run(temp_db)

    stored = json.loads(_raw(conn))
    assert stored[0][DAI_PROBE_SPANS] == [{'start': 0.0, 'end': 4.5}]
    assert stored[1][DAI_PROBE_SPANS] == [{'start': 100.5, 'end': 104.5}]
    assert stored[0]['start'] == 0.0 and stored[0]['end'] == 73.2
    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_migration_leaves_unchanged_and_unreadable_rows_byte_identical(temp_db):
    _seed(temp_db, [_modern()])
    conn = temp_db.get_connection()
    # Both pass the LIKE prefilter; non-canonical spacing shows a rewrite would be caught.
    modern = ('[ {"start": 100.0, "end": 160.0, "dai_core_spans": [{"start": 100.0, "end": 160.0}],'
              ' "dai_probe_spans": [{"start": 100.5, "end": 104.5}]} ]')
    for raw in (modern, '[{"dai_core_spans": '):
        conn.execute("UPDATE episode_details SET ad_markers_json = ?", (raw,))
        conn.commit()
        _run(temp_db)
        assert _raw(conn) == raw
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


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


def test_migration_is_gated(temp_db):
    _seed(temp_db, [_modern()])
    conn = _run(temp_db)
    conn.execute("UPDATE episode_details SET ad_markers_json = ?",
                 (json.dumps([_legacy()]),))
    conn.commit()

    temp_db._normalize_legacy_dai_probe_spans(conn)

    assert DAI_PROBE_SPANS not in json.loads(_raw(conn))[0]

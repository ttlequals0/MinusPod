"""A rejected fingerprint match stays rejected through the recut list and the save that follows."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recut_rejected_match_test_')

from main_app import processing  # noqa: E402
from main_app.pipeline import recut  # noqa: E402
from storage import Storage  # noqa: E402
from tests.unit.pass2_test_utils import _user_corrections  # noqa: E402
from tests.fingerprint_match_helpers import get_fingerprint_matches

SLUG = 'example-podcast'
EP = 'a1b2c3d4e5f6'
SEGMENTS = [{'start': float(t), 'end': float(t + 5), 'text': 'sponsor read for acme'}
            for t in range(0, 400, 5)]


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    import database
    monkeypatch.setattr(database.Database, '_instance', None)
    monkeypatch.setenv('DATA_DIR', _test_data_dir)
    yield


def _fingerprint_marker(pid, start, end):
    return {'start': start, 'end': end, 'confidence': 0.98, 'detection_stage': 'fingerprint',
            'pattern_id': pid, 'sponsor': 'Acme', 'reason': 'Acme read', 'was_cut': True,
            'fingerprint_match_start': start, 'fingerprint_match_end': end,
            'fingerprint_similarity': 0.9, 'fingerprint_coverage': 0.8}


def test_a_rejected_match_stays_rejected_through_a_recut(temp_db, monkeypatch):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode(SLUG, EP, title='Ep')
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='acme read ' * 12)
    stored = [_fingerprint_marker(pid, 30.0, 90.0), _fingerprint_marker(pid, 300.0, 360.0)]
    temp_db.record_fingerprint_matches_from_markers(SLUG, EP, stored)
    assert temp_db.mark_fingerprint_match_rejected(pid, SLUG, EP, 300.0, 360.0)
    rejected_at = next(r for r in get_fingerprint_matches(temp_db, pid, 10, 0) if r['rejected'])['rejected_at']

    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: {'ad_markers_json': json.dumps(stored)})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(processing.db, 'get_podcast_cue_settings_overrides', lambda pid: {})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections',
                        lambda podcast_id, eid: [{'start': 300.0, 'end': 360.0}])
    _, all_ads, *_ = recut._build_recut_ad_list(
        SLUG, EP, SEGMENTS, 600.0, '', 0.80, corrections=_user_corrections(SLUG, EP))

    stor = object.__new__(Storage)
    stor.db = temp_db
    stor.save_combined_ads(SLUG, EP, all_ads)

    rows = sorted(get_fingerprint_matches(temp_db, pid, 10, 0), key=lambda r: r['match_start'])
    assert [(r['match_start'], r['rejected']) for r in rows] == [(30.0, 0), (300.0, 1)]
    assert rows[1]['rejected_at'] == rejected_at
    marker = next(a for a in all_ads if a['start'] == 300.0)
    assert (marker['validation']['decision'], marker['was_cut']) == ('REJECT', False)

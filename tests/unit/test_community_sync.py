"""Tests for community_sync.apply_manifest semantics."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
from community_sync import apply_manifest, sync_now  # noqa: E402


@pytest.fixture
def db(tmp_path):
    Database._instance = None  # type: ignore[attr-defined]
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None  # type: ignore[attr-defined]


def _pattern_entry(cid, version=1, sponsor='Squarespace', text='community version '):
    return {
        'community_id': cid,
        'version': version,
        'data': {
            'community_id': cid,
            'version': version,
            'scope': 'global',
            'sponsor': sponsor,
            'text_template': f'{text} for {sponsor} dot com slash show promo SHOW save ten percent today extra body',
            'intro_variants': [],
            'outro_variants': [],
        },
    }


def test_apply_manifest_insert_update_delete(db):
    # Initial sync: insert two patterns.
    summary = apply_manifest(db, {
        'manifest_version': 1,
        'patterns': [
            _pattern_entry('c-1'),
            _pattern_entry('c-2', sponsor='NordVPN', text='community nord text'),
        ],
    })
    assert summary['inserted'] == 2
    assert summary['updated'] == 0
    assert summary['deleted'] == 0

    rows = db.get_patterns_by_source('community', active_only=False)
    assert len(rows) == 2

    # Second sync: bump version of c-1, drop c-2, add c-3.
    summary = apply_manifest(db, {
        'manifest_version': 2,
        'patterns': [
            _pattern_entry('c-1', version=2, text='community version two'),
            _pattern_entry('c-3', sponsor='ExpressVPN', text='community express'),
        ],
    })
    assert summary['inserted'] == 1
    assert summary['updated'] == 1
    assert summary['deleted'] == 1

    rows = db.get_patterns_by_source('community', active_only=False)
    cids = {r['community_id'] for r in rows}
    assert cids == {'c-1', 'c-3'}
    c1 = next(r for r in rows if r['community_id'] == 'c-1')
    assert c1['version'] == 2
    assert 'version two' in c1['text_template']


def test_apply_manifest_respects_protected_flag(db):
    apply_manifest(db, {'manifest_version': 1, 'patterns': [_pattern_entry('p-1')]})
    rows = db.get_patterns_by_source('community', active_only=False)
    assert len(rows) == 1
    pid = rows[0]['id']
    db.set_pattern_protected(pid, True)

    # Re-sync: try to bump version and drop. Protected pattern should survive both.
    summary = apply_manifest(db, {
        'manifest_version': 2,
        'patterns': [_pattern_entry('p-1', version=5, text='attempted overwrite text')],
    })
    assert summary['updated'] == 0
    assert summary['skipped'] >= 1
    rows = db.get_patterns_by_source('community', active_only=False)
    assert rows[0]['version'] == 1

    summary = apply_manifest(db, {'manifest_version': 3, 'patterns': []})
    assert summary['deleted'] == 0
    assert summary['skipped'] >= 1
    rows = db.get_patterns_by_source('community', active_only=False)
    assert len(rows) == 1  # still present


def test_sync_now_records_settings_state(db, monkeypatch):
    # Stub the network call.
    def fake_fetch(url):
        return {'manifest_version': 7, 'patterns': [_pattern_entry('c-net')]}
    monkeypatch.setattr('community_sync._fetch_manifest', fake_fetch)

    summary = sync_now(db)
    assert summary['inserted'] == 1
    assert summary['manifest_version'] == 7
    assert db.get_setting('community_sync_manifest_version') == '7'
    assert db.get_setting('community_sync_last_error') == ''
    stored = json.loads(db.get_setting('community_sync_last_summary'))
    assert stored['inserted'] == 1

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


def _pattern_entry(cid, version=1, sponsor='Squarespace', text='community version ',
                    category=None):
    data = {
        'community_id': cid,
        'version': version,
        'scope': 'global',
        'sponsor': sponsor,
        'text_template': f'{text} for {sponsor} dot com slash show promo SHOW save ten percent today extra body',
        'intro_variants': [],
        'outro_variants': [],
    }
    if category is not None:
        data['category'] = category
    return {
        'community_id': cid,
        'version': version,
        'data': data,
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


def test_fetch_manifest_accepts_body_over_old_cap(monkeypatch):
    """The raised cap (1 MB) accepts a manifest larger than the old 256 KB
    ceiling; a deterministic ~512 KB body sits between the two."""
    import community_sync

    payload = {'manifest_version': 1, 'patterns': [_pattern_entry('cid-0')]}
    payload['patterns'][0]['data']['text_template'] = 'x' * (512 * 1024)
    body = json.dumps(payload).encode('utf-8')
    assert 256 * 1024 < len(body) < community_sync.MANIFEST_MAX_BYTES

    class _FakeResp:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            for i in range(0, len(body), chunk_size):
                yield body[i:i + chunk_size]

        def close(self):
            pass

    monkeypatch.setattr(community_sync, 'safe_get', lambda *a, **k: _FakeResp())
    result = community_sync._fetch_manifest('https://example.com/index.json')
    assert result['manifest_version'] == 1
    assert len(result['patterns']) == 1


# ========== community_sync_categories filtering ==========

class TestCommunitySyncCategoryFilter:

    def test_default_settings_accept_all_categories_no_filtering(self, db):
        """Upgrade no-op: an unset community_sync_categories setting imports
        exactly as before the setting existed."""
        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [
                _pattern_entry('c-1', category='cross_promo'),
                _pattern_entry('c-2', sponsor='NordVPN', text='nord text', category='self_promo'),
            ],
        })
        assert summary['inserted'] == 2
        assert summary['filtered'] == 0
        rows = db.get_patterns_by_source('community', active_only=False)
        assert len(rows) == 2
        assert all(r['is_active'] for r in rows)

    def test_new_entry_of_excluded_category_is_filtered_not_inserted(self, db):
        db.set_setting('community_sync_categories', json.dumps(['sponsor']))
        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        assert summary['inserted'] == 0
        assert summary['filtered'] == 1
        assert db.get_patterns_by_source('community', active_only=False) == []

    def test_accepted_category_entry_still_inserts(self, db):
        db.set_setting('community_sync_categories', json.dumps(['sponsor']))
        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='sponsor')],
        })
        assert summary['inserted'] == 1
        assert summary['filtered'] == 0

    def test_existing_row_deactivated_when_category_later_excluded(self, db):
        # First sync: accept everything, insert a cross_promo pattern active.
        apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        rows = db.get_patterns_by_source('community', active_only=False)
        assert len(rows) == 1 and rows[0]['is_active']

        # Now exclude cross_promo and re-sync with the same (unchanged) entry.
        db.set_setting('community_sync_categories', json.dumps(['sponsor']))
        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        assert summary['filtered'] == 1
        assert summary['inserted'] == 0
        assert summary['updated'] == 0
        rows = db.get_patterns_by_source('community', active_only=False)
        assert len(rows) == 1
        assert rows[0]['is_active'] == 0
        assert rows[0]['disabled_reason'] == 'category_filtered'

    def test_reenabling_category_reactivates_on_next_sync(self, db):
        apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        db.set_setting('community_sync_categories', json.dumps(['sponsor']))
        apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        rows = db.get_patterns_by_source('community', active_only=False)
        assert rows[0]['is_active'] == 0

        # Re-enable cross_promo: the next sync must reactivate the row.
        db.set_setting(
            'community_sync_categories', json.dumps(['sponsor', 'cross_promo']))
        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='cross_promo')],
        })
        assert summary['filtered'] == 0
        rows = db.get_patterns_by_source('community', active_only=False)
        assert rows[0]['is_active'] == 1
        assert rows[0]['disabled_reason'] is None

    def test_manually_disabled_row_not_resurrected_by_category_reenable(self, db):
        """A row disabled for an unrelated reason (not the category filter)
        must not be reactivated just because its category is accepted."""
        apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='sponsor')],
        })
        rows = db.get_patterns_by_source('community', active_only=False)
        pid = rows[0]['id']
        db.update_ad_pattern(pid, is_active=0, disabled_reason='manual')

        summary = apply_manifest(db, {
            'manifest_version': 1,
            'patterns': [_pattern_entry('c-1', category='sponsor')],
        })
        assert summary['filtered'] == 0
        row = db.get_ad_pattern_by_id(pid)
        assert row['is_active'] == 0
        assert row['disabled_reason'] == 'manual'

    def test_local_patterns_untouched_by_category_filter(self, db):
        """HARD requirement: only community-sourced rows (a community_id) may
        be touched by the filter, regardless of their own category."""
        local_id = db.create_ad_pattern(
            scope='global', text_template='a local pattern of excluded category',
            category='cross_promo',
        )
        db.set_setting('community_sync_categories', json.dumps(['sponsor']))
        apply_manifest(db, {'manifest_version': 1, 'patterns': []})

        local_pattern = db.get_ad_pattern_by_id(local_id)
        assert local_pattern['is_active'] == 1
        assert local_pattern['disabled_reason'] is None

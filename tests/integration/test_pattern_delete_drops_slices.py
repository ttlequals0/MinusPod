"""Deleting or folding a pattern forgets its cached preview slices."""
import pytest

from api import preview_cache
from tests.integration.fingerprint_helpers import SLUG, csrf  # noqa: E402


@pytest.fixture(autouse=True)
def clean_cache():
    preview_cache._slice_cache.clear()
    yield
    preview_cache._slice_cache.clear()


def _pattern(db, label, **kw):
    pid = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=f'{label} read ' * 8, **kw)
    preview_cache.remember_slice(('slice', pid), b'mp3', pid)
    return pid


def _cached_ids():
    return sorted(entry[2] for entry in preview_cache._slice_cache.values())


def test_single_delete_drops_the_patterns_slices(app_client, fp_feed):
    gone, kept = _pattern(fp_feed, 'one'), _pattern(fp_feed, 'two')
    assert app_client.delete(f'/api/v1/patterns/{gone}', headers=csrf(app_client)).status_code == 200
    assert _cached_ids() == [kept]


def test_bulk_delete_drops_the_slices_of_every_deleted_pattern(app_client, fp_feed):
    a, b, kept = _pattern(fp_feed, 'one'), _pattern(fp_feed, 'two'), _pattern(fp_feed, 'three')
    r = app_client.post('/api/v1/patterns/bulk-delete', headers=csrf(app_client),
                        json={'ids': [a, b], 'confirm': True, 'expected_count': 2})
    assert r.status_code == 200
    assert _cached_ids() == [kept]


def test_community_delete_drops_the_slices_of_community_patterns(app_client, fp_feed):
    community, local = _pattern(fp_feed, 'one', source='community'), _pattern(fp_feed, 'two')
    r = app_client.delete('/api/v1/community-patterns/all', headers=csrf(app_client), json={'confirm': True})
    assert r.status_code == 200
    assert _cached_ids() == [local]
    assert community not in _cached_ids()


def test_merge_drops_the_slices_of_the_folded_patterns_only(app_client, fp_feed):
    keep, folded = _pattern(fp_feed, 'one'), _pattern(fp_feed, 'two')
    r = app_client.post('/api/v1/patterns/merge', headers=csrf(app_client),
                        json={'keep_id': keep, 'merge_ids': [folded]})
    assert r.status_code == 200
    assert _cached_ids() == [keep]

"""Integration tests for GET /feeds/<slug>/ad-distribution (issue #360 panel).

The endpoint surfaces a feed's historical ad-cut position distribution for the
detail-page panel. It is setting-independent: it returns data with the
positional-prior experiment toggle off.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


def _marker(start, end=None):
    return {
        'start': start,
        'end': end if end is not None else start + 60.0,
        'confidence': 0.95,
        'detection_stage': 'claude',
        'was_cut': True,
    }


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database

    db = get_database()
    slug = 'ad-dist-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Ad Dist Feed')
    for i in range(6):
        eid = f'addist{i:03d}'
        db.upsert_episode(slug, eid, original_url=f'https://example.com/{eid}.mp3',
                          title=f'Episode {i}', status='processed',
                          original_duration=1000.0,
                          published_at=f'2026-06-{i + 1:02d}T00:00:00+00:00')
        db.save_episode_details(slug, eid, ad_markers=[_marker(300.0)])

    yield {'slug': slug, 'db': db}

    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def test_returns_distribution_with_experiment_off(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    # Experiment toggle is off (never set); endpoint must still return data.
    r = app_client.get(f'/api/v1/feeds/{slug}/ad-distribution')

    assert r.status_code == 200
    body = r.get_json()
    assert body['slug'] == slug
    assert body['episodesConsidered'] == 6
    assert body['bucketCount'] == len(body['buckets']) == 20
    assert sum(body['buckets']) == body['totalEvents'] == 6
    assert body['buckets'][6] == 6  # 0.30 -> bucket 6
    assert len(body['zones']) == 1
    zone = body['zones'][0]
    assert set(zone) == {'center', 'low', 'high', 'support', 'boost'}
    assert zone['support'] == 6


def test_unknown_feed_returns_404(app_client, _auth):
    r = app_client.get('/api/v1/feeds/no-such-feed/ad-distribution')
    assert r.status_code == 404

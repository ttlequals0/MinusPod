"""T14: markers persisted by a failed run must not render as cut ads.

Contract: an episode with status='failed' and a non-held marker with no
'was_cut' key must not land in adMarkers (the cut/"Detected Ads" bucket).
Companion: the same marker on a 'processed' episode still defaults to cut,
so existing behavior is unchanged.
"""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('failed_run_marker_test_', passphrase='failed-run-marker-test-passphrase')

from main_app import app as flask_app

from api import get_database


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


def _seed_episode(db, slug, episode_id, status):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(slug=slug, episode_id=episode_id, title='ep',
                      original_url=f'https://example.com/{episode_id}.mp3',
                      status=status)
    marker = {'start': 10.0, 'end': 20.0, 'category': 'sponsor'}
    db.save_episode_details(slug, episode_id, ad_markers=[marker])


def test_failed_run_marker_not_in_cut_bucket(client):
    db = get_database()
    slug = 'failed-run-feed'
    episode_id = 'aaaaaaaaaaaa'
    _seed_episode(db, slug, episode_id, status='failed')

    resp = client.get(f'/api/v1/feeds/{slug}/episodes/{episode_id}')
    assert resp.status_code == 200
    data = resp.get_json()

    assert data['adMarkers'] == []
    assert len(data['rejectedAdMarkers']) == 1


def test_processed_episode_marker_still_defaults_to_cut(client):
    """Regression: an ACCEPT marker without was_cut on a processed episode
    still lands in adMarkers, unchanged from before this fix."""
    db = get_database()
    slug = 'processed-run-feed'
    episode_id = 'bbbbbbbbbbbb'
    _seed_episode(db, slug, episode_id, status='processed')

    resp = client.get(f'/api/v1/feeds/{slug}/episodes/{episode_id}')
    assert resp.status_code == 200
    data = resp.get_json()

    assert len(data['adMarkers']) == 1
    assert data['rejectedAdMarkers'] == []

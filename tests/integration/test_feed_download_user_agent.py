"""Per-feed download UA validation and persistence."""
import pytest

from config import USER_AGENT_MAX_LENGTH
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_download_ua_')


@pytest.fixture
def feed_ua(app_client):
    from api import get_database, get_storage
    db = get_database()
    get_storage().podcasts_dir.mkdir(parents=True, exist_ok=True)
    slug = 'download-ua-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Example')
    with app_client.session_transaction() as session:
        session['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    headers = {'X-CSRF-Token': next(cookie.value for cookie in app_client._cookies.values()
                                  if cookie.key == 'minuspod_csrf')}
    yield slug, db, headers
    db.delete_podcast(slug)


UA_OVERRIDE_FIELDS = [
    ('downloadUserAgentOverride', 'download_user_agent_override'),
    ('feedUserAgentOverride', 'feed_user_agent_override'),
]


@pytest.mark.parametrize('field, column', UA_OVERRIDE_FIELDS)
@pytest.mark.parametrize('value, expected', [('  Client/2.0  ', 'Client/2.0'), (None, None), ('  ', None)])
def test_patch_feed_ua(app_client, feed_ua, value, expected, field, column):
    slug, db, headers = feed_ua
    db.update_podcast(slug, **{column: 'Previous/1.0'})
    response = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers,
                                json={field: value})
    assert response.status_code == 200
    assert response.json[field] == expected
    assert db.get_podcast_by_slug(slug)[column] == expected
    assert app_client.get(f'/api/v1/feeds/{slug}').json[field] == expected
    assert next(feed for feed in app_client.get('/api/v1/feeds').json['feeds']
                if feed['slug'] == slug)[field] == expected


@pytest.mark.parametrize('field, column', UA_OVERRIDE_FIELDS)
@pytest.mark.parametrize('value', [True, 12, [], 'Client/1.0\nInjected: yes', 'Client/1.0\tbad',
                                  'x' * (USER_AGENT_MAX_LENGTH + 1)])
def test_invalid_feed_ua_is_atomic(app_client, feed_ua, value, field, column):
    slug, db, headers = feed_ua
    db.update_podcast(slug, **{column: 'Previous/1.0'})
    response = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers,
                                json={field: value, 'languageOverride': 'de'})
    assert response.status_code == 400
    assert db.get_podcast_by_slug(slug)[column] == 'Previous/1.0'
    assert db.get_podcast_by_slug(slug)['language_override'] is None


def test_create_stores_download_ua_before_initial_refresh(app_client, feed_ua, monkeypatch):
    from api import feeds
    import main_app.feeds as refresh
    slug, db, headers = feed_ua
    created_slug = slug + '-created'
    monkeypatch.setattr(feeds, 'validate_url', lambda url: None)
    observed = []
    monkeypatch.setattr(refresh, 'refresh_rss_feed', lambda slug, url:
                        observed.append(db.get_podcast_by_slug(slug)['download_user_agent_override']))
    try:
        response = app_client.post('/api/v1/feeds', headers=headers, json={
            'sourceUrl': 'https://example.com/feed.xml', 'slug': created_slug,
            'downloadUserAgentOverride': 'Feed/2.0',
        })
        assert response.status_code == 201
        assert observed == ['Feed/2.0']
        assert db.get_podcast_by_slug(created_slug)['download_user_agent_override'] == 'Feed/2.0'
    finally:
        db.delete_podcast(created_slug)

"""Per-feed ad chapter override API surface.

Covers the two podcasts columns exposed by GET /feeds/{slug} and settable
through PATCH: ad_chapters_enabled_override and ad_chapter_categories_override.
"""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('ad_chapter_api_test_')


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'ad-chapter-api-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Ad Chapter API Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_get_feed_echoes_null_ad_chapter_overrides(app_client, seeded_feed):
    _authed(app_client)
    feed = app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}").get_json()
    assert feed['adChaptersEnabled'] is None
    assert feed['adChapterCategories'] is None


def test_feed_ad_chapter_overrides_round_trip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': 'on', 'adChapterCategories': {'sponsor': False}})
    assert r.status_code == 200, r.get_data(as_text=True)
    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChaptersEnabled'] == 'on'
    assert feed['adChapterCategories'] == {'sponsor': False}
    assert seeded_feed['db'].get_podcast_by_slug(slug)['ad_chapters_enabled_override'] == 'on'

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': None, 'adChapterCategories': None})
    assert r.status_code == 200, r.get_data(as_text=True)
    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChaptersEnabled'] is None
    assert feed['adChapterCategories'] is None


@pytest.mark.parametrize('payload', [
    {'adChaptersEnabled': 'maybe'},
    {'adChapterCategories': {'bogus': True}},
    {'adChapterCategories': {'sponsor': 1}},
    {'adChapterCategories': 'x'},
])
def test_feed_ad_chapter_overrides_validate(app_client, seeded_feed, payload):
    _authed(app_client)
    r = app_client.patch(f"/api/v1/feeds/{seeded_feed['slug']}", json=payload,
                         headers=_csrf_headers(app_client))
    assert r.status_code == 400, r.get_data(as_text=True)

"""Per-feed adChaptersEnabled/adChapterCategories compatibility fields.

Retired in favor of the 'mark' segment action (2.98.0,
mark_action_from_ad_chapters_v1): GET derives them from the feed's resolved
segment actions, PATCH translates them into the feed's segmentCategoryActions
override. See spec 1.4.
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


def test_get_feed_derives_ad_chapters_fields_from_resolved_actions(app_client, seeded_feed):
    _authed(app_client)
    feed = app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}").get_json()
    # Fresh feed: every category defaults to remove, so nothing is marked.
    assert feed['adChaptersEnabled'] is False
    assert feed['adChapterCategories'] == {
        cat: False for cat in feed['adChapterCategories']}


def test_patch_ad_chapter_categories_true_marks_a_keep_category(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'keep'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChapterCategories': {'sponsor': True}})
    assert r.status_code == 200, r.get_data(as_text=True)

    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChapterCategories']['sponsor'] is True
    assert feed['adChaptersEnabled'] is True
    assert seeded_feed['db'].get_podcast_by_slug(slug)['segment_category_actions'] == (
        '{"sponsor": "mark"}')


def test_patch_ad_chapter_categories_false_demotes_mark_to_keep(app_client, seeded_feed):
    """false demotes mark to keep and leaves other actions alone."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark', 'cross_promo': 'remove'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChapterCategories': {'sponsor': False}})
    assert r.status_code == 200, r.get_data(as_text=True)

    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChapterCategories']['sponsor'] is False
    override = seeded_feed['db'].get_podcast_by_slug(slug)['segment_category_actions']
    assert '"sponsor": "keep"' in override
    assert '"cross_promo": "remove"' in override


def test_patch_ad_chapter_categories_mixed_true_false_untouched(app_client, seeded_feed):
    """One payload: promote sponsor, demote cross_promo, leave recap alone."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {
            'sponsor': 'keep', 'cross_promo': 'mark', 'recap': 'remove'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChapterCategories': {
            'sponsor': True, 'cross_promo': False, 'recap': False}})
    assert r.status_code == 200, r.get_data(as_text=True)

    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChapterCategories']['sponsor'] is True
    assert feed['adChapterCategories']['cross_promo'] is False
    assert feed['adChapterCategories']['recap'] is False
    override = seeded_feed['db'].get_podcast_by_slug(slug)['segment_category_actions']
    assert '"sponsor": "mark"' in override
    assert '"cross_promo": "keep"' in override
    assert '"recap": "remove"' in override


def test_patch_ad_chapters_enabled_false_demotes_every_mark(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark', 'recap': 'mark'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': False})
    assert r.status_code == 200, r.get_data(as_text=True)

    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChaptersEnabled'] is False
    assert feed['adChapterCategories']['sponsor'] is False
    assert feed['adChapterCategories']['recap'] is False


def test_patch_ad_chapters_enabled_true_is_accepted_and_ignored(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': True})
    assert r.status_code == 200, r.get_data(as_text=True)

    feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert feed['adChapterCategories']['sponsor'] is True


def test_patch_ad_chapters_enabled_accepts_legacy_on_off_strings(app_client, seeded_feed):
    """The still-unmigrated feed panel may still send 'on'/'off'."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': 'on'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app_client.get(
        f'/api/v1/feeds/{slug}').get_json()['adChapterCategories']['sponsor'] is True

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': 'off'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app_client.get(
        f'/api/v1/feeds/{slug}').get_json()['adChapterCategories']['sponsor'] is False


def test_patch_ad_chapters_enabled_null_is_a_no_op(app_client, seeded_feed):
    """Matches the old per-feed override contract: null clears/ignores."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChaptersEnabled': None})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app_client.get(
        f'/api/v1/feeds/{slug}').get_json()['adChapterCategories']['sponsor'] is True


def test_patch_ad_chapter_categories_null_is_a_no_op(app_client, seeded_feed):
    """adChapterCategories: null must be tolerated like adChaptersEnabled: null."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'segmentCategoryActions': {'sponsor': 'mark'}})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = app_client.patch(f'/api/v1/feeds/{slug}', headers=headers, json={
        'adChapterCategories': None})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert app_client.get(
        f'/api/v1/feeds/{slug}').get_json()['adChapterCategories']['sponsor'] is True


@pytest.mark.parametrize('payload', [
    {'adChapterCategories': {'bogus': True}},
    {'adChapterCategories': {'sponsor': 1}},
    {'adChapterCategories': 'x'},
    {'adChaptersEnabled': 'maybe'},
    {'adChaptersEnabled': 1},
])
def test_feed_ad_chapter_categories_validate(app_client, seeded_feed, payload):
    _authed(app_client)
    r = app_client.patch(f"/api/v1/feeds/{seeded_feed['slug']}", json=payload,
                         headers=_csrf_headers(app_client))
    assert r.status_code == 400, r.get_data(as_text=True)

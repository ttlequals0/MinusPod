"""Local feed creation + metadata API (Task 6): POST /feeds branch,
PATCH extensions for local-only fields, and feed artwork upload.

Mirrors test_feeds_api.py's fixture style (shared app_client fixture from
conftest.py, local _authed/_csrf_headers helpers).
"""
import io
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='local-feed-api-test-'))

# Minimal but structurally valid 1x1 PNG (magic bytes + IHDR/IDAT/IEND).
_PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
    '3df40000000c4944415478da6360606060000000050001a5f6454000000000'
    '49454e44ae426082'
)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear in-memory flask-limiter counters before each test (mirrors
    tests/integration/conftest.py). This module's POST /feeds tests alone
    exceed add_feed's "3 per minute" limit within a single run without this.
    """
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _align_main_app_singletons(app_client):
    """Many test modules across the suite reset Database._instance /
    Storage._instance at import time for isolation (tests/app_bootstrap.py).
    main_app.feeds and local_feed_builder bind `db`/`storage` module-level
    names once, at whichever moment main_app itself first got imported
    during collection -- so by the time this module's tests run, those
    frozen references can point at a stale singleton, different from
    whatever api.get_database()/get_storage() currently return. The local
    feed rebuild path (PATCH -> refresh_rss_feed -> rebuild_local_feed, and
    POST -> local_feed_builder.rebuild_local_feed) goes through those frozen
    references, so without this alignment it silently no-ops against an
    empty database instead of touching the row the API layer just wrote.
    """
    import main_app.feeds as mf
    import local_feed_builder as lfb
    from api import get_database, get_storage

    db, storage = get_database(), get_storage()
    orig = (mf.db, mf.storage, lfb.db, lfb.storage)
    mf.db, mf.storage = db, storage
    lfb.db, lfb.storage = db, storage
    yield
    mf.db, mf.storage, lfb.db, lfb.storage = orig


@pytest.fixture
def subscribed_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'local-feed-api-subscribed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Subscribed Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


@pytest.fixture
def local_feed(app_client):
    from api import get_database
    from utils.feed_guid import compute_feed_guid
    db = get_database()
    slug = 'local-feed-api-local'
    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    # Seed the same minted p20_channel_json _add_local_feed produces at
    # creation, since this fixture bypasses the POST endpoint -- PATCH p20
    # tests need a real guid already in place to assert it survives.
    db.update_podcast(slug, p20_channel_json=json.dumps({
        'guid': compute_feed_guid(f'http://localhost:8000/{slug}'),
        'medium': 'podcast',
        'locked': 'yes',
    }))
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


# -- POST /feeds (feedType: local) --

def test_post_local_feed_happy_path(app_client):
    from api import get_database, get_storage
    from utils.feed_guid import compute_feed_guid

    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post('/api/v1/feeds', json={
        'feedType': 'local',
        'title': 'My Archive Show',
    }, headers=headers)

    assert resp.status_code == 201
    body = resp.get_json()
    assert body['feedType'] == 'local'
    slug = body['slug']
    assert slug

    db = get_database()
    try:
        podcast = db.get_podcast_by_slug(slug)
        assert podcast is not None
        assert podcast['feed_type'] == 'local'
        assert podcast['source_url'] == f'local://{slug}'

        channel_json = json.loads(podcast['p20_channel_json'])
        assert channel_json['guid'] == compute_feed_guid(body['feedUrl'])
        assert channel_json['medium'] == 'podcast'
        assert channel_json['locked'] == 'yes'

        storage = get_storage()
        assert storage.get_rss(slug) is not None
    finally:
        db.delete_podcast(slug)


def test_post_local_feed_without_title_400(app_client):
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post('/api/v1/feeds', json={'feedType': 'local'}, headers=headers)
    assert resp.status_code == 400


def test_post_local_feed_invalid_slug_400(app_client):
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post('/api/v1/feeds', json={
        'feedType': 'local',
        'title': 'Whatever',
        'slug': '../etc/passwd',
    }, headers=headers)
    assert resp.status_code == 400


# -- PATCH /feeds/<slug> local-only fields --

def test_patch_local_only_fields_on_local_feed_rebuilds_rss(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    with patch('main_app.feeds.rebuild_local_feed') as mock_rebuild:
        resp = app_client.patch(f'/api/v1/feeds/{slug}', json={
            'author': 'New Author',
            'categories': ['Comedy', 'News'],
        }, headers=headers)

    assert resp.status_code == 200
    assert mock_rebuild.called

    podcast = local_feed['db'].get_podcast_by_slug(slug)
    assert podcast['author'] == 'New Author'
    assert json.loads(podcast['categories']) == ['Comedy', 'News']


def test_patch_author_on_subscribed_feed_400(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'author': 'Nope'}, headers=headers)
    assert resp.status_code == 400
    assert 'only editable on local feeds' in resp.get_json()['error']


# -- POST /feeds/<slug>/artwork --

def test_artwork_upload_happy_path(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/artwork',
        data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'artworkUrl' in body


def test_artwork_upload_wrong_mime_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/artwork',
        data={'file': (io.BytesIO(b'not an image'), 'cover.txt')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


def test_artwork_upload_on_subscribed_feed_400(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/artwork',
        data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


# -- GET /feeds list serialization --

def test_feed_type_present_in_list_serialization(app_client, subscribed_feed, local_feed):
    _authed(app_client)

    resp = app_client.get('/api/v1/feeds')
    assert resp.status_code == 200
    feeds_by_slug = {f['slug']: f for f in resp.get_json()['feeds']}
    assert feeds_by_slug[subscribed_feed['slug']]['feedType'] == 'subscribed'
    assert feeds_by_slug[local_feed['slug']]['feedType'] == 'local'


# -- _validate_p20 / _validate_p20_items (direct unit coverage) --

def test_validate_p20_funding_person_happy_path():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({
        'funding': [{'text': 'Support us', 'url': 'https://example.com/donate'}],
        'person': [{'text': 'Jane Host', 'role': 'host'}],
    })
    assert err is None
    assert cleaned == {
        'funding': [{'text': 'Support us', 'url': 'https://example.com/donate'}],
        'person': [{'text': 'Jane Host', 'role': 'host'}],
    }


def test_validate_p20_rejects_non_http_url():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({
        'funding': [{'text': 'Support us', 'url': 'javascript:alert(1)'}],
    })
    assert cleaned is None
    assert 'http://' in err and 'https://' in err


def test_validate_p20_funding_requires_url():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({'funding': [{'text': 'Support us'}]})
    assert cleaned is None
    assert 'requires a url' in err


def test_validate_p20_person_requires_text():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({'person': [{'role': 'host'}]})
    assert cleaned is None
    assert 'requires a name' in err


def test_validate_p20_unknown_tag_rejected():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({'soundbite': []})
    assert cleaned is None
    assert "unknown tag" in err


def test_validate_p20_guid_rejected():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({'guid': 'some-guid'})
    assert cleaned is None
    assert 'immutable' in err


def test_validate_p20_medium_whitelist():
    from api.feeds import _validate_p20

    for value in ('podcast', 'music', 'video', 'film', 'audiobook',
                  'newsletter', 'blog'):
        cleaned, err = _validate_p20({'medium': value})
        assert err is None
        assert cleaned == {'medium': value}

    cleaned, err = _validate_p20({'medium': 'spaghetti'})
    assert cleaned is None
    assert 'medium must be one of' in err


def test_validate_p20_locked_values():
    from api.feeds import _validate_p20

    for value in ('yes', 'no'):
        cleaned, err = _validate_p20({'locked': value})
        assert err is None
        assert cleaned == {'locked': value}

    cleaned, err = _validate_p20({'locked': 'maybe'})
    assert cleaned is None
    assert "must be 'yes' or 'no'" in err


def test_validate_p20_locked_owner_email_shape():
    from api.feeds import _validate_p20

    cleaned, err = _validate_p20({'locked': 'yes', 'locked_owner': 'owner@example.com'})
    assert err is None
    assert cleaned == {'locked': 'yes', 'locked_owner': 'owner@example.com'}

    cleaned, err = _validate_p20({'locked_owner': 'not-an-email'})
    assert cleaned is None
    assert 'email address' in err

    # blank/whitespace-only clears rather than storing an empty string
    cleaned, err = _validate_p20({'locked_owner': '   '})
    assert err is None
    assert cleaned == {}


# -- PATCH /feeds/<slug> p20 (funding/person round-trip, null clearing,
#    medium/locked/locked_owner) --

def test_patch_p20_funding_person_round_trips(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={
        'p20': {
            'funding': [{'text': 'Support us', 'url': 'https://example.com/donate'}],
            'person': [{'text': 'Jane Host', 'role': 'host'}],
        },
    }, headers=headers)
    assert resp.status_code == 200

    channel_json = json.loads(local_feed['db'].get_podcast_by_slug(slug)['p20_channel_json'])
    assert channel_json['funding'] == [{'text': 'Support us', 'url': 'https://example.com/donate'}]
    assert channel_json['person'] == [{'text': 'Jane Host', 'role': 'host'}]
    # Minted at creation, must survive an unrelated p20 PATCH.
    assert channel_json['guid']
    assert channel_json['medium'] == 'podcast'
    assert channel_json['locked'] == 'yes'


def test_patch_p20_null_clears_tags_preserves_scalars(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={
        'p20': {'funding': [{'text': 'Support us', 'url': 'https://example.com/donate'}]},
    }, headers=headers)
    before = json.loads(db.get_podcast_by_slug(slug)['p20_channel_json'])
    assert before['funding']

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'p20': None}, headers=headers)
    assert resp.status_code == 200

    after = json.loads(db.get_podcast_by_slug(slug)['p20_channel_json'])
    assert 'funding' not in after
    assert after['guid'] == before['guid']
    assert after['medium'] == before['medium']
    assert after['locked'] == before['locked']


def test_patch_p20_per_tag_clear_still_works(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={
        'p20': {'funding': [{'text': 'Support us', 'url': 'https://example.com/donate'}]},
    }, headers=headers)

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'p20': {'funding': []}}, headers=headers)
    assert resp.status_code == 200
    channel_json = json.loads(db.get_podcast_by_slug(slug)['p20_channel_json'])
    assert channel_json['funding'] == []
    assert channel_json['guid']


def test_patch_p20_medium_and_locked_with_owner(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={
        'p20': {'medium': 'music', 'locked': 'no', 'locked_owner': 'owner@example.com'},
    }, headers=headers)
    assert resp.status_code == 200

    channel_json = json.loads(local_feed['db'].get_podcast_by_slug(slug)['p20_channel_json'])
    assert channel_json['medium'] == 'music'
    assert channel_json['locked'] == 'no'
    assert channel_json['locked_owner'] == 'owner@example.com'
    assert channel_json['guid']


def test_patch_p20_invalid_medium_rejected(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'p20': {'medium': 'bogus'}}, headers=headers)
    assert resp.status_code == 400


def test_patch_p20_guid_rejected(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'p20': {'guid': 'hijacked'}}, headers=headers)
    assert resp.status_code == 400

    channel_json = json.loads(local_feed['db'].get_podcast_by_slug(slug)['p20_channel_json'])
    assert channel_json['guid'] != 'hijacked'


def test_post_local_feed_p20_medium_locked_override_minted_defaults(app_client):
    from api import get_database

    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post('/api/v1/feeds', json={
        'feedType': 'local',
        'title': 'Locked Down Show',
        'p20': {'medium': 'audiobook', 'locked': 'no', 'locked_owner': 'owner@example.com'},
    }, headers=headers)
    assert resp.status_code == 201
    slug = resp.get_json()['slug']

    db = get_database()
    try:
        channel_json = json.loads(db.get_podcast_by_slug(slug)['p20_channel_json'])
        assert channel_json['medium'] == 'audiobook'
        assert channel_json['locked'] == 'no'
        assert channel_json['locked_owner'] == 'owner@example.com'
        # guid is still the minted one -- a client can never set it, even
        # implicitly by omission changing the merge order.
        assert channel_json['guid']
    finally:
        db.delete_podcast(slug)

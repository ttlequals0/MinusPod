"""Recents feed API (#721)."""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recents_api_test_')

from main_app import app  # noqa: E402
import io  # noqa: E402

import database  # noqa: E402
from database.podcasts import RECENTS_SLUG  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    # POST /feeds is rate limited; clear the in-memory counters per test.
    from api import limiter
    limiter.reset()
    # The rebuild path reads through module-level db/storage names bound at
    # import; point them at the singletons the API layer writes to (see
    # test_local_feed_api._align_main_app_singletons).
    import main_app.feeds as mf
    import local_feed_builder as lfb
    import recents_feed as rf
    from api import get_database, get_storage
    db, storage = get_database(), get_storage()
    orig = (mf.db, mf.storage, lfb.db, lfb.storage, rf.db, rf.storage)
    mf.db, mf.storage, lfb.db, lfb.storage, rf.db, rf.storage = db, storage, db, storage, db, storage
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        c.get('/api/v1/auth/status')
        yield c
    mf.db, mf.storage, lfb.db, lfb.storage, rf.db, rf.storage = orig
    for slug in (RECENTS_SLUG, 'alpha'):
        db.delete_podcast(slug)


def _seed_alpha_episode(client):
    """A source feed with one processed episode published after the
    (backdated) recents row was created."""
    db = database.Database()
    db.create_podcast('alpha', 'https://example.com/alpha.xml', 'Alpha')
    _create_ok(client)
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET created_at = '2026-09-01T00:00:00Z' WHERE slug = ?", (RECENTS_SLUG,))
    conn.commit()
    db.upsert_episode('alpha', 'aaaaaaaaaaa1', original_url='u', title='t', status='processed',
                      published_at='2026-09-10T00:00:00Z', processed_file='/a.mp3')
    return db


def _csrf(client):
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _create(client, **body):
    return client.post('/api/v1/feeds', json={'feedType': 'recents', **body}, headers=_csrf(client))


def _create_ok(client, **body):
    resp = _create(client, **body)
    assert resp.status_code == 201, resp.data
    return resp


def test_create_returns_the_fixed_slug_and_defaults(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.data
    body = resp.get_json()
    assert body['slug'] == RECENTS_SLUG and body['feedType'] == 'recents'
    feed = client.get(f'/api/v1/feeds/{RECENTS_SLUG}').get_json()
    assert feed['title'] == 'Recents' and feed['feedType'] == 'recents'
    assert feed['hasArtwork'] is True
    assert client.get(f'/api/v1/feeds/{RECENTS_SLUG}/artwork').mimetype == 'image/png'


def test_second_create_is_409(client):
    assert _create(client, title='One').status_code == 201
    assert _create(client, title='Two').status_code == 409


def test_patch_allows_title_and_description_only(client):
    _create_ok(client)
    ok = client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'title': 'New', 'description': 'd'},
                      headers=_csrf(client))
    assert ok.status_code == 200, ok.data
    assert ok.get_json()['title'] == 'New'
    bad = client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'chaptersMode': 'off'}, headers=_csrf(client))
    assert bad.status_code == 400


def test_counts_come_from_membership(client):
    _seed_alpha_episode(client)
    feed = client.get(f'/api/v1/feeds/{RECENTS_SLUG}').get_json()
    assert feed['episodeCount'] == 1 and feed['processedCount'] == 1


def test_edit_re_renders_the_served_feed(client):
    from api import get_storage
    _create_ok(client)
    client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'title': 'Again'}, headers=_csrf(client))
    assert '<title>Again</title>' in get_storage().get_rss(RECENTS_SLUG)


def test_artwork_upload_keeps_the_served_items(client):
    from api import get_storage
    _seed_alpha_episode(client)
    client.patch(f'/api/v1/feeds/{RECENTS_SLUG}', json={'title': 'Mine'}, headers=_csrf(client))
    assert 'aaaaaaaaaaa1' in get_storage().get_rss(RECENTS_SLUG)
    png = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
                        '3df40000000c4944415478da6360606060000000050001a5f6454000000000'
                        '49454e44ae426082')
    import io
    resp = client.post(f'/api/v1/feeds/{RECENTS_SLUG}/artwork', headers=_csrf(client),
                       data={'file': (io.BytesIO(png), 'logo.png')}, content_type='multipart/form-data')
    assert resp.status_code == 200, resp.data
    assert 'aaaaaaaaaaa1' in get_storage().get_rss(RECENTS_SLUG)


def test_delete_removes_the_row(client):
    _create_ok(client)
    assert client.delete(f'/api/v1/feeds/{RECENTS_SLUG}', headers=_csrf(client)).status_code == 200
    assert client.get(f'/api/v1/feeds/{RECENTS_SLUG}').status_code == 404


def test_opml_includes_the_recents_feed_only_in_modified_mode(client):
    from utils.opml import build_opml_xml
    _create(client)
    podcasts = database.Database().get_all_podcasts()
    modified = build_opml_xml(podcasts, 'modified', 'https://mp.example.com', None)
    assert 'https://mp.example.com/recents' in modified
    original = build_opml_xml(podcasts, 'original', 'https://mp.example.com', None)
    assert '/recents' not in original


def test_episode_list_for_recents_carries_the_source_slug(client):
    _seed_alpha_episode(client)
    body = client.get(f'/api/v1/feeds/{RECENTS_SLUG}/episodes?limit=10').get_json()
    assert body['total'] == 1
    assert body['episodes'][0]['feedSlug'] == 'alpha'
    assert body['episodes'][0]['feedTitle'] == 'Alpha'
    assert body['episodes'][0]['id'] == 'aaaaaaaaaaa1'


def test_other_feeds_cannot_take_the_recents_slug(client):
    resp = client.post('/api/v1/feeds', json={'feedType': 'local', 'title': 'Recents'}, headers=_csrf(client))
    assert resp.status_code == 400
    assert 'reserved' in resp.get_json()['error']


def test_title_must_be_a_string(client):
    resp = _create(client, title=5)
    assert resp.status_code == 400


def test_recents_has_no_upstream_to_refresh(client):
    _create_ok(client)
    resp = client.post(f'/api/v1/feeds/{RECENTS_SLUG}/refresh', headers=_csrf(client))
    assert resp.status_code == 400


def test_status_filter_other_than_processed_is_empty(client):
    _seed_alpha_episode(client)
    ok = client.get(f'/api/v1/feeds/{RECENTS_SLUG}/episodes?status=processed').get_json()
    assert ok['total'] == 1
    failed = client.get(f'/api/v1/feeds/{RECENTS_SLUG}/episodes?status=failed').get_json()
    assert failed['total'] == 0 and failed['episodes'] == []


def test_deleting_a_source_feed_re_renders_the_served_feed(client):
    from api import get_storage
    from recents_feed import rebuild_recents_feed
    _seed_alpha_episode(client)
    rebuild_recents_feed()
    assert 'aaaaaaaaaaa1' in get_storage().get_rss(RECENTS_SLUG)
    resp = client.delete('/api/v1/feeds/alpha', headers=_csrf(client))
    assert resp.status_code == 200, resp.data
    assert 'aaaaaaaaaaa1' not in get_storage().get_rss(RECENTS_SLUG)


def test_opml_import_cannot_take_the_recents_slug(client):
    opml = ('<opml version="2.0"><body><outline type="rss" text="Recents" title="Recents" '
            'xmlUrl="https://example.com/recents.xml"/></body></opml>')
    resp = client.post('/api/v1/feeds/import-opml', data={'opml': (io.BytesIO(opml.encode()), 'f.opml')},
                       content_type='multipart/form-data', headers=_csrf(client))
    assert resp.status_code in (200, 207), resp.data
    assert database.Database().get_podcast_by_slug(RECENTS_SLUG) is None

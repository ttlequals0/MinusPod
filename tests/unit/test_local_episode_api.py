"""Local episode upload, edit, bulk-edit, delete, and artwork APIs (Task 8).

Mirrors test_local_feed_api.py's fixture style (shared app_client fixture
from conftest.py, local _authed/_csrf_headers helpers, main_app singleton
alignment).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='local-episode-api-test-'))

requires_ffmpeg = pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg/ffprobe not available')


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
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _align_main_app_singletons(app_client):
    """See test_local_feed_api.py's identical fixture for the full
    explanation: main_app.feeds / local_feed_builder freeze module-level
    db/storage references at import time that can go stale relative to
    api.get_database()/get_storage() by the time this module's tests run.
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
    slug = 'local-episode-api-subscribed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Subscribed Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


@pytest.fixture
def local_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'local-episode-api-local'
    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


@pytest.fixture(scope='session')
def real_mp3_bytes(tmp_path_factory):
    """A small, structurally valid mp3 -- generated once per test session
    (per the task brief) since ffmpeg invocation is comparatively slow."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    mp3_path = tmp_path_factory.mktemp('audio') / 'fixture.mp3'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
         '-t', '1', '-q:a', '9', str(mp3_path)],
        check=True, capture_output=True,
    )
    return mp3_path.read_bytes()


def _seed_episode(db, slug, episode_id='s01e01', **kwargs):
    defaults = dict(
        title=f'Episode {episode_id}', status='discovered',
        original_url=f'local://{episode_id}',
        published_at='2026-01-01T00:00:00Z',
        season_number=1, episode_number=int(episode_id[-2:]),
    )
    defaults.update(kwargs)
    db.upsert_episode(slug, episode_id, **defaults)
    return db.get_episode(slug, episode_id)


# -- POST /feeds/<slug>/episodes (single upload) --

@requires_ffmpeg
def test_upload_happy_path_into_empty_feed(app_client, local_feed, real_mp3_bytes):
    from api import get_storage
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    with patch('database.queue.QueueMixin.queue_episode_for_processing') as mock_queue:
        resp = app_client.post(
            f'/api/v1/feeds/{slug}/episodes',
            data={
                'audio': (io.BytesIO(real_mp3_bytes), 'ep1.mp3'),
                'title': 'First Episode',
            },
            headers=headers,
            content_type='multipart/form-data',
        )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body['episodeId'] == 's01e01'
    assert body['status'] == 'discovered'
    assert body['queued'] is False
    mock_queue.assert_not_called()

    episode = local_feed['db'].get_episode(slug, 's01e01')
    assert episode is not None
    assert episode['status'] == 'discovered'
    assert episode['season_number'] == 1
    assert episode['episode_number'] == 1

    storage = get_storage()
    original_path = storage.get_original_path(slug, 's01e01')
    assert original_path.exists()


@requires_ffmpeg
def test_upload_into_nonempty_feed_with_auto_process_queues(app_client, local_feed, real_mp3_bytes):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    with patch('database.queue.QueueMixin.queue_episode_for_processing') as mock_queue:
        mock_queue.return_value = 123
        resp = app_client.post(
            f'/api/v1/feeds/{slug}/episodes',
            data={'audio': (io.BytesIO(real_mp3_bytes), 'ep2.mp3')},
            headers=headers,
            content_type='multipart/form-data',
        )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body['episodeId'] == 's01e02'
    assert body['queued'] is True
    # No recency filtering: queued regardless of the seeded episode's
    # published_at (2026-01-01, long outside any "fresh" window).
    mock_queue.assert_called_once()
    call_args = mock_queue.call_args
    assert call_args.args[0] == slug
    assert call_args.args[1] == 's01e02'


@requires_ffmpeg
def test_upload_duplicate_id_409(app_client, local_feed, real_mp3_bytes):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={
            'audio': (io.BytesIO(real_mp3_bytes), 'ep.mp3'),
            'season': '1',
            'episode': '1',
        },
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 409


def test_upload_non_mp3_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(b'not audio'), 'ep.wav')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


def test_upload_missing_audio_field_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'title': 'No audio here'},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


# -- PATCH /feeds/<slug>/episodes/<episode_id> (single edit) --

def test_patch_single_episode_happy_path(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={
            'title': 'Renamed',
            'description': 'New description',
            'season': 2,
            'episode': 5,
            'publishedAt': '2026-02-01T12:00:00+00:00',
        },
        headers=headers,
    )
    assert resp.status_code == 200

    episode = db.get_episode(slug, 's01e01')
    assert episode['title'] == 'Renamed'
    assert episode['description'] == 'New description'
    # season/episode -> season_number/episode_number; episode_id unchanged.
    assert episode['season_number'] == 2
    assert episode['episode_number'] == 5
    assert episode['episode_id'] == 's01e01'
    assert episode['published_at'] == '2026-02-01T12:00:00Z'


def test_patch_single_episode_p20_person_location(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={'p20': {'person': [{'text': 'Jane Guest', 'role': 'guest'}]}},
        headers=headers,
    )
    assert resp.status_code == 200

    episode = db.get_episode(slug, 's01e01')
    p20 = json.loads(episode['p20_item_json'])
    assert p20 == {'person': [{'text': 'Jane Guest', 'role': 'guest'}]}


def test_patch_single_episode_p20_unknown_tag_400(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={'p20': {'funding': [{'text': 'x', 'url': 'https://example.com'}]}},
        headers=headers,
    )
    assert resp.status_code == 400


def test_patch_single_episode_not_found_404(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s09e09',
        json={'title': 'Nope'},
        headers=headers,
    )
    assert resp.status_code == 404


# -- PATCH /feeds/<slug>/episodes (bulk edit) --

def test_bulk_patch_happy_path(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _seed_episode(db, slug, 's01e02')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes',
        json=[
            {'episodeId': 's01e01', 'title': 'Bulk One'},
            {'episodeId': 's01e02', 'title': 'Bulk Two'},
        ],
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.get_json() == {'updated': 2}

    assert db.get_episode(slug, 's01e01')['title'] == 'Bulk One'
    assert db.get_episode(slug, 's01e02')['title'] == 'Bulk Two'


def test_bulk_patch_atomicity_one_invalid_entry_applies_none(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01', title='Original One')
    _seed_episode(db, slug, 's01e02', title='Original Two')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes',
        json=[
            {'episodeId': 's01e01', 'title': 'Should Not Apply'},
            {'episodeId': 's01e02', 'season': 'not-an-int'},
        ],
        headers=headers,
    )
    assert resp.status_code == 400

    # Zero rows changed: the first (valid) entry must not have been applied
    # even though it was validated and ordered before the invalid one.
    assert db.get_episode(slug, 's01e01')['title'] == 'Original One'
    assert db.get_episode(slug, 's01e02')['title'] == 'Original Two'


def test_bulk_patch_max_500_exceeded_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes',
        json=[{'episodeId': f's01e{i:02d}', 'title': 'x'} for i in range(1, 502)],
        headers=headers,
    )
    assert resp.status_code == 400


# -- DELETE /feeds/<slug>/episodes/<episode_id> --

def test_delete_removes_row_and_files(app_client, local_feed):
    from api import get_storage
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    storage = get_storage()
    original_path = storage.get_original_path(slug, 's01e01')
    original_path.write_bytes(b'fake-mp3-bytes')
    assert original_path.exists()

    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s01e01', headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['deleted'] == 1

    assert db.get_episode(slug, 's01e01') is None
    assert not original_path.exists()


def test_delete_not_found_404(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s09e09', headers=headers)
    assert resp.status_code == 404


# -- POST /feeds/<slug>/episodes/<episode_id>/artwork --

_PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
    '3df40000000c4944415478da6360606060000000050001a5f6454000000000'
    '49454e44ae426082'
)


def test_episode_artwork_upload_happy_path(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/s01e01/artwork',
        data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 200

    from api import get_storage
    assert get_storage().has_episode_artwork(slug, 's01e01')


# -- All five endpoints 400 on a subscribed feed --

def test_upload_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(b'x'), 'ep.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


def test_patch_single_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={'title': 'Nope'},
        headers=headers,
    )
    assert resp.status_code == 400


def test_bulk_patch_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes',
        json=[{'episodeId': 's01e01', 'title': 'Nope'}],
        headers=headers,
    )
    assert resp.status_code == 400


def test_delete_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s01e01', headers=headers)
    assert resp.status_code == 400


def test_artwork_upload_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/s01e01/artwork',
        data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400

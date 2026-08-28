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
from pathlib import Path
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
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'local-episode-api-local'
    # The slug (and its on-disk artwork/audio) is reused across every test
    # in this module -- clear anything a previous test left behind so a
    # cached cover from one test can't bleed into another's "no artwork"
    # assertion.
    storage.cleanup_podcast_dir(slug)
    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)
    storage.cleanup_podcast_dir(slug)


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


@pytest.fixture(scope='session')
def chaptered_mp3_bytes(tmp_path_factory):
    """An mp3 with two embedded ID3v2 chapters (ffmetadata -> -map_chapters),
    generated once per session -- mirrors
    test_embedded_chapters.py::TestProbeChaptersIntegration's fixture build."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    tmp_dir = tmp_path_factory.mktemp('chaptered_audio')
    meta = tmp_dir / 'chap.ffmeta'
    meta.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=One\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\ntitle=Two\n"
    )
    mp3_path = tmp_dir / 'chaptered.mp3'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono:d=1',
         '-i', str(meta), '-map_metadata', '1', '-map_chapters', '1',
         '-shortest', '-q:a', '9', str(mp3_path)],
        check=True, capture_output=True, timeout=30,
    )
    return mp3_path.read_bytes()


@pytest.fixture(scope='session')
def artwork_embedded_mp3_bytes(tmp_path_factory):
    """An mp3 with an embedded (ID3 APIC) cover image, generated once per
    session -- mirrors chaptered_mp3_bytes's ffmpeg fixture pattern above."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    tmp_dir = tmp_path_factory.mktemp('artwork_audio')
    cover_path = tmp_dir / 'cover.jpg'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=16x16',
         '-frames:v', '1', str(cover_path)],
        check=True, capture_output=True, timeout=30,
    )
    mp3_path = tmp_dir / 'with_cover.mp3'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono:d=1',
         '-i', str(cover_path), '-map', '0:a', '-map', '1:v',
         '-c:a', 'libmp3lame', '-q:a', '9', '-c:v', 'copy',
         '-id3v2_version', '3', '-disposition:v', 'attached_pic',
         str(mp3_path)],
        check=True, capture_output=True, timeout=30,
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
def test_upload_sets_original_file_and_serves_original_audio(app_client, local_feed, real_mp3_bytes):
    """Report bug 1: original_file must be set on upload so hasOriginalAudio
    is true and GET .../original.mp3 200s -- not left null until the first
    processing run happens to write the column."""
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(real_mp3_bytes), 'ep.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    episode_id = resp.get_json()['episodeId']

    detail = app_client.get(f'/api/v1/feeds/{slug}/episodes/{episode_id}')
    assert detail.get_json()['hasOriginalAudio'] is True

    original_resp = app_client.get(
        f'/api/v1/feeds/{slug}/episodes/{episode_id}/original.mp3')
    assert original_resp.status_code == 200


@requires_ffmpeg
def test_upload_defaults_title_when_missing(app_client, local_feed, real_mp3_bytes):
    """Report bug 4: no title falls back to 'Episode {n}', matching the
    import path's fallback -- not an empty <title></title>."""
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(real_mp3_bytes), 'ep.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    assert resp.get_json()['title'] == 'Episode 1'


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


@requires_ffmpeg
def test_upload_persists_embedded_chapters(app_client, local_feed, chaptered_mp3_bytes):
    """Critical fix: chapters must be stored even though upsert_episode
    used to run AFTER save_chapters_json (which needs the episode row to
    already exist -- save_episode_details raises ValueError otherwise,
    silently swallowed to a warning by the storage layer)."""
    slug = local_feed['slug']
    db = local_feed['db']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(chaptered_mp3_bytes), 'chaptered.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    episode_id = resp.get_json()['episodeId']

    episode = db.get_episode(slug, episode_id)
    assert episode['chapters_json'] is not None
    chapters = json.loads(episode['chapters_json'])
    assert chapters['version'] == '1.2.0'
    assert [c['title'] for c in chapters['chapters']] == ['One', 'Two']


@requires_ffmpeg
def test_upload_extracts_embedded_artwork_when_none_supplied(
        app_client, local_feed, artwork_embedded_mp3_bytes):
    """Report bug 5: with no 'artwork' field, upload_local_episode falls
    back to the audio's own embedded cover art, the same way the import
    path's _commit_entry already does."""
    from api import get_storage
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(artwork_embedded_mp3_bytes), 'ep.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    episode_id = resp.get_json()['episodeId']
    assert get_storage().has_episode_artwork(slug, episode_id)


@requires_ffmpeg
def test_upload_tempfile_created_in_same_dir_as_final_path(app_client, local_feed, real_mp3_bytes):
    """The tmp file must live next to the final original-audio path so the
    move is a same-filesystem rename, not a cross-device copy (relevant
    once the upload route's request cap is widened to 1 GB)."""
    from api import get_storage
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    storage = get_storage()
    expected_final_path = storage.get_original_path(slug, 's01e01')
    expected_dir = expected_final_path.parent

    # patch('...shutil.move') patches the single shared `shutil` module
    # object, so this also intercepts storage.save_rss's own shutil.move
    # (invoked via rebuild_local_feed at the end of the request) -- record
    # every call and pick out the one that produced the audio file rather
    # than assuming the audio move is the only (or the last) call.
    real_move = shutil.move
    calls = []

    def _spy_move(src, dst, *a, **kw):
        calls.append((str(Path(src).parent), str(dst)))
        return real_move(src, dst, *a, **kw)

    with patch('api.local_episodes.shutil.move', side_effect=_spy_move):
        resp = app_client.post(
            f'/api/v1/feeds/{slug}/episodes',
            data={'audio': (io.BytesIO(real_mp3_bytes), 'ep.mp3')},
            headers=headers,
            content_type='multipart/form-data',
        )
    assert resp.status_code == 201

    audio_calls = [src_parent for src_parent, dst in calls if dst == str(expected_final_path)]
    assert audio_calls == [str(expected_dir)]


def test_upload_artwork_field_capped_before_full_read(app_client, local_feed, monkeypatch):
    """A malicious/oversized artwork part must 400 without ever buffering
    more than the cap, even though the route's overall request cap is
    widened to 1 GB for the audio field."""
    import api.local_episodes as local_episodes_mod
    monkeypatch.setattr(local_episodes_mod, 'MAX_EPISODE_ARTWORK_BYTES', 100)

    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    oversized = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200  # > 100-byte test cap
    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={
            'audio': (io.BytesIO(b'not-real-audio-bytes'), 'ep.mp3'),
            'artwork': (io.BytesIO(oversized), 'cover.png'),
        },
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400
    assert 'smaller' in resp.get_json()['error']

    # Bailed out before any DB row was written.
    from api import get_database
    assert get_database().get_episode(slug, 's01e01') is None


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


def test_patch_single_episode_unknown_key_400_names_key(app_client, local_feed):
    """Report smaller item 2: an unknown PATCH key must fail closed (400
    naming the key), matching the JSON sidecar's fail-closed behavior --
    not a silent 200 no-op that hides a typo like 'publishedat'."""
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01', published_at='2020-01-01T00:00:00Z')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={'publishedat': '2026-01-01T00:00:00Z'},
        headers=headers,
    )
    assert resp.status_code == 400
    assert 'publishedat' in resp.get_json()['error']
    # No silent no-op: nothing changed.
    assert db.get_episode(slug, 's01e01')['published_at'] == '2020-01-01T00:00:00Z'


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


# -- GET /feeds/<slug>/episodes/<episode_id> (detail) --

def test_get_episode_detail_includes_season_and_episode_numbers(app_client, local_feed):
    """The detail GET (api/episodes.py's get_episode) must echo season/
    episode numbers, matching what upload/PATCH already return -- without
    this, EpisodeDetail.tsx's edit form falls back to parsing them out of
    the episode id, which is stale after any season/episode edit (the id is
    minted once at upload and never renamed) (#625 Task 13 review finding 1).
    """
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01', season_number=1, episode_number=1)
    _authed(app_client)
    headers = _csrf_headers(app_client)

    # Edit season/episode without touching the id (episode_id is never
    # renamed by a PATCH -- see test_patch_single_episode_happy_path).
    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes/s01e01',
        json={'season': 2, 'episode': 5},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/s01e01')
    assert resp.status_code == 200
    body = resp.get_json()
    # Parsing the id itself would (wrongly) read season=1/episode=1.
    assert body['seasonNumber'] == 2
    assert body['episodeNumber'] == 5


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


def test_bulk_patch_unknown_key_400_names_key(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01', title='Original')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(
        f'/api/v1/feeds/{slug}/episodes',
        json=[{'episodeId': 's01e01', 'titel': 'Typo'}],
        headers=headers,
    )
    assert resp.status_code == 400
    assert 'titel' in resp.get_json()['error']
    assert db.get_episode(slug, 's01e01')['title'] == 'Original'


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


def test_delete_409_when_episode_is_processing(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    with patch('api.local_episodes.ProcessingQueue') as mock_pq_cls:
        mock_pq_cls.return_value.is_processing.return_value = True
        resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s01e01', headers=headers)

    assert resp.status_code == 409
    # Nothing was touched: the row (and its files, if any) must survive.
    assert db.get_episode(slug, 's01e01') is not None


@requires_ffmpeg
def test_delete_removes_queue_row_and_reupload_queues_again(app_client, local_feed, real_mp3_bytes):
    """Important fix: delete_episode_rows must also drop the
    auto_process_queue row. Left behind, it (a) lets the background queue
    processor resurrect a deleted episode and (b) silently blocks a future
    re-upload of the same id from queuing at all, since the queue table's
    UNIQUE(podcast_id, episode_id) + ON CONFLICT DO NOTHING no-ops the
    insert against the stale row."""
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')  # feed non-empty -> next upload queues
    _authed(app_client)
    headers = _csrf_headers(app_client)

    def _queue_row_count(episode_id):
        podcast = db.get_podcast_by_slug(slug)
        conn = db.get_connection()
        cur = conn.execute(
            "SELECT COUNT(*) FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?",
            (podcast['id'], episode_id),
        )
        return cur.fetchone()[0]

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(real_mp3_bytes), 'ep2.mp3')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    assert resp.get_json()['episodeId'] == 's01e02'
    assert resp.get_json()['queued'] is True
    assert _queue_row_count('s01e02') == 1

    del_resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s01e02', headers=headers)
    assert del_resp.status_code == 200
    assert _queue_row_count('s01e02') == 0

    resp2 = app_client.post(
        f'/api/v1/feeds/{slug}/episodes',
        data={'audio': (io.BytesIO(real_mp3_bytes), 'ep2b.mp3'), 'episode': '2'},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp2.status_code == 201
    assert resp2.get_json()['queued'] is True
    assert _queue_row_count('s01e02') == 1


def test_delete_removes_from_status_service_display_queue(app_client, local_feed):
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    with patch('api.local_episodes.get_status_service') as mock_get_status:
        mock_status = mock_get_status.return_value
        resp = app_client.delete(f'/api/v1/feeds/{slug}/episodes/s01e01', headers=headers)

    assert resp.status_code == 200
    mock_status.remove_queued_episode.assert_called_once_with(slug, 's01e01')


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


def test_list_and_detail_artwork_url_falls_back_to_local_route(app_client, local_feed):
    """Report bug 6: a local episode's artwork_url column is never
    populated (there's no upstream RSS item image to source it from), so
    both the list and detail serializers must fall back to the admin
    artwork proxy route when a cover is actually cached -- not leave
    artworkUrl null. Must be the internal /api/v1/... proxy, not the
    public feed-key-gated route local_feed_builder emits in the RSS (a
    list/detail JSON response must never carry the feed auth key)."""
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    upload_resp = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/s01e01/artwork',
        data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert upload_resp.status_code == 200

    expected = f'/api/v1/feeds/{slug}/episodes/s01e01/artwork'

    list_resp = app_client.get(f'/api/v1/feeds/{slug}/episodes')
    list_item = next(e for e in list_resp.get_json()['episodes'] if e['episodeId'] == 's01e01')
    assert list_item['artworkUrl'] == expected

    detail_resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/s01e01')
    assert detail_resp.get_json()['artworkUrl'] == expected


def test_artwork_url_stays_null_without_a_cached_cover(app_client, local_feed):
    """No artwork uploaded at all -> artworkUrl stays null, same as before
    -- the fallback only fires when storage actually has a cached cover."""
    slug = local_feed['slug']
    db = local_feed['db']
    _seed_episode(db, slug, 's01e01')
    _authed(app_client)

    detail_resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/s01e01')
    assert detail_resp.get_json()['artworkUrl'] is None


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

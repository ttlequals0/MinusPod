"""Bulk import API endpoints: upload/scan/commit/status (#625 Task 11).

Mirrors test_local_episode_api.py's fixture style (shared app_client fixture
from conftest.py, local _authed/_csrf_headers helpers, main_app singleton
alignment).
"""
import io
import os
import shutil
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='local-import-api-test-'))


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
    slug = 'local-import-api-subscribed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Subscribed Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


@pytest.fixture
def local_feed(app_client):
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'local-import-api-local'
    # The staging dir is keyed only by slug, not per-test -- clear any
    # leftover files from a prior test in this module before this test's
    # uploads/scans run, so plans stay scoped to what this test staged.
    shutil.rmtree(storage.import_staging_dir(slug), ignore_errors=True)
    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)
    shutil.rmtree(storage.import_staging_dir(slug), ignore_errors=True)


def _upload(app_client, slug, files):
    headers = _csrf_headers(app_client)
    data = {'files': [(io.BytesIO(content), name) for name, content in files]}
    return app_client.post(
        f'/api/v1/feeds/{slug}/import/upload',
        data=data,
        headers=headers,
        content_type='multipart/form-data',
    )


# ---------- upload ----------

def test_upload_stages_files_under_original_basename(app_client, local_feed):
    from api import get_storage
    slug = local_feed['slug']
    _authed(app_client)

    resp = _upload(app_client, slug, [
        ('s01e01 - Pilot.mp3', b'audio-bytes'),
        ('s01e01 - Pilot.json', b'{"title": "Pilot"}'),
    ])

    assert resp.status_code == 200
    body = resp.get_json()
    assert sorted(body['staged']) == ['s01e01 - Pilot.json', 's01e01 - Pilot.mp3']
    assert body['rejected'] == []

    staging_dir = get_storage().import_staging_dir(slug)
    assert (staging_dir / 's01e01 - Pilot.mp3').read_bytes() == b'audio-bytes'
    assert (staging_dir / 's01e01 - Pilot.json').read_bytes() == b'{"title": "Pilot"}'


@pytest.mark.parametrize('name,reason_substr', [
    ('../evil.mp3', 'invalid'),
    ('a/b.mp3', 'invalid'),
    ('a\\b.mp3', 'invalid'),
    ('.hidden.mp3', 'dotfile'),
    ('', 'empty'),
])
def test_upload_rejects_dangerous_basenames(app_client, local_feed, name, reason_substr):
    slug = local_feed['slug']
    _authed(app_client)

    resp = _upload(app_client, slug, [(name, b'x')])

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['staged'] == []
    assert len(body['rejected']) == 1
    assert body['rejected'][0]['file'] == name
    assert reason_substr in body['rejected'][0]['reason']


def test_upload_mixed_valid_and_rejected(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)

    resp = _upload(app_client, slug, [
        ('s01e01.mp3', b'audio'),
        ('../escape.mp3', b'bad'),
    ])

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['staged'] == ['s01e01.mp3']
    assert len(body['rejected']) == 1
    assert body['rejected'][0]['file'] == '../escape.mp3'


def test_upload_rejects_overlong_basename_without_crashing_batch(app_client, local_feed):
    """A 300-char filename must not OSError (ENAMETOOLONG) the whole
    request -- it degrades to a per-file rejection alongside the valid
    file, which still stages normally."""
    slug = local_feed['slug']
    _authed(app_client)

    long_name = ('a' * 296) + '.mp3'  # 300 chars, well past the 255-byte cap
    assert len(long_name) == 300

    resp = _upload(app_client, slug, [
        ('s01e01.mp3', b'audio'),
        (long_name, b'bad'),
    ])

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['staged'] == ['s01e01.mp3']
    assert len(body['rejected']) == 1
    assert body['rejected'][0]['file'] == long_name
    assert 'too long' in body['rejected'][0]['reason']


def test_upload_missing_files_field_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/upload',
        data={},
        headers=headers,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400


def test_upload_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)

    resp = _upload(app_client, slug, [('s01e01.mp3', b'x')])
    assert resp.status_code == 400


def test_upload_exempt_from_default_rate_limit(app_client, local_feed):
    """(round-2 review finding 2) The blueprint's default rate limit
    (200/min, api/__init__.py) must not apply to this route: the UI now
    uploads one file per request (sequential per-file uploads for "x of y"
    progress), so a batch past 200 files would otherwise start 429ing
    partway through even though nothing is actually wrong. Functional,
    not an internals check: loops past the default limit against the real
    endpoint and confirms every response is a normal 200, never a 429 --
    this module's autouse _reset_rate_limiter fixture confirms the limiter
    itself is genuinely active in this test process."""
    slug = local_feed['slug']
    _authed(app_client)

    for i in range(205):
        resp = _upload(app_client, slug, [(f'ex{i:04d}.mp3', b'x')])
        assert resp.status_code == 200, f'request {i} got {resp.status_code}: {resp.get_json()}'


# ---------- scan ----------

def test_scan_strips_internal_paths_and_returns_plan(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01 - Pilot.mp3', b'audio-bytes')])

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={'source': 'staging'},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['slug'] == slug
    assert len(body['entries']) == 1
    entry = body['entries'][0]
    assert entry['episodeId'] == 's01e01'
    assert entry['audioFile'] == 's01e01 - Pilot.mp3'
    assert entry['mtimeNs'] > 0
    for key in ('audioPath', 'descriptionPath', 'artworkPath', 'sidecarPath'):
        assert key not in entry
    assert 'planHash' in body


def test_scan_default_source_is_both(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01.mp3', b'audio-bytes')])

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={},
        headers=headers,
    )
    assert resp.status_code == 200
    assert len(resp.get_json()['entries']) == 1


def test_scan_invalid_source_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={'source': 'nope'},
        headers=headers,
    )
    assert resp.status_code == 400


def test_scan_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={},
        headers=headers,
    )
    assert resp.status_code == 400


# ---------- commit ----------

def test_commit_stale_hash_409(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01.mp3', b'audio-bytes')])

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/commit',
        json={'planHash': 'stale-not-a-real-hash', 'source': 'staging', 'overwrite': False},
        headers=headers,
    )

    assert resp.status_code == 409
    assert 're-run scan' in resp.get_json()['error']


def test_commit_overwrite_mismatch_from_scan_409s(app_client, local_feed):
    """(round-2 review finding 4) The exact same files, scanned with
    overwrite=False, then committed with overwrite=True (or vice versa)
    must 409 as stale -- plan_hash folds overwrite into the hash, so a
    commit whose overwrite doesn't match what was reviewed at scan time
    can never slip through as if nothing had changed."""
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01.mp3', b'audio-bytes')])
    scan_resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={'source': 'staging', 'overwrite': False},
        headers=headers,
    )
    plan_hash = scan_resp.get_json()['planHash']

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/commit',
        json={'planHash': plan_hash, 'source': 'staging', 'overwrite': True},
        headers=headers,
    )

    assert resp.status_code == 409
    assert 're-run scan' in resp.get_json()['error']


def test_commit_starts_job_with_server_side_plan(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01.mp3', b'audio-bytes')])
    scan_resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={'source': 'staging'},
        headers=headers,
    )
    plan_hash = scan_resp.get_json()['planHash']

    with patch('api.local_episodes.start_commit') as mock_start:
        mock_start.return_value = (True, 'started')
        resp = app_client.post(
            f'/api/v1/feeds/{slug}/import/commit',
            json={'planHash': plan_hash, 'source': 'staging', 'overwrite': False},
            headers=headers,
        )

    assert resp.status_code == 202
    assert resp.get_json()['message'] == 'import started'
    mock_start.assert_called_once()
    call_kwargs = mock_start.call_args
    passed_slug, passed_plan = call_kwargs.args
    assert passed_slug == slug
    # Server-side plan retains the internal path keys (never a
    # client-supplied plan, and never the stripped scan-response shape).
    assert passed_plan['entries'][0]['audioPath'].endswith('s01e01.mp3')
    assert passed_plan['planHash'] == plan_hash


def test_commit_already_running_409(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    _upload(app_client, slug, [('s01e01.mp3', b'audio-bytes')])
    scan_resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/scan',
        json={'source': 'staging'},
        headers=headers,
    )
    plan_hash = scan_resp.get_json()['planHash']

    with patch('api.local_episodes.start_commit') as mock_start:
        mock_start.return_value = (False, 'import already running')
        resp = app_client.post(
            f'/api/v1/feeds/{slug}/import/commit',
            json={'planHash': plan_hash, 'source': 'staging', 'overwrite': False},
            headers=headers,
        )

    assert resp.status_code == 409
    assert resp.get_json()['error'] == 'import already running'


def test_commit_missing_plan_hash_400(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/commit',
        json={'source': 'staging'},
        headers=headers,
    )
    assert resp.status_code == 400


def test_commit_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.post(
        f'/api/v1/feeds/{slug}/import/commit',
        json={'planHash': 'x', 'source': 'staging', 'overwrite': False},
        headers=headers,
    )
    assert resp.status_code == 400


# ---------- status ----------

def test_status_passthrough(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}/import/status')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None}


def test_status_reflects_running_job(app_client, local_feed):
    slug = local_feed['slug']
    _authed(app_client)

    fake_status = {'state': 'running', 'processed': 2, 'total': 5, 'startedAt': '2026-08-27T00:00:00Z'}
    with patch('api.local_episodes.get_import_status', return_value=fake_status) as mock_status:
        resp = app_client.get(f'/api/v1/feeds/{slug}/import/status')

    assert resp.status_code == 200
    assert resp.get_json() == fake_status
    mock_status.assert_called_once_with(slug)


def test_status_400_on_subscribed_feed(app_client, subscribed_feed):
    slug = subscribed_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}/import/status')
    assert resp.status_code == 400

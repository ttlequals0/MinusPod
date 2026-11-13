"""DELETE /feeds/<slug> also removes a local feed's import staging and source dirs.

They live outside podcasts_dir (storage.import_staging_dir/import_source_dir), so cleanup_podcast_dir alone misses them. Fixture style mirrors test_local_import_api.py."""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feeds-delete-local-dirs-test-'))


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
def _align_main_app_singletons(app_client):
    """See test_local_feed_api.py's identical fixture: main_app.feeds / local_feed_builder freeze db/storage references at import time that can go stale."""
    import main_app.feeds as mf
    import local_feed_builder as lfb
    from api import get_database, get_storage

    db, storage = get_database(), get_storage()
    orig = (mf.db, mf.storage, lfb.db, lfb.storage)
    mf.db, mf.storage = db, storage
    lfb.db, lfb.storage = db, storage
    yield
    mf.db, mf.storage, lfb.db, lfb.storage = orig


def test_delete_local_feed_removes_staging_and_import_dirs(app_client):
    from api import get_database, get_storage
    db, storage = get_database(), get_storage()
    slug = 'feeds-delete-local-staging'
    _authed(app_client)
    headers = _csrf_headers(app_client)

    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    staging_dir = storage.import_staging_dir(slug, create=True)
    (staging_dir / 'leftover.mp3').write_bytes(b'x')
    import_dir = storage.import_source_dir(slug)
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / 'archive.mp3').write_bytes(b'x')

    resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=headers)

    assert resp.status_code == 200
    assert not staging_dir.exists()
    assert not import_dir.exists()


def test_delete_local_feed_survives_a_failing_rmtree(app_client):
    """A refusing bind-mount (shutil.rmtree always raising) must not fail the API call: _best_effort_rmtree tolerates it after the feed row and podcast dir are already deleted."""
    from api import get_database, get_storage
    db, storage = get_database(), get_storage()
    slug = 'feeds-delete-local-busy-mount'
    _authed(app_client)
    headers = _csrf_headers(app_client)

    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    staging_dir = storage.import_staging_dir(slug, create=True)
    (staging_dir / 'leftover.mp3').write_bytes(b'x')

    with patch('api.feeds.shutil.rmtree', side_effect=OSError('device or resource busy')):
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=headers)

    assert resp.status_code == 200
    assert resp.get_json()['message'] == 'Feed deleted'


def test_delete_local_feed_clears_import_job_files(app_client):
    """A recreated slug must not inherit the deleted feed's import report or lock: delete also clears <data>/.import-jobs/<slug>.{json,lock}."""
    from api import get_database, get_storage
    from local_import import get_import_status

    db, storage = get_database(), get_storage()
    slug = 'feeds-delete-local-import-jobs'
    _authed(app_client)
    headers = _csrf_headers(app_client)

    db.create_podcast(slug, f'local://{slug}', 'Local Test', feed_type='local')
    jobs_dir = storage.data_dir / '.import-jobs'
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f'{slug}.json').write_text(
        '{"state": "done", "processed": 1, "total": 1, '
        '"startedAt": "2026-08-27T00:00:00Z", "report": {}}')
    (jobs_dir / f'{slug}.lock').write_text('')

    resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=headers)
    assert resp.status_code == 200

    assert not (jobs_dir / f'{slug}.json').exists()
    assert not (jobs_dir / f'{slug}.lock').exists()

    # Recreating the slug must read idle, not the old feed's report.
    db.create_podcast(slug, f'local://{slug}', 'Local Test Again', feed_type='local')
    try:
        assert get_import_status(slug, storage) == {
            'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None,
        }
    finally:
        db.delete_podcast(slug)


def test_delete_subscribed_feed_does_not_touch_local_import_dirs(app_client):
    """A subscribed feed must never be probed for local import dirs: is_local_feed(podcast) gates the whole cleanup block."""
    from api import get_database, get_storage
    db, storage = get_database(), get_storage()
    slug = 'feeds-delete-subscribed'
    _authed(app_client)
    headers = _csrf_headers(app_client)

    db.create_podcast(slug, 'https://example.com/feed.xml', 'Subscribed Test')

    with patch.object(storage, 'import_staging_dir') as mock_staging, \
         patch.object(storage, 'import_source_dir') as mock_source:
        resp = app_client.delete(f'/api/v1/feeds/{slug}', headers=headers)

    assert resp.status_code == 200
    mock_staging.assert_not_called()
    mock_source.assert_not_called()

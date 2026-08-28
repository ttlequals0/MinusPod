"""DELETE /feeds/<slug> also removes a local feed's import staging and
import source directories (round-2 field-test fix): they live outside
podcasts_dir (see storage.import_staging_dir/import_source_dir), so
cleanup_podcast_dir alone never touches them, and they used to survive the
feed they were staged for.

Mirrors test_local_import_api.py's fixture style (shared app_client
fixture from conftest.py, local _authed/_csrf_headers helpers, main_app
singleton alignment).
"""
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
    """A busy bind-mount point refusing to remove itself (simulated here by
    making shutil.rmtree always raise) must not fail the API call -- the
    feed row and podcast dir deletion already succeeded by the time the
    import-dir cleanup runs, and a stubborn mount point is exactly the
    scenario _best_effort_rmtree exists to tolerate."""
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


def test_delete_subscribed_feed_does_not_touch_local_import_dirs(app_client):
    """A subscribed feed has no import directories of its own, and must
    never be probed for a local feed's staging/import dirs it happens to
    share a slug-derived path shape with -- is_local_feed(podcast) gates
    the whole cleanup block."""
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

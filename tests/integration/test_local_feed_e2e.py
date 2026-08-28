"""End-to-end local feed lifecycle (#625, Task 15).

Exercises the whole local-feed seam through the real Flask app, the real
ffmpeg-probed commit engine, and the real RSS builder -- the way an
operator's session would: create the feed, bulk-import an archive, serve
the RSS, upload a single new episode with auto-process on, edit and delete
an episode, confirm retention never touches a local feed's only copy, and
check OPML export includes/excludes the feed per mode.

Mirrors test_episode_artwork_endpoint.py's fixture style: app_bootstrap's
bootstrap() pins the Database/Storage singleton defaults before main_app is
imported, app_password is cleared so auth/CSRF (api/__init__.py's
check_auth) never engage, and the podcast row is created/torn down by slug
in a client fixture rather than the shared root app_client fixture.

Also mirrors test_opml_url_route.py's ``_bind_db_singleton`` fixture: in a
full-suite run, some other already-collected integration module is the one
that actually first imported main_app (module imports and Database/Storage
singleton construction happen once per process), so main_app.db/storage --
and every submodule that did ``from main_app import db, storage`` at ITS own
import time (main_app.routes, main_app.feeds, local_feed_builder) -- are
frozen on THAT module's instances, not necessarily this module's own
bootstrap()'d dir. Rebinding database.Database._instance /
storage.Storage._instance to main_app's own db/storage makes every one of
those call sites (and this module's own Database()/Storage() calls)
consistent again, regardless of collection order.
"""
import io
import shutil
import subprocess
import time

import feedparser
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('local_feed_e2e_', secret_key='local-feed-e2e-secret')

import database  # noqa: E402
import storage as storage_module  # noqa: E402
from main_app import app, db as app_db, storage as app_storage  # noqa: E402

requires_ffmpeg = pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg/ffprobe not available')

SLUG = 'e2e-local-show'

# Import commit runs on a real background daemon thread (see
# local_import.start_commit); polling avoids monkeypatching threading.Thread
# globally, which would also break flask-limiter's own Timer-based memory
# storage mid-request (Timer.__init__ resolves the module-global `Thread`
# name at call time, not via super()).
_IMPORT_POLL_TIMEOUT_S = 30
_IMPORT_POLL_INTERVAL_S = 0.1


def _wait_for_import_done(client, slug):
    deadline = time.monotonic() + _IMPORT_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = client.get(f'/api/v1/feeds/{slug}/import/status').get_json()
        if status['state'] in ('done', 'error'):
            return status
        time.sleep(_IMPORT_POLL_INTERVAL_S)
    raise AssertionError(f'import for {slug} did not finish within '
                         f'{_IMPORT_POLL_TIMEOUT_S}s')


def _delete_podcast_row(conn):
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()


@pytest.fixture(autouse=True)
def _bind_singletons():
    prev_db, prev_storage = database.Database._instance, storage_module.Storage._instance
    database.Database._instance = app_db
    storage_module.Storage._instance = app_storage
    yield
    database.Database._instance = prev_db
    storage_module.Storage._instance = prev_storage


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True

    _delete_podcast_row(db.get_connection())

    with app.test_client() as c:
        yield c

    _delete_podcast_row(db.get_connection())
    shutil.rmtree(storage_module.Storage().import_staging_dir(SLUG), ignore_errors=True)


@pytest.fixture(scope='session')
def real_mp3_bytes(tmp_path_factory):
    """A small, structurally valid mp3 -- same ffmpeg invocation as
    test_local_episode_api.py's identical fixture."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    mp3_path = tmp_path_factory.mktemp('audio') / 'fixture.mp3'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
         '-t', '1', '-q:a', '9', str(mp3_path)],
        check=True, capture_output=True,
    )
    return mp3_path.read_bytes()


@requires_ffmpeg
def test_local_feed_end_to_end_flow(client, real_mp3_bytes):
    db = database.Database()
    storage = storage_module.Storage()

    # ---- Step 1: create the local feed; empty feed serves valid RSS ----
    create_resp = client.post('/api/v1/feeds', json={
        'feedType': 'local', 'title': 'E2E Local Show', 'slug': SLUG,
    })
    assert create_resp.status_code == 201, create_resp.get_data(as_text=True)
    assert create_resp.get_json()['feedType'] == 'local'

    empty_feed = client.get(f'/{SLUG}')
    assert empty_feed.status_code == 200
    parsed_empty = feedparser.parse(empty_feed.data)
    assert parsed_empty.bozo == 0
    assert parsed_empty.entries == []

    # ---- Step 2: stage 3 mp3s + 1 description txt + 1 bad-name mp3;
    # scan -> 3 entries + 1 rejected; commit; poll status to done ----
    staged_files = [
        ('S01E01 - Alpha.mp3', real_mp3_bytes),
        ('S01E01 - Alpha.txt', b'Alpha show notes'),
        ('S01E02 - Bravo.mp3', real_mp3_bytes),
        ('S01E03 - Charlie.mp3', real_mp3_bytes),
        ('not-a-valid-name.mp3', real_mp3_bytes),
    ]
    upload_resp = client.post(
        f'/api/v1/feeds/{SLUG}/import/upload',
        data={'files': [(io.BytesIO(content), name) for name, content in staged_files]},
        content_type='multipart/form-data',
    )
    assert upload_resp.status_code == 200, upload_resp.get_data(as_text=True)
    assert upload_resp.get_json()['rejected'] == []

    scan_resp = client.post(f'/api/v1/feeds/{SLUG}/import/scan', json={})
    assert scan_resp.status_code == 200
    plan = scan_resp.get_json()
    assert len(plan['entries']) == 3
    assert len(plan['rejected']) == 1
    assert plan['rejected'][0]['file'] == 'not-a-valid-name.mp3'
    assert sorted(e['episodeId'] for e in plan['entries']) == ['s01e01', 's01e02', 's01e03']
    for entry in plan['entries']:
        for key in ('audioPath', 'descriptionPath', 'artworkPath', 'sidecarPath'):
            assert key not in entry  # server filesystem layout never reaches the client

    commit_resp = client.post(
        f'/api/v1/feeds/{SLUG}/import/commit',
        json={'planHash': plan['planHash'], 'source': 'both', 'overwrite': False},
    )
    assert commit_resp.status_code == 202

    status = _wait_for_import_done(client, SLUG)
    assert status['state'] == 'done'
    report = status['report']
    assert sorted(item['episodeId'] for item in report['committed']) == \
        ['s01e01', 's01e02', 's01e03']
    assert report['failed'] == []
    assert report['queued'] == []  # empty feed's first import never auto-queues

    for ep_id in ('s01e01', 's01e02', 's01e03'):
        episode = db.get_episode(SLUG, ep_id)
        assert episode is not None
        assert episode['status'] == 'discovered'
        assert storage.get_original_path(SLUG, ep_id).exists()
    assert db.get_episode(SLUG, 's01e01')['description'] == 'Alpha show notes'

    conn = db.get_connection()

    def _queue_count(episode_id=None):
        if episode_id is None:
            row = conn.execute(
                "SELECT COUNT(*) c FROM auto_process_queue aq "
                "JOIN podcasts p ON aq.podcast_id = p.id WHERE p.slug = ?",
                (SLUG,)).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) c FROM auto_process_queue aq "
                "JOIN podcasts p ON aq.podcast_id = p.id "
                "WHERE p.slug = ? AND aq.episode_id = ?",
                (SLUG, episode_id)).fetchone()
        return row['c']

    assert _queue_count() == 0  # nothing queued by the initial import

    # ---- Step 3: served RSS has 3 items, unversioned enclosures, sNNeNN guids ----
    feed_resp = client.get(f'/{SLUG}')
    assert feed_resp.status_code == 200
    parsed = feedparser.parse(feed_resp.data)
    assert parsed.bozo == 0
    assert len(parsed.entries) == 3
    assert sorted(e.id for e in parsed.entries) == ['s01e01', 's01e02', 's01e03']
    for entry in parsed.entries:
        href = entry.enclosures[0]['href']
        assert href.endswith(f'/episodes/{SLUG}/{entry.id}.mp3')
        assert '-v' not in href.rsplit('/', 1)[-1]

    # ---- Step 4: single upload into the now-non-empty feed, auto-process
    # on by default -> the episode is queued ----
    single_upload_resp = client.post(
        f'/api/v1/feeds/{SLUG}/episodes',
        data={'audio': (io.BytesIO(real_mp3_bytes), 'delta.mp3')},
        content_type='multipart/form-data',
    )
    assert single_upload_resp.status_code == 201, single_upload_resp.get_data(as_text=True)
    single_body = single_upload_resp.get_json()
    assert single_body['episodeId'] == 's01e04'
    assert single_body['queued'] is True
    assert _queue_count('s01e04') == 1

    # ---- Step 5: PATCH title -> RSS regenerated with the new title ----
    patch_resp = client.patch(
        f'/api/v1/feeds/{SLUG}/episodes/s01e01', json={'title': 'Renamed Alpha'})
    assert patch_resp.status_code == 200
    assert patch_resp.get_json()['title'] == 'Renamed Alpha'

    retitled_feed = client.get(f'/{SLUG}')
    assert b'Renamed Alpha' in retitled_feed.data

    # ---- Step 6: DELETE episode -> row and files gone, RSS regenerated ----
    delete_resp = client.delete(f'/api/v1/feeds/{SLUG}/episodes/s01e04')
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()['deleted'] == 1
    assert db.get_episode(SLUG, 's01e04') is None
    assert not storage.get_original_path(SLUG, 's01e04').exists()
    assert _queue_count('s01e04') == 0  # queue row dropped with the episode

    after_delete_feed = client.get(f'/{SLUG}')
    parsed_after_delete = feedparser.parse(after_delete_feed.data)
    assert parsed_after_delete.bozo == 0
    assert sorted(e.id for e in parsed_after_delete.entries) == \
        ['s01e01', 's01e02', 's01e03']

    # ---- Step 7: retention sweep + force_all both leave local originals
    # (and the retained processed file) untouched ----
    db.update_podcast(SLUG, retention_days_override=0)
    orig_path = storage.get_original_path(SLUG, 's01e02')
    assert orig_path.exists()
    processed_path = storage.get_episode_path(SLUG, 's01e02')
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_bytes(b'PROCESSED')
    db.upsert_episode(SLUG, 's01e02', status='processed',
                      processed_file='s01e02.mp3', new_duration=1.0)
    conn.execute(
        "UPDATE episodes SET processed_at = '2020-01-01T00:00:00Z', "
        "created_at = '2020-01-01T00:00:00Z' WHERE episode_id = 's01e02'")
    conn.commit()

    db.cleanup_old_episodes(storage=storage)
    assert orig_path.exists(), 'the scheduled sweep must never touch a local original'
    assert processed_path.exists()

    db.cleanup_old_episodes(force_all=True, storage=storage)
    assert orig_path.exists(), 'force_all must never touch a local original'
    assert processed_path.exists()
    assert db.get_episode(SLUG, 's01e02')['status'] == 'processed'

    # ---- Step 8: OPML export -- original mode omits the local feed,
    # modified mode includes it ----
    original_opml = client.get('/api/v1/feeds/export-opml?mode=original')
    assert original_opml.status_code == 200
    assert 'E2E Local Show' not in original_opml.get_data(as_text=True)

    modified_opml = client.get('/api/v1/feeds/export-opml?mode=modified')
    assert modified_opml.status_code == 200
    modified_body = modified_opml.get_data(as_text=True)
    assert 'E2E Local Show' in modified_body
    assert f'/{SLUG}' in modified_body

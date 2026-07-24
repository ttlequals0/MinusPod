"""podcasts.last_podping_at: migration column, setter, and API exposure."""
from datetime import datetime

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('last_podping_at_test_')

from main_app import app
from database import Database
from utils.time import ISO_FORMAT

SLUG = 'last-podping-at-feed'


@pytest.fixture
def db():
    # Resolved at test-run time, not at module-import time: the Database
    # singleton can be re-pointed by a later-collected test module's own
    # bootstrap() call before this file's tests actually run, so binding
    # `db` at import time risks a stale reference relative to what the
    # Flask routes under test resolve via get_database().
    return Database()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        yield c


@pytest.fixture
def seeded_feed(db):
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Last Podping At Test')
    yield SLUG
    db.delete_podcast(SLUG)


def test_fresh_db_has_last_podping_at_column(db):
    cols = {row['name'] for row in
            db.get_connection().execute("PRAGMA table_info(podcasts)").fetchall()}
    assert 'last_podping_at' in cols


def test_set_last_podping_at_writes_iso_string(db, seeded_feed):
    assert db.get_podcast_by_slug(SLUG)['last_podping_at'] is None

    db.set_last_podping_at(SLUG)

    value = db.get_podcast_by_slug(SLUG)['last_podping_at']
    assert value is not None
    datetime.strptime(value, ISO_FORMAT)  # raises if not a valid ISO timestamp


def test_feed_api_payload_includes_last_podping_at_null_then_set(db, client, seeded_feed):
    resp = client.get(f'/api/v1/feeds/{SLUG}')
    assert resp.status_code == 200
    assert resp.get_json()['lastPodpingAt'] is None

    db.set_last_podping_at(SLUG)

    resp = client.get(f'/api/v1/feeds/{SLUG}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['lastPodpingAt'] == db.get_podcast_by_slug(SLUG)['last_podping_at']
    assert body['lastPodpingAt'] is not None

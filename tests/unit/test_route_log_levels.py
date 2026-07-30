"""Expected traffic on the public feed routes logs at INFO, not WARNING/ERROR:
bot probes of unknown slugs (RSS and episode) and unauthenticated OPML fetches."""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('route_log_levels_test_')

from main_app import app, db  # noqa: E402

KEY = 'a' * 64
WRONG_KEY = 'b' * 64


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _feed_auth_off():
    """Never leak an enabled feed-auth state into other test modules."""
    yield
    db.set_setting('feed_auth_enabled', 'false', is_default=False)


def _levels(caplog, needle):
    return [r.levelname for r in caplog.records if needle in r.getMessage()]


def test_unknown_feed_probe_logs_info(client, caplog):
    with caplog.at_level('DEBUG', logger='podcast.feed'):
        resp = client.get('/no-such-feed')
    assert resp.status_code == 404
    assert _levels(caplog, 'Feed not found') == ['INFO']


def test_unknown_feed_episode_probe_logs_info(client, caplog):
    with caplog.at_level('DEBUG', logger='podcast.feed'):
        resp = client.get('/episodes/no-such-feed/a1b2c3d4e5f6.mp3')
    assert resp.status_code == 404
    assert _levels(caplog, 'Feed not found for episode') == ['INFO']


def test_opml_bad_key_logs_info(client, caplog):
    db.set_setting('feed_auth_enabled', 'true', is_default=False)
    db.set_setting('feed_auth_key', KEY, is_default=False)
    with caplog.at_level('DEBUG', logger='podcast.feed'):
        resp = client.get(f'/opml/modified.opml?key={WRONG_KEY}')
    assert resp.status_code == 401
    assert _levels(caplog, 'no auth key provided or is invalid') == ['INFO']

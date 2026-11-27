"""serve_rss measures the 15 minute freshness from the later of the last check and the last refresh attempt."""
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('serve_rss_parse_failure_test_', secret_key='serve-rss-parse-failure-secret')

import database
from main_app import app, db as app_db
import main_app.feeds as feeds_mod
import main_app.routes as routes_mod
from rss_parser import RSS_RENDER_VERSION
from utils.time import ISO_FORMAT, utc_now

BASE = 'http://localhost:8000'


def _cached_rss(slug):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- minuspod-rss-render-version:{RSS_RENDER_VERSION} -->
<rss version="2.0"><channel><title>Cached Title</title>
<item><enclosure url="{BASE}/episodes/{slug}/abcdefabcdef.mp3" type="audio/mpeg" /></item>
</channel></rss>"""


def _ago(minutes):
    return (utc_now() - timedelta(minutes=minutes)).strftime(ISO_FORMAT)


@pytest.fixture(autouse=True)
def _bind_db_singleton():
    prev = database.Database._instance
    database.Database._instance = app_db
    yield
    database.Database._instance = prev


@pytest.fixture
def client():
    app_db.set_setting('app_password', '')
    app_db.set_setting('feed_auth_enabled', 'false', is_default=False)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _feed(slug, attempted_min_ago, cached=True, attempt_text=None):
    app_db.create_podcast(slug, 'https://example.com/feed.xml', 'Example Feed')
    fields = {'last_checked_at': _ago(40)}
    if attempt_text is not None:
        fields['last_refresh_attempt_at'] = attempt_text
    elif attempted_min_ago is not None:
        fields['last_refresh_attempt_at'] = _ago(attempted_min_ago)
    app_db.update_podcast(slug, **fields)
    if cached:
        routes_mod.storage.save_rss(slug, _cached_rss(slug))
    feeds_mod.invalidate_feed_cache()


def _get(client, slug):
    with patch.object(routes_mod, '_kick_background_refresh') as kick, \
         patch.object(feeds_mod, 'refresh_rss_feed') as sync_refresh:
        response = client.get(f'/{slug}')
    return response, kick, sync_refresh


@pytest.mark.parametrize('attempted_min_ago, expect_kick', [(2, False), (20, True), (None, True)])
def test_background_refresh_follows_the_later_of_the_check_and_the_attempt(
        client, attempted_min_ago, expect_kick):
    slug = f'attempt-{attempted_min_ago}'.lower()
    _feed(slug, attempted_min_ago)
    response, kick, sync_refresh = _get(client, slug)
    assert response.status_code == 200
    assert 'Cached Title' in response.get_data(as_text=True)
    assert kick.called is expect_kick
    sync_refresh.assert_not_called()


def test_missing_cache_still_refreshes_despite_a_recent_attempt(client):
    _feed('attempt-nocache', 2, cached=False)
    _, kick, sync_refresh = _get(client, 'attempt-nocache')
    sync_refresh.assert_called_once()
    assert sync_refresh.call_args.kwargs['force'] is True
    kick.assert_not_called()


def test_malformed_attempt_timestamp_does_not_skip_the_freshness_check(client):
    _feed('attempt-malformed', None, attempt_text='not a timestamp')
    response, kick, _ = _get(client, 'attempt-malformed')
    assert response.status_code == 200
    kick.assert_called_once()

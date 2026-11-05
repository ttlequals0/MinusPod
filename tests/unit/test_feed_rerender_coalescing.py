"""A per-feed marker / playback change re-renders that feed promptly in the
background, and a later change is never dropped by an earlier refresh that is
still in flight."""
import threading
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('rerender_coalescing_test_', reset_storage=True)
import main_app.routes as routes  # noqa: E402
from main_app import app, db  # noqa: E402
from rss_parser import RSSParser  # noqa: E402

SLUG = 'rerender-feed'
UP = '''<?xml version="1.0"?><rss version="2.0"><channel><title>Show</title>
<link>https://x.example</link><description>d</description>
<item><title>Alpha</title><guid>g1</guid><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/a.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>'''


@pytest.fixture(autouse=True)
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Rerender')
    parser = RSSParser()
    entry = parser.parse_feed(UP, source=SLUG).entries[0]
    eid = parser.generate_episode_id(entry.enclosures[0]['href'], entry.get('id'))
    db.upsert_episode(SLUG, eid, original_url=entry.enclosures[0]['href'],
                      title='Alpha', status='processed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET processed_file='x.mp3' WHERE episode_id=?", (eid,))
    yield
    db.delete_podcast(SLUG)


def _wait_idle(timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with routes._bg_refresh_lock:
            if SLUG not in routes._bg_refresh_inflight:
                return
        time.sleep(0.02)
    raise AssertionError('background refresh never finished')


def test_kick_while_in_flight_reruns_once_with_force():
    calls, gate, started = [], threading.Event(), threading.Event()

    def fake_refresh(slug, url, force=False):
        calls.append(force)
        started.set()
        if len(calls) == 1:
            gate.wait(5)

    with patch('main_app.feeds.refresh_rss_feed', fake_refresh):
        routes._kick_background_refresh(SLUG, 'u', force=True)
        assert started.wait(5)
        routes._kick_background_refresh(SLUG, 'u', force=True)     # arrives mid-flight
        routes._kick_background_refresh(SLUG, 'u', force=True)     # coalesces with it
        gate.set()
        _wait_idle()
    assert calls == [True, True]


def test_unforced_kick_while_in_flight_is_still_dropped():
    calls, gate, started = [], threading.Event(), threading.Event()

    def fake_refresh(slug, url, force=False):
        calls.append(force)
        started.set()
        gate.wait(5)

    with patch('main_app.feeds.refresh_rss_feed', fake_refresh):
        routes._kick_background_refresh(SLUG, 'u')
        assert started.wait(5)
        routes._kick_background_refresh(SLUG, 'u')                 # stale-while-revalidate
        gate.set()
        _wait_idle()
    assert calls == [False]


def test_two_quick_patches_leave_the_served_rss_on_the_last_one():
    fm = {SLUG: {'in': 'https://example.com/feed.xml', 'out': SLUG}}
    first_fetch, release = threading.Event(), threading.Event()
    n = {'fetch': 0}

    def slow_fetch(url, *a, **k):
        n['fetch'] += 1
        if n['fetch'] == 1:
            first_fetch.set()
            release.wait(5)
        return UP, None, None

    with patch('main_app.feeds.get_feed_map', return_value=fm), \
         patch('main_app.routes.get_feed_map', return_value=fm), \
         patch('main_app.feeds.rss_parser.fetch_feed_conditional', slow_fetch), \
         app.test_client() as c:
        assert c.patch(f'/api/v1/feeds/{SLUG}', json={'processedTitleMarker': '[x] '}).status_code == 200
        assert first_fetch.wait(5)
        assert c.patch(f'/api/v1/feeds/{SLUG}', json={'processedTitleMarker': '[y] '}).status_code == 200
        release.set()
        _wait_idle()
        titles = [i.findtext('title') for i in ET.fromstring(
            c.get(f'/{SLUG}').get_data(as_text=True)).iter('item')]
    assert titles == ['[y] Alpha']


def test_a_per_feed_change_bypasses_the_refresh_coalesce_window():
    """refresh_rss_feed skips an unforced refresh inside its 30 s window; the
    change must still render, so the kick forces it."""
    seen = []
    with patch('main_app.feeds.refresh_rss_feed',
               lambda slug, url, force=False: seen.append(force)), \
         app.test_client() as c:
        c.patch(f'/api/v1/feeds/{SLUG}', json={'unprocessedPlayback': 'original'})
        _wait_idle()
    assert seen == [True]

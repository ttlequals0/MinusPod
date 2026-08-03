"""Feed metadata refresh on re-fetch (#596)."""
import sqlite3
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_metadata_test_', reset_storage=True)

import main_app.feeds as mf  # noqa: E402
import storage as storage_mod  # noqa: E402


def _png() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (60, 60), (10, 200, 10)).save(buf, 'PNG')
    return buf.getvalue()


def _seed(slug, artwork_url='https://example.com/old.png'):
    mf.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    mf.storage.save_artwork(slug, _png(), 'image/png', artwork_url)
    mf.invalidate_feed_cache()


def test_a_changed_cover_url_is_fetched_rather_than_read_as_cached():
    """The guard in download_artwork re-reads the row, so without force it
    compares the freshly written URL against itself and always hits."""
    slug = 'art-changed'
    _seed(slug)
    row = mf.db.get_podcast_by_slug(slug)
    assert row['artwork_cached']

    # Exactly what refresh does: store the new URL first, then download.
    mf.db.update_podcast(slug, artwork_url='https://example.com/new.png')
    with patch.object(storage_mod, 'safe_get',
                      side_effect=RuntimeError('fetched')) as get:
        mf.storage.download_artwork(slug, 'https://example.com/new.png',
                                    force=True)
    assert get.called, "force must skip the guard and actually fetch"

    # Without force the guard matches the URL against itself and returns early.
    with patch.object(storage_mod, 'safe_get') as get:
        mf.storage.download_artwork(slug, 'https://example.com/new.png')
    assert not get.called


def test_an_unchanged_cover_url_is_not_refetched():
    slug = 'art-same'
    _seed(slug, 'https://example.com/same.png')
    with patch.object(storage_mod, 'safe_get') as get:
        assert mf.storage.download_artwork(
            slug, 'https://example.com/same.png') is True
    assert not get.called


def test_refresh_forces_the_download_and_clears_the_cache_flag():
    """The call site is where the bug lived: it must pass force and drop
    artwork_cached, so a download that fails is retried next refresh."""
    slug = 'art-refresh'
    _seed(slug)
    feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>New title</title><link>https://example.com</link>
  <description>New description</description>
  <image><url>https://example.com/new.png</url></image>
</channel></rss>"""

    with patch.object(mf.rss_parser, 'fetch_feed_conditional',
                      return_value=(feed, None, None)), \
         patch.object(mf.storage, 'download_artwork',
                      return_value=False) as dl:
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml', force=True)

    assert dl.called, "a changed cover must reach download_artwork"
    args, kwargs = dl.call_args
    assert args[1] == 'https://example.com/new.png'
    assert kwargs.get('force') is True
    row = mf.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == 'https://example.com/new.png'
    assert not row['artwork_cached'], "a failed download must not look cached"
    assert row['description'] == 'New description'
    assert row['title'] == 'New title'


@pytest.fixture
def etag_db_path(tmp_path):
    """A podcasts table whose feeds carry conditional-GET validators."""
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT,
            etag TEXT,
            last_modified_header TEXT,
            artwork_url TEXT,
            artwork_cached INTEGER DEFAULT 0
        )
    """)
    conn.executemany(
        "INSERT INTO podcasts (slug, source_url, title, etag, "
        "last_modified_header, artwork_cached) VALUES (?, ?, ?, ?, ?, 1)",
        [('steady-a', 'https://example.com/a.xml', 'A', '"abc"', None),
         ('steady-b', 'https://example.com/b.xml', 'B', None,
          'Mon, 01 Jan 2026 00:00:00 GMT')],
    )
    conn.commit()
    conn.close()
    return path


def _validators(conn):
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT slug, etag, last_modified_header FROM podcasts")}


def test_migration_queues_one_artwork_redownload(etag_db_path):
    """The skipped-download bug stored the new URL against the old image, so
    change detection cannot repair those feeds; the flag clear does."""
    from database import Database

    Database._instance = None
    try:
        db = Database(data_dir=str(etag_db_path.parent))
        conn = db.get_connection()
        cached = [r[0] for r in conn.execute(
            "SELECT artwork_cached FROM podcasts")]
        assert cached == [0, 0]

        # A cover cached after the migration is not cleared again on reboot.
        conn.execute("UPDATE podcasts SET artwork_cached = 1 WHERE slug = 'steady-a'")
        conn.commit()
        db._run_schema_migrations()
        again = dict(conn.execute("SELECT slug, artwork_cached FROM podcasts"))
        assert again['steady-a'] == 1
    finally:
        Database._instance = None


def test_feed_response_never_hands_back_the_publisher_artwork_url():
    """An http:// cover in an https page is blocked by the browser, so the
    response always points at the proxy regardless of the cache flag."""
    from api.feeds import _podcast_listing_fields

    row = {
        'slug': 'insecure-art', 'artwork_cached': 0,
        'artwork_url': 'http://www.example.com/cover-v1.jpg',
        'source_url': 'https://example.com/f.xml',
        'title': 'Insecure Art', 'description': '', 'website_url': None,
    }
    out = _podcast_listing_fields(row, (False, False))
    assert out['artworkUrl'] == '/api/v1/feeds/insecure-art/artwork'
    assert not out['artworkUrl'].startswith('http://')


_LIVE_ITEM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
  <title>The Show</title>
  <link>https://show.example/</link>
  <description>What the show is about</description>
  <podcast:liveItem status="live">
    <title>Live now</title>
    <link>https://chat.example/room</link>
    <description>Tonight's live episode blurb</description>
  </podcast:liveItem>
</channel></rss>"""


def test_refresh_stores_the_shows_metadata_not_a_live_items():
    """feedparser folds <podcast:liveItem> children into the channel, so the
    row would otherwise hold the live episode's blurb and chat link (#596)."""
    slug = 'live-item-feed'
    _seed(slug)

    with patch.object(mf.rss_parser, 'fetch_feed_conditional',
                      return_value=(_LIVE_ITEM_FEED, None, None)), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml', force=True)

    row = mf.db.get_podcast_by_slug(slug)
    assert row['description'] == 'What the show is about'
    assert row['website_url'] == 'https://show.example/'
    assert row['title'] == 'The Show'
    assert row['channel_metadata_at'], "a successful refresh must stamp the read"


def test_a_304_forces_one_full_fetch_until_metadata_has_been_read():
    """Rows written before the raw-XML read can hold a live item's blurb, and
    a 304 carries no body to repair them."""
    slug = 'never-read-metadata'
    _seed(slug)
    # podping already read, so this isolates the channel-metadata condition.
    mf.db.update_podcast(slug, etag='"e1"', channel_metadata_at=None,
                         podping_checked_at='2026-07-26T00:00:00Z')
    mf.db.bulk_upsert_discovered_episodes(slug, [{
        'id': 'a1b2c3d4e5f6', 'url': 'https://example.com/a.mp3',
        'title': 'Ep', 'description': '', 'published': None}])

    calls = []

    def fake_fetch(url, etag=None, last_modified=None):
        calls.append(etag)
        if etag:
            return (None, '"e1"', None)
        return (_LIVE_ITEM_FEED, '"e2"', None)

    with patch.object(mf.rss_parser, 'fetch_feed_conditional', fake_fetch), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml')

    assert calls == ['"e1"', None], "the 304 must be followed by a full fetch"
    row = mf.db.get_podcast_by_slug(slug)
    assert row['description'] == 'What the show is about'
    assert row['channel_metadata_at']

    # Stamped now, so the next 304 must not force a second fetch.
    calls.clear()
    mf._refresh_coalesce.invalidate()
    with patch.object(mf.rss_parser, 'fetch_feed_conditional', fake_fetch):
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml', force=False)
    assert calls == ['"e2"']


def test_episode_api_keeps_an_insecure_cover_url_for_the_proxy():
    """The client turns this into a call to the episode artwork proxy rather
    than rendering it, and the proxy fetches server-side, so an http:// cover
    is no longer mixed content and no longer worth dropping the image over."""
    from api.episodes import _secure_artwork_url
    assert _secure_artwork_url('http://cdn.example/ep.jpg') == 'http://cdn.example/ep.jpg'
    assert _secure_artwork_url('https://cdn.example/ep.jpg') == 'https://cdn.example/ep.jpg'
    assert _secure_artwork_url(None) is None
    # Anything that is not an http(s) URL still has nothing to fetch.
    assert _secure_artwork_url('javascript:alert(1)') is None
    assert _secure_artwork_url('data:image/png;base64,AAAA') is None


def test_feed_artwork_falls_back_to_an_https_publisher_url_when_uncached():
    """A cover rejected at cache time has no file to proxy, so the endpoint
    would 404; the publisher's https URL still renders."""
    from api.feeds import _feed_artwork_url

    assert _feed_artwork_url({
        'slug': 'too-big', 'artwork_url': 'https://cdn.example/huge.gif',
    }) == 'https://cdn.example/huge.gif'
    # http would be blocked as mixed content, so keep the proxy and let the
    # placeholder show rather than a broken image.
    assert _feed_artwork_url({
        'slug': 'insecure', 'artwork_url': 'http://cdn.example/c.jpg',
    }) == '/api/v1/feeds/insecure/artwork'

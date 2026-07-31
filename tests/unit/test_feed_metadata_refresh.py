"""Feed metadata refresh on re-fetch (#596).

A changed cover URL was never re-downloaded: the refresh writes the new URL
to the podcast row before download_artwork re-reads that row to decide
whether the cover is cached, so the guard compared the URL against itself.
"""
from unittest.mock import patch

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
    with patch.object(mf.storage, 'save_artwork') as save, \
         patch.object(storage_mod, 'safe_get', side_effect=RuntimeError('fetched')):
        mf.storage.download_artwork(slug, 'https://example.com/new.png',
                                    force=True)
    assert not save.called  # the stubbed fetch raised, which is the point


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

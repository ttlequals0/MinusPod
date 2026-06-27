"""Cover-art refresh action (issue #420 follow-up).

refresh_feed_artwork re-pulls a feed's cover and rebuilds its served RSS so the
artwork_watermark_enabled setting takes effect, WITHOUT re-discovering or
queuing episodes (so the "Refresh all artwork" button never triggers
processing). refresh_all_artwork does it across every feed.
"""
import atexit
import io
import os
import shutil
import sys
import tempfile
from unittest.mock import patch

from PIL import Image

_test_data_dir = tempfile.mkdtemp(prefix='refresh_artwork_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage._instance = None
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)
atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

import main_app.feeds as mf

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (300, 300), (255, 255, 255)).save(buf, 'PNG')
    return buf.getvalue()


def _feed_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    <image>
      <url>https://example.com/art.png</url>
      <title>Source Show</title>
      <link>https://example.com</link>
    </image>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


def _seed(slug):
    mf.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    # save_artwork stamps artwork_url + artwork_cached on the podcast row.
    mf.storage.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')
    mf.invalidate_feed_cache()


def test_refresh_feed_artwork_rebuilds_served_rss_with_badge():
    slug = 'art-on'
    _seed(slug)
    mf.db.set_setting('artwork_watermark_enabled', 'true')
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        assert mf.refresh_feed_artwork(slug) is True
    served = mf.storage.get_rss(slug)
    assert f'/episodes/{slug}/cover-minuspod.jpg' in served
    assert 'https://example.com/art.png' not in served


def test_refresh_feed_artwork_drops_cached_badge_variant():
    # Regression: on a cache-hit cover, download_artwork no-ops and never drops
    # the badge variant, so refresh must drop it explicitly to recomposite with
    # the current rendering/setting (issue #420).
    slug = 'art-recomposite'
    _seed(slug)
    mf.db.set_setting('artwork_watermark_enabled', 'true')
    mf.storage.get_watermarked_artwork(slug)  # create the cached badge variant
    variant = mf.storage.get_podcast_dir(slug) / 'artwork-minuspod.jpg'
    assert variant.exists()
    # download_artwork returns True without touching the variant (cache-hit
    # no-op), so only the explicit clear can remove it.
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        assert mf.refresh_feed_artwork(slug) is True
    assert not variant.exists()


def test_refresh_feed_artwork_uses_upstream_when_disabled():
    slug = 'art-off'
    _seed(slug)
    mf.db.set_setting('artwork_watermark_enabled', 'false')
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        assert mf.refresh_feed_artwork(slug) is True
    served = mf.storage.get_rss(slug)
    assert 'https://example.com/art.png' in served
    assert 'cover-minuspod' not in served


def test_refresh_feed_artwork_does_not_queue_episodes():
    slug = 'art-noqueue'
    _seed(slug)
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True), \
         patch.object(mf.db, 'queue_episode_for_processing') as queue, \
         patch.object(mf.db, 'bulk_upsert_discovered_episodes') as discover:
        mf.refresh_feed_artwork(slug)
    queue.assert_not_called()
    discover.assert_not_called()


def test_refresh_feed_artwork_no_source_url_returns_false():
    # A podcast row with no source_url cannot be rebuilt.
    assert mf.refresh_feed_artwork('does-not-exist') is False


def test_refresh_all_artwork_counts_feeds():
    for slug in ('art-all-1', 'art-all-2'):
        _seed(slug)
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        count = mf.refresh_all_artwork()
    assert count >= 2

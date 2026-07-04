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
    assert f'/{slug}/cover-minuspod-' in served
    assert 'https://example.com/art.png' not in served


def test_served_cover_url_is_cache_busted_with_version():
    # Apps cache channel art by URL, so a static cover path never refreshes.
    # The served URL must carry the content-addressed ?v= token so a cover or
    # badge change shifts the URL and downstream apps re-fetch.
    slug = 'art-cachebust'
    _seed(slug)
    mf.db.set_setting('artwork_watermark_enabled', 'true')
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork', return_value=True):
        assert mf.refresh_feed_artwork(slug) is True
    served = mf.storage.get_rss(slug)
    token = mf.storage.artwork_version(slug)
    assert token
    assert f'/{slug}/cover-minuspod-{token}.jpg' in served


def test_artwork_version_is_content_addressed():
    slug = 'art-version'
    _seed(slug)  # seeded with the white _png()
    v1 = mf.storage.artwork_version(slug)
    assert v1 and mf.storage.artwork_version(slug) == v1  # stable for same bytes
    # A different cover shifts the token so the URL cache-busts.
    other = io.BytesIO()
    Image.new('RGB', (300, 300), (10, 20, 30)).save(other, 'PNG')
    mf.storage.save_artwork(slug, other.getvalue(), 'image/png',
                            'https://example.com/art.png')
    assert mf.storage.artwork_version(slug) != v1
    # No source cover -> no token (caller falls back to the bare URL).
    assert mf.storage.artwork_version('no-such-feed') is None


def test_artwork_version_shifts_when_badge_changes():
    # The badge-identity half of the token (cover_badge_salt) must cache-bust a
    # badge change, not just a cover change, so a redesigned badge on unchanged
    # covers still re-reaches apps.
    slug = 'art-badge-rev'
    _seed(slug)
    v1 = mf.storage.artwork_version(slug)
    with patch('artwork_watermark.BADGE_REVISION', 999):
        v2 = mf.storage.artwork_version(slug)
    assert v1 and v2 and v1 != v2


def test_stale_watermark_variant_is_regenerated():
    # A variant older than its cover/badge inputs must be recomposited, so a
    # badge or cover change reaches apps even on the passive refresh path that
    # never clears the cache.
    slug = 'art-stale'
    _seed(slug)
    mf.storage.get_watermarked_artwork(slug)  # composite + cache the variant
    variant = mf.storage.get_podcast_dir(slug) / 'artwork-minuspod.jpg'
    assert variant.exists()
    old = variant.stat().st_mtime - 3600
    os.utime(variant, (old, old))  # backdate so it predates the source cover
    mf.storage.get_watermarked_artwork(slug)
    assert variant.stat().st_mtime > old  # regenerated, not served stale


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


# --- rebuild_served_rss / rebuild_all_served_feeds (2.33.0) -----------------
# The feed-auth "Regenerate feeds" action must be a pure re-render: no
# processing triggers, no artwork side effects, no episode-row writes.

def test_rebuild_served_rss_does_not_queue_or_discover():
    slug = 'rebuild-noqueue'
    _seed(slug)
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.db, 'queue_episode_for_processing') as queue, \
         patch.object(mf.db, 'bulk_upsert_discovered_episodes') as discover:
        assert mf.rebuild_served_rss(slug) is True
    queue.assert_not_called()
    discover.assert_not_called()


def test_rebuild_served_rss_skips_artwork_side_effects():
    slug = 'rebuild-noart'
    _seed(slug)
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()), \
         patch.object(mf.storage, 'download_artwork') as dl, \
         patch.object(mf.storage, 'clear_watermark_cache') as clear:
        assert mf.rebuild_served_rss(slug) is True
    dl.assert_not_called()
    clear.assert_not_called()


def test_rebuild_served_rss_no_source_url_returns_false():
    assert mf.rebuild_served_rss('does-not-exist') is False


def test_rebuild_all_served_feeds_counts():
    for slug in ('rebuild-all-1', 'rebuild-all-2'):
        _seed(slug)
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()):
        count = mf.rebuild_all_served_feeds()
    assert count >= 2


def test_rebuild_embeds_feed_auth_key_and_preserves_episode_ids():
    # Stats/history are keyed by (slug, episode_id); the keyed rebuild must
    # emit identical episode ids, only the URLs change.
    import re as _re
    slug = 'rebuild-keyed'
    _seed(slug)
    key = 'd' * 64
    with patch.object(mf.rss_parser, 'fetch_feed', return_value=_feed_xml()):
        assert mf.rebuild_served_rss(slug) is True
        keyless = mf.storage.get_rss(slug)
        mf.db.set_setting('feed_auth_enabled', 'true', is_default=False)
        mf.db.set_setting('feed_auth_key', key, is_default=False)
        try:
            assert mf.rebuild_served_rss(slug) is True
            keyed = mf.storage.get_rss(slug)
        finally:
            mf.db.set_setting('feed_auth_enabled', 'false', is_default=False)
    id_re = _re.compile(r'/episodes/%s/([0-9a-f]{12})' % slug)
    assert f'?key={key}' in keyed and '?key=' not in keyless
    assert sorted(set(id_re.findall(keyless))) == sorted(set(id_re.findall(keyed)))

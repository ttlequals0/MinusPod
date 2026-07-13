"""Cover-art MinusPod badge overlay (issue #420).

Covers the compositor, the cached watermark variant in Storage, and the served
RSS feed pointing its channel image at the badge-overlaid endpoint only when the
toggle is on and the cover is cached.
"""
import atexit
import io
import os
import shutil
import sys
import tempfile

from PIL import Image

_test_data_dir = tempfile.mkdtemp(prefix='watermark_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
import artwork_watermark
from rss_parser import RSSParser

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage._instance = None
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)
atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

st = storage_mod.Storage()

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _png(color=(255, 255, 255), size=(400, 400)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def _region_mean(img, box):
    px = list(img.crop(box).getdata())
    return sum(sum(p) / 3 for p in px) / len(px)


# --- compositor -----------------------------------------------------------

def test_composite_outputs_jpeg_and_darkens_bottom_right():
    out = artwork_watermark.composite_watermark(_png())
    assert out is not None
    composed = Image.open(io.BytesIO(out))
    assert composed.format == 'JPEG'
    assert composed.size == (400, 400)

    composed = composed.convert('RGB')
    top_left = _region_mean(composed, (0, 0, 60, 60))
    bottom_right = _region_mean(composed, (315, 315, 380, 380))  # badge bounds
    assert top_left > 240                       # white cover left untouched
    assert bottom_right < top_left - 5          # badge darkened the corner


def test_composite_badge_has_solid_dark_backing():
    # Regression for issue #420: the badge was the colorful waveform on a
    # transparent background, so it vanished on dark/busy covers. The fix gives
    # it a solid dark chip behind the mark. On a white cover the old badge left
    # the corner white (only thin pastel bars); the chip fills it dark.
    out = artwork_watermark.composite_watermark(_png())  # white cover
    assert out is not None
    composed = Image.open(io.BytesIO(out)).convert('RGB')
    badge = composed.crop((300, 300, 392, 392))  # badge bounds, bottom-right
    lum = [sum(p) / 3 for p in badge.getdata()]
    dark_frac = sum(1 for v in lum if v < 80) / len(lum)
    assert dark_frac > 0.4                       # chip fill dominates the corner
    assert min(lum) < 40                         # the fill is genuinely dark


def test_composite_badge_visible_on_black_cover():
    # Issue #514: the near-black chip disappeared on black cover art. The
    # hulu-green halo behind the chip keeps the badge visible there.
    out = artwork_watermark.composite_watermark(_png(color=(0, 0, 0)))
    assert out is not None
    composed = Image.open(io.BytesIO(out)).convert('RGB')
    badge = composed.crop((280, 280, 400, 400))  # badge corner incl. halo
    px = list(badge.getdata())
    green = sum(1 for r, g, b in px if g > 60 and g > r + 25 and g > b + 25)
    assert green / len(px) > 0.05                # the halo reads as green
    top_left = _region_mean(composed, (0, 0, 60, 60))
    assert top_left < 10                         # black cover left untouched


def test_composite_returns_none_when_badge_missing(monkeypatch):
    monkeypatch.setattr(artwork_watermark, 'badge_path', lambda: None)
    assert artwork_watermark.composite_watermark(_png()) is None


# --- storage cache --------------------------------------------------------

def test_watermarked_artwork_is_generated_and_cached():
    slug = 'wm-feed'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')

    result = st.get_watermarked_artwork(slug)
    assert result is not None
    data, content_type = result
    assert content_type == 'image/jpeg'
    assert Image.open(io.BytesIO(data)).format == 'JPEG'

    variant = st.get_podcast_dir(slug) / 'artwork-minuspod.jpg'
    assert variant.exists()
    # Second call serves the cached bytes unchanged.
    assert st.get_watermarked_artwork(slug)[0] == data


def test_saving_new_artwork_invalidates_the_watermark_cache():
    slug = 'wm-invalidate'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')
    st.get_watermarked_artwork(slug)
    variant = st.get_podcast_dir(slug) / 'artwork-minuspod.jpg'
    assert variant.exists()

    st.save_artwork(slug, _png((0, 0, 0)), 'image/png', 'https://example.com/art2.png')
    assert not variant.exists()


def test_clear_watermark_cache_forces_recomposite():
    # Regression for issue #420: refreshing artwork must replace an existing
    # badge even when the source cover is unchanged (download_artwork no-ops on a
    # cache hit, so save_artwork never runs to drop the variant).
    slug = 'wm-recomposite'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')
    st.get_watermarked_artwork(slug)
    variant = st.get_podcast_dir(slug) / 'artwork-minuspod.jpg'
    assert variant.exists()

    st.clear_watermark_cache(slug)
    assert not variant.exists()

    assert st.get_watermarked_artwork(slug) is not None
    assert variant.exists()


def test_get_watermarked_artwork_none_without_source():
    slug = 'wm-noart'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    assert st.get_watermarked_artwork(slug) is None
    assert st.has_artwork(slug) is False


# --- feed rewrite ---------------------------------------------------------

def _feed():
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


def _serve(slug, watermark):
    return RSSParser(base_url="https://mp.example.com").modify_feed(
        _feed(), slug, storage=st, watermark_artwork=watermark)


def test_feed_points_at_badge_endpoint_when_enabled_and_cached():
    slug = 'wm-served'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')

    served = _serve(slug, watermark=True)
    token = st.artwork_version(slug)
    assert token
    # 2.32.5 invariant: the token is in the path and the URL ends in .jpg, so
    # Pocket Casts / Apple accept it (a ?v= query would be rejected).
    assert f'https://mp.example.com/{slug}/cover-minuspod-{token}.jpg' in served
    assert '?v=' not in served
    assert 'https://example.com/art.png' not in served


def test_feed_uses_upstream_art_when_disabled():
    slug = 'wm-served-off'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png(), 'image/png', 'https://example.com/art.png')

    served = _serve(slug, watermark=False)
    assert 'https://example.com/art.png' in served
    assert 'cover-minuspod' not in served


def test_feed_falls_back_to_upstream_when_no_cached_art():
    slug = 'wm-served-noart'
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)

    served = _serve(slug, watermark=True)
    assert 'https://example.com/art.png' in served
    assert 'cover-minuspod' not in served

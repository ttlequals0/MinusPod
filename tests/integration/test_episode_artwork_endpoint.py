"""The episode artwork proxy, end to end (#617).

Publishers reject a browser's cross-site Referer, so the cover is fetched server-side and served
from MinusPod's origin. The URL is read from the episode row, never the caller."""
import struct
import zlib
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('epart_test_', secret_key='epart-test-secret')

import api as api_module  # noqa: E402
import database  # noqa: E402
import storage as storage_module  # noqa: E402
from api.episodes.core import get_episode_artwork  # noqa: E402
from main_app import app  # noqa: E402


SLUG = 'artwork-feed'
EP = 'a1b2c3d4e5f6'
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
COVER_URL = 'https://cdn.example/ep1.jpg'
LOCAL_EP = 'b1b2c3d4e5f6'
SHARED_EP = 'c1b2c3d4e5f6'
IMMUTABLE = 'private, max-age=604800, immutable'


def _image(side=800, fmt='JPEG'):
    out = BytesIO()
    Image.effect_noise((side, side), 90).convert('RGB').save(out, fmt)
    return out.getvalue()


SOURCE = _image()


def _clear_cached_covers():
    """Each test decides whether a cover is on disk, so start from empty."""
    art_dir = storage_module.Storage()._episode_artwork_dir(SLUG)
    if art_dir and art_dir.is_dir():
        for path in art_dir.iterdir():
            if path.is_file():
                path.unlink()


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    _clear_cached_covers()
    storage_module.Storage()._artwork_failure_cache.clear()

    conn = db.get_connection()
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()
    podcast_id = db.create_podcast(slug=SLUG, source_url='https://example.com/rss.xml',
                                   title='Artwork Feed')
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, title, original_url, artwork_url) "
        "VALUES (?, ?, 'E1', 'https://e/1.mp3', 'https://cdn.example/ep1.jpg')",
        (podcast_id, EP))
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, title, original_url, artwork_url) "
        "VALUES (?, ?, 'E2', 'https://e/2.mp3', 'https://cdn.example/ep1.jpg')",
        (podcast_id, SHARED_EP))
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, title, original_url) "
        "VALUES (?, ?, 'E3', 'https://e/3.mp3')",
        (podcast_id, LOCAL_EP))
    conn.commit()

    with app.test_client() as c:
        yield c

    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()


def _url(episode_id=EP, slug=SLUG):
    return f'/api/v1/feeds/{slug}/episodes/{episode_id}/artwork'


def _fetching(storage, data=SOURCE, mime='image/jpeg'):
    """Stand in for the network: the validated fetch returns generated image bytes."""
    return patch.object(storage, '_fetch_validated_artwork', return_value=(data, mime))


def _cache_url_cover(storage, data=SOURCE):
    storage._write_cover(SLUG, storage_module.artwork_url_key(COVER_URL), data, 'image/jpeg')


def test_a_cached_cover_is_served_from_our_own_origin(client):
    _cache_url_cover(storage_module.Storage())

    response = client.get(_url())

    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'
    assert response.data == SOURCE


def test_a_cached_cover_cannot_be_sniffed_into_something_executable(client):
    _cache_url_cover(storage_module.Storage())

    response = client.get(_url())

    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Content-Security-Policy'] == "default-src 'none'"


def test_a_cold_cover_is_fetched_once_and_then_served(client):
    storage = storage_module.Storage()
    with _fetching(storage) as fetch:
        assert client.get(_url()).status_code == 200
        assert client.get(_url()).status_code == 200

    assert fetch.call_count == 1


def test_episodes_sharing_a_cover_url_download_and_store_it_once(client):
    storage = storage_module.Storage()
    with _fetching(storage) as fetch:
        assert client.get(_url()).status_code == 200
        assert client.get(_url(SHARED_EP)).status_code == 200
        assert client.get(_url(SHARED_EP) + '?size=thumb').status_code == 200

    assert fetch.call_count == 1
    stored = [p.name for p in storage._episode_artwork_dir(SLUG).iterdir()]
    key = storage_module.artwork_url_key(COVER_URL)
    assert sorted(stored) == [f'{key}.jpg', f'{key}.thumb.jpg']


def test_the_fetch_uses_the_stored_url_not_anything_the_caller_supplies(client):
    storage = storage_module.Storage()
    with patch.object(storage, '_fetch_validated_artwork', return_value=None) as fetch:
        client.get(_url() + '?url=https://evil.example/pwn.jpg')

    assert fetch.call_args[0][2] == COVER_URL


def test_a_blocked_cover_falls_back_to_the_show_cover(client):
    # Better a show cover than the grey placeholder the hotlink block produced.
    storage = storage_module.Storage()
    with patch.object(storage, '_fetch_validated_artwork', return_value=None):
        response = client.get(_url())

    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/api/v1/feeds/{SLUG}/artwork')
    assert 'max-age' not in response.headers['Cache-Control']
    assert response.headers['Cache-Control'] == 'no-cache'


def test_a_thumbnail_is_small_and_the_full_size_file_is_not_stored(client):
    storage = storage_module.Storage()
    with _fetching(storage):
        response = client.get(_url() + '?size=thumb')

    assert response.status_code == 200
    with Image.open(BytesIO(response.data)) as thumb:
        assert max(thumb.size) <= 256
    assert len(response.data) < len(SOURCE)
    key = storage_module.artwork_url_key(COVER_URL)
    assert [p.name for p in storage._episode_artwork_dir(SLUG).iterdir()] == [f'{key}.thumb.jpg']


def test_a_thumbnail_is_derived_from_an_already_cached_full_size_cover(client):
    storage = storage_module.Storage()
    _cache_url_cover(storage)
    with _fetching(storage) as fetch:
        response = client.get(_url() + '?size=thumb')

    assert response.status_code == 200
    assert fetch.call_count == 0


def test_a_small_cover_is_never_upscaled(client):
    storage = storage_module.Storage()
    with _fetching(storage, data=_image(100)):
        response = client.get(_url() + '?size=thumb')

    with Image.open(BytesIO(response.data)) as thumb:
        assert thumb.size == (100, 100)


def test_an_image_that_cannot_be_thumbnailed_is_served_as_is(client, caplog):
    storage = storage_module.Storage()
    storage._cover_thumb_warned.clear()
    broken = b'\xff\xd8\xff\xe0' + b'\x00' * 64
    with _fetching(storage, data=broken):
        with caplog.at_level('WARNING', logger='podcast.storage'):
            first = client.get(_url() + '?size=thumb')
            second = client.get(_url() + '?size=thumb')

    assert first.status_code == 200 and first.data == broken
    assert second.data == broken
    warnings = [r for r in caplog.records if 'thumbnail failed' in r.getMessage()]
    assert len(warnings) == 1


def _header_only_png(width, height):
    """A few dozen bytes that declare a huge bitmap; nothing can decode it."""
    ihdr = b'IHDR' + struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    idat = b'IDAT' + zlib.compress(b'\x00')
    return (b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + ihdr + struct.pack('>I', zlib.crc32(ihdr))
            + struct.pack('>I', len(idat) - 4) + idat + struct.pack('>I', zlib.crc32(idat)))


@pytest.mark.parametrize('declared', [(9000, 9000), (30000, 30000)])
def test_an_oversized_cover_is_never_decoded_and_is_not_refetched(client, declared):
    storage = storage_module.Storage()
    storage._cover_thumb_warned.clear()
    huge = _header_only_png(*declared)
    with _fetching(storage, data=huge, mime='image/png') as fetch:
        with patch.object(Image.Image, 'load', side_effect=AssertionError('decoded')) as load:
            first = client.get(_url() + '?size=thumb')
            second = client.get(_url() + '?size=thumb')

    assert first.status_code == second.status_code == 302
    assert first.headers['Location'].endswith(f'/api/v1/feeds/{SLUG}/artwork')
    assert fetch.call_count == 1
    assert load.call_count == 0
    assert list(storage._episode_artwork_dir(SLUG).iterdir()) == []


def test_a_cover_over_a_lowered_pixel_cap_is_refused_and_decoded_once(client, monkeypatch):
    storage = storage_module.Storage()
    monkeypatch.setattr(storage_module, 'COVER_MAX_DECODE_PIXELS', 100 * 100)
    with _fetching(storage) as fetch, \
            patch.object(storage_module, 'make_cover_thumbnail',
                         wraps=storage_module.make_cover_thumbnail) as make:
        assert client.get(_url() + '?size=thumb').status_code == 302
        assert client.get(_url() + '?size=thumb').status_code == 302

    assert fetch.call_count == 1
    assert make.call_count == 1


def test_a_failed_thumbnail_is_not_decoded_again_on_the_next_request(client):
    storage = storage_module.Storage()
    broken = b'\xff\xd8\xff\xe0' + b'\x00' * 64
    with _fetching(storage, data=broken) as fetch, \
            patch.object(storage_module, 'make_cover_thumbnail',
                         wraps=storage_module.make_cover_thumbnail) as make:
        client.get(_url() + '?size=thumb')
        second = client.get(_url() + '?size=thumb')

    assert second.data == broken
    assert fetch.call_count == 1
    assert make.call_count == 1


def test_a_jpeg_thumbnail_decodes_at_reduced_size():
    with patch.object(JpegImageFile, 'draft', autospec=True, side_effect=JpegImageFile.draft) as draft:
        storage_module.make_cover_thumbnail(SOURCE)

    assert draft.call_args_list[0].args[1:] == ("RGB", (256, 256))


def test_an_unknown_size_is_a_bad_request(client):
    assert client.get(_url() + '?size=huge').status_code == 400
    assert client.get(_url() + '?size=').status_code == 400


def test_a_matching_version_allows_a_week_of_browser_caching(client):
    _cache_url_cover(storage_module.Storage())
    version = storage_module.artwork_url_version(COVER_URL)

    response = client.get(_url() + f'?v={version}')

    assert response.headers['Cache-Control'] == IMMUTABLE
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Content-Security-Policy'] == "default-src 'none'"


@pytest.mark.parametrize('query', ['', '?v=000000000000', '?size=thumb'])
def test_no_or_wrong_version_keeps_revalidating(client, query):
    _cache_url_cover(storage_module.Storage())

    response = client.get(_url() + query)

    assert response.headers['Cache-Control'] == 'no-cache'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_a_local_cover_keeps_its_episode_key_and_serves_a_thumbnail(client):
    storage = storage_module.Storage()
    storage.save_episode_artwork(SLUG, LOCAL_EP, SOURCE, 'image/jpeg', evict=False)
    version = storage.local_artwork_version(SLUG, LOCAL_EP)

    full = client.get(_url(LOCAL_EP) + f'?v={version}')
    thumb = client.get(_url(LOCAL_EP) + f'?size=thumb&v={version}')

    assert full.data == SOURCE and full.headers['Cache-Control'] == IMMUTABLE
    with Image.open(BytesIO(thumb.data)) as img:
        assert max(img.size) <= 256
    assert thumb.headers['Cache-Control'] == IMMUTABLE
    names = sorted(p.name for p in storage._episode_artwork_dir(SLUG).iterdir())
    assert names == [f'{LOCAL_EP}.jpg', f'{LOCAL_EP}.thumb.jpg']


def test_replacing_a_local_cover_drops_its_stale_thumbnail(client):
    storage = storage_module.Storage()
    storage.save_episode_artwork(SLUG, LOCAL_EP, SOURCE, 'image/jpeg', evict=False)
    client.get(_url(LOCAL_EP) + '?size=thumb')
    storage.save_episode_artwork(SLUG, LOCAL_EP, _image(300), 'image/jpeg', evict=False)

    names = [p.name for p in storage._episode_artwork_dir(SLUG).iterdir()]
    assert names == [f'{LOCAL_EP}.jpg']


def test_the_route_limit_is_3000_per_minute(client):
    key = f'{get_episode_artwork.__module__}.{get_episode_artwork.__name__}.{get_episode_artwork.__name__}'
    limits = list(api_module.limiter.limit_manager._decorated_limits[key])
    assert [lim.limit_provider for lim in limits] == ['3000 per minute']
    assert limits[0].override_defaults


def test_an_episode_with_no_cover_falls_back_to_the_show_cover(client):
    response = client.get(_url(episode_id='ffffffffffff'))

    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/api/v1/feeds/{SLUG}/artwork')


@pytest.mark.parametrize('episode_id', [
    '..', '%2e%2e', 'a%2f..%2fb', 'NOTHEXAT_ALL', 'a' * 64,
])
def test_a_traversal_payload_cannot_reach_the_filesystem(client, episode_id):
    # The blueprint-wide episode_id guard in api/__init__.py refuses anything
    # off the 12-hex shape before a handler runs, so this route inherits it.
    # Asserted here anyway: the route puts the value into a filename.
    _cache_url_cover(storage_module.Storage(), JPEG)

    response = client.get(_url(episode_id=episode_id))

    assert response.status_code in (400, 404)
    assert response.data != JPEG


def _thumb_address(episode_id, version):
    return f'/api/v1/feeds/{SLUG}/episodes/{episode_id}/artwork?size=thumb&v={version}'


def test_the_episode_list_and_detail_carry_the_thumbnail_address(client):
    storage = storage_module.Storage()
    storage.save_episode_artwork(SLUG, LOCAL_EP, SOURCE, 'image/jpeg', evict=False)
    url_version = storage_module.artwork_url_version(COVER_URL)

    listing = client.get(f'/api/v1/feeds/{SLUG}/episodes').get_json()
    by_id = {e['id']: e for e in listing['episodes']}
    detail = client.get(f'/api/v1/feeds/{SLUG}/episodes/{EP}').get_json()

    assert by_id[EP]['artworkThumbUrl'] == _thumb_address(EP, url_version)
    assert by_id[SHARED_EP]['artworkThumbUrl'] == _thumb_address(SHARED_EP, url_version)
    assert detail['artworkThumbUrl'] == _thumb_address(EP, url_version)
    # A subscribed feed's episode without a publisher URL has no cover, so no address.
    assert by_id[LOCAL_EP]['artworkThumbUrl'] is None


def test_the_feeds_dashboard_projection_carries_the_thumbnail_address(client):
    feeds = client.get('/api/v1/feeds?includeLatestEpisodes=true').get_json()['feeds']
    episodes = next(f for f in feeds if f['slug'] == SLUG)['latestEpisodes']
    version = storage_module.artwork_url_version(COVER_URL)

    assert next(e for e in episodes if e['id'] == EP)['artworkThumbUrl'] == _thumb_address(EP, version)

"""Episode covers are cached locally so publishers cannot block them (#617)."""
import os
import sys
import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import storage as storage_module  # noqa: E402
from storage import Storage  # noqa: E402


# Smallest valid image per magic-number check; content does not matter here.
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
JPEG = (b'\xff\xd8\xff\xe0' + b'\x00' * 64)
EP = 'a1b2c3d4e5f6'
SLUG = 'example-feed'
URL = 'https://cdn.example/ep1.jpg'


def _cached_cover(storage, slug, episode_id):
    found = storage.episode_artwork_file(slug, episode_id)
    return (found[0].read_bytes(), found[1]) if found else None


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, '_EVICT_SCAN_INTERVAL_SECONDS', 0)
    Storage._instance = None
    s = Storage(str(tmp_path))
    s._artwork_failure_cache.clear()
    yield s
    Storage._instance = None


def test_saved_cover_round_trips(storage):
    assert storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg') is True
    assert _cached_cover(storage, SLUG, EP) == (JPEG, 'image/jpeg')


@pytest.mark.parametrize('image,extension,content_type', [
    (PNG, 'png', 'image/png'), (JPEG, 'jpg', 'image/jpeg'),
])
def test_chapter_images_are_content_addressed_and_local(storage, image, extension, content_type):
    name = f'{hashlib.sha256(image).hexdigest()}.{extension}'
    url = storage.save_chapter_image(SLUG, EP, image)
    assert url == f'/episodes/{SLUG}/{EP}/chapter-images/{name}'
    assert storage.get_chapter_image(SLUG, EP, name) == (image, content_type)
    assert storage.save_chapter_image(SLUG, EP, image) == url
    assert len(list(storage._chapter_images_dir(SLUG, EP).iterdir())) == 1


@pytest.mark.parametrize('image', [b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'GIF89a' + PNG[6:]])
def test_non_jpeg_png_chapter_images_are_not_served(storage, image):
    assert storage.save_chapter_image(SLUG, EP, image) is None
    assert storage._chapter_images_dir(SLUG, EP) is None


def test_oversized_chapter_image_does_not_create_an_asset(storage):
    with patch.object(storage_module, '_max_artwork_bytes', return_value=len(PNG) - 1):
        assert storage.save_chapter_image(SLUG, EP, PNG) is None
    assert storage._chapter_images_dir(SLUG, EP) is None


def test_failed_image_replace_preserves_previous_chapter_asset(storage):
    url = storage.save_chapter_image(SLUG, EP, PNG)
    with patch.object(storage_module.os, 'replace', side_effect=OSError):
        assert storage.save_chapter_image(SLUG, EP, JPEG) is None
    assert storage.get_chapter_image(SLUG, EP, url.rsplit('/', 1)[-1]) == (PNG, 'image/png')
    assert len(list(storage._chapter_images_dir(SLUG, EP).iterdir())) == 1


def test_chapter_asset_reads_do_not_create_directories(storage):
    name = f'{hashlib.sha256(PNG).hexdigest()}.png'
    assert storage.get_chapter_image('unknown-feed', EP, name) is None
    assert not (storage.podcasts_dir / 'unknown-feed').exists()


@pytest.mark.parametrize('name', ['../image.png', 'image.svg', 'a' * 64 + '.webp', 'a' * 65 + '.png'])
def test_chapter_image_names_cannot_traverse_or_select_unsafe_formats(storage, name):
    assert storage.get_chapter_image(SLUG, EP, name) is None


def test_chapter_image_symlink_cannot_escape_its_episode_directory(storage, tmp_path):
    url = storage.save_chapter_image(SLUG, EP, PNG)
    name = url.rsplit('/', 1)[-1]
    path = storage._chapter_images_dir(SLUG, EP) / name
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    path.unlink()
    path.symlink_to(outside)
    assert storage.get_chapter_image(SLUG, EP, name) is None
    assert outside.read_bytes() == PNG


def test_chapter_image_modified_or_spoofed_bytes_are_not_served(storage):
    url = storage.save_chapter_image(SLUG, EP, PNG)
    name = url.rsplit('/', 1)[-1]
    path = storage._chapter_images_dir(SLUG, EP) / name
    path.write_bytes(JPEG)
    assert storage.get_chapter_image(SLUG, EP, name) is None


def test_chapter_asset_reads_enforce_the_current_image_size_limit(storage):
    url = storage.save_chapter_image(SLUG, EP, PNG)
    with patch.object(storage_module, '_max_artwork_bytes', return_value=len(PNG) - 1):
        assert storage.get_chapter_image(SLUG, EP, url.rsplit('/', 1)[-1]) is None


def test_episode_cleanup_removes_chapter_images_and_counts_bytes(storage):
    storage.save_chapter_image(SLUG, EP, PNG)
    assert storage.cleanup_episode_files(SLUG, EP) == len(PNG)
    assert not storage._chapter_images_dir(SLUG, EP).exists()


def test_audio_only_delete_preserves_referenced_chapter_images(storage):
    url = storage.save_chapter_image(SLUG, EP, PNG)
    storage.get_episode_path(SLUG, EP).write_bytes(b'audio')
    storage.delete_processed_file(SLUG, EP, keep_original=True)
    assert storage.get_chapter_image(SLUG, EP, url.rsplit('/', 1)[-1]) == (PNG, 'image/png')


def test_concurrent_chapter_image_writes_are_atomic(storage):
    with ThreadPoolExecutor(max_workers=8) as pool:
        urls = list(pool.map(lambda _: storage.save_chapter_image(SLUG, EP, PNG), range(16)))
    assert None not in urls
    assert len(set(urls)) == 1
    assert storage.get_chapter_image(SLUG, EP, urls[0].rsplit('/', 1)[-1]) == (PNG, 'image/png')
    assert len(list(storage._chapter_images_dir(SLUG, EP).iterdir())) == 1


def test_failed_image_cleanup_does_not_abort_audio_cleanup(storage):
    storage.save_chapter_image(SLUG, EP, PNG)
    audio = storage.get_episode_path(SLUG, EP)
    audio.write_bytes(b'audio')
    with patch.object(storage_module.shutil, 'rmtree', side_effect=OSError):
        assert storage.cleanup_episode_files(SLUG, EP) == 5
    assert not audio.exists()
    assert storage._chapter_images_dir(SLUG, EP).exists()


def test_image_cleanup_does_not_follow_an_escaping_directory(storage, tmp_path):
    storage.save_chapter_image(SLUG, EP, PNG)
    path = storage._chapter_images_dir(SLUG, EP)
    storage.remove_chapter_images(SLUG, EP)
    outside = tmp_path / 'outside-images'
    outside.mkdir()
    (outside / 'keep.png').write_bytes(PNG)
    path.symlink_to(outside)
    assert storage.remove_chapter_images(SLUG, EP) == 0
    assert (outside / 'keep.png').read_bytes() == PNG


def test_missing_cover_reads_as_none(storage):
    assert _cached_cover(storage, SLUG, EP) is None


def test_reading_an_unknown_feed_does_not_create_its_directory(storage):
    assert _cached_cover(storage, 'never-seen', EP) is None
    assert not (storage.podcasts_dir / 'never-seen').exists()


def test_a_new_format_replaces_the_old_file(storage):
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    storage._save_episode_artwork(SLUG, EP, PNG, 'image/png')

    assert _cached_cover(storage, SLUG, EP) == (PNG, 'image/png')
    art_dir = storage._episode_artwork_dir(SLUG)
    assert not (art_dir / f'{EP}.jpg').exists()


@pytest.mark.parametrize('episode_id', ['../escape', 'a/b', '', 'NOTHEX', 'a' * 40])
def test_a_malformed_episode_id_is_refused(storage, episode_id):
    # The id becomes a filename, so anything off the 12-hex shape is rejected
    # rather than sanitised into something that might still escape.
    assert _cached_cover(storage, SLUG, episode_id) is None
    assert storage.download_episode_artwork(SLUG, episode_id, URL) is False


def test_a_blocked_url_is_not_refetched_on_every_page_load(storage):
    with patch.object(storage, '_download_episode_artwork_uncached',
                      return_value=False) as fetch:
        for _ in range(3):
            assert storage.download_episode_artwork(SLUG, EP, URL) is False
    assert fetch.call_count == 1


def test_a_changed_url_retries_immediately(storage):
    with patch.object(storage, '_download_episode_artwork_uncached',
                      return_value=False) as fetch:
        storage.download_episode_artwork(SLUG, EP, URL)
        storage.download_episode_artwork(SLUG, EP, 'https://cdn.example/ep1-v2.jpg')
    assert fetch.call_count == 2


def test_downloading_a_cover_leaves_the_badged_show_cover_alone(storage):
    # The badge variant is podcast-level; re-rendering it on every episode
    # fetch would throw away work the cover route depends on.
    with patch.object(storage, 'clear_watermark_cache') as clear:
        storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    clear.assert_not_called()


def test_eviction_drops_the_least_recently_served_covers(storage, monkeypatch):
    blob = b'\xff\xd8\xff\xe0' + b'\x00' * 96  # 100 bytes each

    # Roomy while seeding, so the fixtures survive to be measured.
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 10_000)
    ids = ['aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc']
    art_dir = storage._episode_artwork_dir(SLUG, create=True)
    for i, ep in enumerate(ids):
        storage._save_episode_artwork(SLUG, ep, blob, 'image/jpeg')
        os.utime(art_dir / f'{ep}.jpg', (1_000 + i, 1_000 + i))

    # Serving the oldest makes it the newest, so the next write evicts the
    # ones nobody asked for rather than the one that is actually in use.
    _cached_cover(storage, SLUG, ids[0])

    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 200)
    storage._save_episode_artwork(SLUG, 'dddddddddddd', blob, 'image/jpeg')

    surviving = {p.stem for p in art_dir.glob('*.jpg')}
    assert surviving == {ids[0], 'dddddddddddd'}


def test_evict_false_preserves_older_covers_over_the_cap(storage, monkeypatch):
    """Local-feed episode artwork is the only copy (no upstream URL to re-download), so
    save_episode_artwork(..., evict=False) skips the LRU trim even when the directory is far over the cap."""
    blob = b'\xff\xd8\xff\xe0' + b'\x00' * 96  # 100 bytes each

    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 10_000)
    ids = ['aaaaaaaaaaaa', 'bbbbbbbbbbbb']
    for ep in ids:
        storage._save_episode_artwork(SLUG, ep, blob, 'image/jpeg')

    art_dir = storage._episode_artwork_dir(SLUG)
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 50)
    result = storage.save_episode_artwork(SLUG, 'cccccccccccc', blob, 'image/jpeg', evict=False)

    assert result is True
    surviving = {p.stem for p in art_dir.glob('*.jpg')}
    assert surviving == {ids[0], ids[1], 'cccccccccccc'}


def test_cache_stays_under_the_cap(storage, monkeypatch):
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 250)
    blob = b'\xff\xd8\xff\xe0' + b'\x00' * 96

    for n in range(10):
        storage._save_episode_artwork(SLUG, f'{n:012x}', blob, 'image/jpeg')

    art_dir = storage._episode_artwork_dir(SLUG)
    total = sum(p.stat().st_size for p in art_dir.iterdir() if p.is_file())
    assert total <= 250


def test_traversal_episode_id_never_reaches_the_filesystem(storage):
    # Invalid shape is rejected before any path join; a hostile id that
    # somehow got past shape validation is stopped by path containment.
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    assert _cached_cover(storage, SLUG, '../../secrets') is None


def test_has_episode_artwork_reflects_existence(storage):
    assert storage.has_episode_artwork(SLUG, EP) is False
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    assert storage.has_episode_artwork(SLUG, EP) is True


def test_has_episode_artwork_does_not_read_or_touch_mtime(storage):
    # Existence-only check (task-5 review fix #4): must not read the file
    # or bump its mtime the way episode_artwork_file's LRU touch does.
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    art_dir = storage._episode_artwork_dir(SLUG)
    path = art_dir / f'{EP}.jpg'
    os.utime(path, (1_000, 1_000))

    assert storage.has_episode_artwork(SLUG, EP) is True

    assert path.stat().st_mtime == 1_000


def test_has_episode_artwork_rejects_malformed_id(storage):
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    assert storage.has_episode_artwork(SLUG, '../../secrets') is False


def test_has_episode_artwork_on_unknown_feed_creates_no_directory(storage):
    assert storage.has_episode_artwork('never-seen', EP) is False
    assert not (storage.podcasts_dir / 'never-seen').exists()


def test_cleanup_podcast_dir_refuses_traversal_slug(storage, tmp_path):
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / 'data.txt').write_text('keep me')

    assert storage.cleanup_podcast_dir('../victim') is False
    assert (victim / 'data.txt').exists()


def test_deleting_an_episode_leaves_the_shared_url_keyed_cover(storage):
    key = storage_module.artwork_url_key(URL)
    storage._write_cover(SLUG, key, JPEG, 'image/jpeg')
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')

    assert storage.remove_episode_artwork(SLUG, EP) is True

    assert storage.url_artwork_file(SLUG, URL) is not None
    assert storage.has_episode_artwork(SLUG, EP) is False


def test_old_episode_keyed_files_fall_out_of_a_full_cache_with_the_url_keyed_ones(storage, monkeypatch):
    blob = b'\xff\xd8\xff\xe0' + b'\x00' * 96  # 100 bytes each
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 10_000)
    art_dir = storage._episode_artwork_dir(SLUG, create=True)
    storage._save_episode_artwork(SLUG, EP, blob, 'image/jpeg')
    os.utime(art_dir / f'{EP}.jpg', (1_000, 1_000))

    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 250)
    for n in range(3):
        storage._write_cover(SLUG, storage_module.artwork_url_key(f'{URL}{n}'), blob, 'image/jpeg')

    assert not (art_dir / f'{EP}.jpg').exists()
    assert sum(p.stat().st_size for p in art_dir.iterdir()) <= 250


def test_local_covers_and_their_thumbnails_survive_a_full_cache(storage, monkeypatch):
    local = _png(400)
    storage.save_episode_artwork(SLUG, EP, local, 'image/png', evict=False)
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 50)

    assert storage.local_artwork_file(SLUG, EP, thumb=True) is not None

    names = {p.name for p in storage._episode_artwork_dir(SLUG).iterdir()}
    assert names == {f'{EP}.png', f'{EP}.thumb.png'}


def test_thumbnails_count_toward_the_cap_and_are_evictable(storage, monkeypatch):
    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 1)
    storage._write_cover(SLUG, 'a' * 64 + '.thumb', JPEG, 'image/jpeg')

    assert list(storage._episode_artwork_dir(SLUG).iterdir()) == []


def _png(side):
    out = BytesIO()
    Image.new('RGBA', (side, side), (200, 30, 30, 128)).save(out, 'PNG')
    return out.getvalue()


def test_thumbnails_keep_alpha_and_never_exceed_256_px():
    thumb, mime = storage_module.make_cover_thumbnail(_png(1000))

    assert mime == 'image/png'
    with Image.open(BytesIO(thumb)) as img:
        assert img.size == (256, 256)


def test_saves_inside_the_scan_window_scan_the_directory_once(storage, monkeypatch):
    monkeypatch.setattr(storage_module, '_EVICT_SCAN_INTERVAL_SECONDS', 60)
    art_dir = storage._episode_artwork_dir(SLUG, create=True)
    with patch.object(type(art_dir), 'iterdir', autospec=True, side_effect=type(art_dir).iterdir) as listing:
        storage._write_cover(SLUG, 'a' * 64, JPEG, 'image/jpeg')
        storage._write_cover(SLUG, 'b' * 64, JPEG, 'image/jpeg')

    assert listing.call_count == 1


def test_versions_for_a_page_match_the_per_episode_version(storage):
    storage.save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg', evict=False)

    assert storage.local_artwork_versions(SLUG, [EP, 'ffffffffffff']) == {
        EP: storage.local_artwork_version(SLUG, EP)}


def test_a_served_thumbnail_is_touched_and_survives_eviction(storage, monkeypatch):
    with patch.object(storage, '_fetch_validated_artwork', return_value=(_png(600), 'image/png')):
        thumb_path = storage.cache_url_artwork(SLUG, URL, thumb=True)[0]
    art_dir = storage._episode_artwork_dir(SLUG)
    unserved = art_dir / ('b' * 64 + '.png')
    unserved.write_bytes(b'x' * 200)
    old = 1_000
    for path in (thumb_path, unserved):
        os.utime(path, (old, old))

    assert storage.cache_url_artwork(SLUG, URL, thumb=True)[0] == thumb_path
    assert thumb_path.stat().st_mtime > old

    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', thumb_path.stat().st_size + 100)
    storage._write_cover(SLUG, 'c' * 64, b'y' * 10, 'image/png')
    assert thumb_path.exists() and not unserved.exists()


def test_an_episode_keyed_file_is_the_thumbnail_source_before_any_download(storage):
    storage._save_episode_artwork(SLUG, EP, _png(600), 'image/png')
    with patch.object(storage, '_fetch_validated_artwork') as fetch:
        found = storage.cache_url_artwork(SLUG, URL, thumb=True, episode_id=EP)

    assert fetch.call_count == 0
    with Image.open(found[0]) as img:
        assert max(img.size) <= 256


def test_without_an_episode_keyed_file_the_cover_is_downloaded(storage):
    with patch.object(storage, '_fetch_validated_artwork',
                      return_value=(_png(600), 'image/png')) as fetch:
        found = storage.cache_url_artwork(SLUG, URL, thumb=True, episode_id=EP)

    assert fetch.call_count == 1 and found is not None


@pytest.mark.parametrize('episode_id, scope', [(EP, f'{SLUG}:{EP}'), (None, SLUG)])
def test_cover_download_log_label(storage, caplog, episode_id, scope):
    version = storage_module.artwork_url_version(URL)
    with patch.object(storage, '_fetch_artwork_response', side_effect=storage_module.SSRFError('x')), \
            patch.object(storage.db, 'get_podcast_by_slug', return_value=None), \
            caplog.at_level('INFO', logger=storage_module.logger.name):
        storage._download_url_artwork(SLUG, URL, episode_id)

    assert f'[{scope} cover={version}] Downloading episode artwork' in caplog.text

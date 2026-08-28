"""Episode covers are cached locally so publishers cannot block them (#617)."""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import storage as storage_module  # noqa: E402
from storage import Storage  # noqa: E402


# Smallest valid image per magic-number check; content does not matter here.
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
JPEG = (b'\xff\xd8\xff\xe0' + b'\x00' * 64)
EP = 'a1b2c3d4e5f6'
SLUG = 'example-feed'
URL = 'https://cdn.example/ep1.jpg'


@pytest.fixture
def storage(tmp_path):
    Storage._instance = None
    s = Storage(str(tmp_path))
    s._artwork_failure_cache.clear()
    yield s
    Storage._instance = None


def test_saved_cover_round_trips(storage):
    assert storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg') is True
    assert storage.get_episode_artwork(SLUG, EP) == (JPEG, 'image/jpeg')


def test_missing_cover_reads_as_none(storage):
    assert storage.get_episode_artwork(SLUG, EP) is None


def test_reading_an_unknown_feed_does_not_create_its_directory(storage):
    assert storage.get_episode_artwork('never-seen', EP) is None
    assert not (storage.podcasts_dir / 'never-seen').exists()


def test_a_new_format_replaces_the_old_file(storage):
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    storage._save_episode_artwork(SLUG, EP, PNG, 'image/png')

    assert storage.get_episode_artwork(SLUG, EP) == (PNG, 'image/png')
    art_dir = storage._episode_artwork_dir(SLUG)
    assert not (art_dir / f'{EP}.jpg').exists()


@pytest.mark.parametrize('episode_id', ['../escape', 'a/b', '', 'NOTHEX', 'a' * 40])
def test_a_malformed_episode_id_is_refused(storage, episode_id):
    # The id becomes a filename, so anything off the 12-hex shape is rejected
    # rather than sanitised into something that might still escape.
    assert storage.get_episode_artwork(SLUG, episode_id) is None
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
    storage.get_episode_artwork(SLUG, ids[0])

    monkeypatch.setattr(storage_module, 'EPISODE_ARTWORK_CACHE_BYTES', 200)
    storage._save_episode_artwork(SLUG, 'dddddddddddd', blob, 'image/jpeg')

    surviving = {p.stem for p in art_dir.glob('*.jpg')}
    assert surviving == {ids[0], 'dddddddddddd'}


def test_evict_false_preserves_older_covers_over_the_cap(storage, monkeypatch):
    """Local-feed episode artwork (#625 Task 8) is the only copy of that
    cover -- there is no upstream URL to re-download it from -- so
    save_episode_artwork(..., evict=False) must skip the LRU trim
    entirely, even when the directory is left far over the cap."""
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
    assert storage.get_episode_artwork(SLUG, '../../secrets') is None


def test_has_episode_artwork_reflects_existence(storage):
    assert storage.has_episode_artwork(SLUG, EP) is False
    storage._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')
    assert storage.has_episode_artwork(SLUG, EP) is True


def test_has_episode_artwork_does_not_read_or_touch_mtime(storage):
    # Existence-only check (task-5 review fix #4): must not read the file
    # or bump its mtime the way get_episode_artwork's LRU touch does.
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

"""Tests for versioned reprocess mp3 filenames."""
import tempfile
from pathlib import Path

import pytest

from storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(data_dir=str(tmp_path))


def _make_episode_file(storage, slug, episode_id, version=None, content=b"x"):
    path = storage.get_episode_path(slug, episode_id, version=version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestGetEpisodePath:

    def test_unversioned_default(self, storage):
        path = storage.get_episode_path("pod", "abcdef123456")
        assert path.name == "abcdef123456.mp3"

    def test_version_zero_is_unversioned(self, storage):
        path = storage.get_episode_path("pod", "abcdef123456", version=0)
        assert path.name == "abcdef123456.mp3"

    def test_version_one(self, storage):
        path = storage.get_episode_path("pod", "abcdef123456", version=1)
        assert path.name == "abcdef123456-v1.mp3"

    def test_version_five(self, storage):
        path = storage.get_episode_path("pod", "abcdef123456", version=5)
        assert path.name == "abcdef123456-v5.mp3"


class TestIterEpisodeAudioPaths:

    def test_empty_when_no_files(self, storage):
        assert storage.iter_episode_audio_paths("pod", "abcdef123456") == []

    def test_returns_unversioned_and_versioned(self, storage):
        p0 = _make_episode_file(storage, "pod", "abc123def456")
        p1 = _make_episode_file(storage, "pod", "abc123def456", version=1)
        p2 = _make_episode_file(storage, "pod", "abc123def456", version=2)
        paths = storage.iter_episode_audio_paths("pod", "abc123def456")
        names = [p.name for p in paths]
        assert set(names) == {"abc123def456.mp3",
                              "abc123def456-v1.mp3",
                              "abc123def456-v2.mp3"}


class TestCleanupStaleAudioVersions:

    def test_noop_when_current_version_zero(self, storage):
        _make_episode_file(storage, "pod", "abc123def456")
        removed = storage.cleanup_stale_audio_versions("pod", "abc123def456", current_version=0)
        assert removed == 0
        assert storage.get_episode_path("pod", "abc123def456").exists()

    def test_first_reprocess_drops_unversioned(self, storage):
        _make_episode_file(storage, "pod", "abc123def456")          # unversioned (v0)
        _make_episode_file(storage, "pod", "abc123def456", version=1)
        removed = storage.cleanup_stale_audio_versions("pod", "abc123def456", current_version=1)
        assert removed == 1
        assert not storage.get_episode_path("pod", "abc123def456").exists()
        assert storage.get_episode_path("pod", "abc123def456", version=1).exists()

    def test_second_reprocess_drops_everything_prior(self, storage):
        _make_episode_file(storage, "pod", "abc123def456")
        _make_episode_file(storage, "pod", "abc123def456", version=1)
        _make_episode_file(storage, "pod", "abc123def456", version=2)
        removed = storage.cleanup_stale_audio_versions("pod", "abc123def456", current_version=2)
        assert removed == 2
        assert not storage.get_episode_path("pod", "abc123def456").exists()
        assert not storage.get_episode_path("pod", "abc123def456", version=1).exists()
        assert storage.get_episode_path("pod", "abc123def456", version=2).exists()

    def test_third_reprocess_keeps_only_current(self, storage):
        _make_episode_file(storage, "pod", "abc123def456")
        _make_episode_file(storage, "pod", "abc123def456", version=1)
        _make_episode_file(storage, "pod", "abc123def456", version=2)
        _make_episode_file(storage, "pod", "abc123def456", version=3)
        removed = storage.cleanup_stale_audio_versions("pod", "abc123def456", current_version=3)
        assert removed == 3
        assert not storage.get_episode_path("pod", "abc123def456").exists()
        assert not storage.get_episode_path("pod", "abc123def456", version=1).exists()
        assert not storage.get_episode_path("pod", "abc123def456", version=2).exists()
        assert storage.get_episode_path("pod", "abc123def456", version=3).exists()


class TestProcessedUrl:

    def test_unversioned(self):
        from api.episodes import _processed_url
        url = _processed_url("https://example.com", "pod", "abc123def456", 0)
        assert url == "https://example.com/episodes/pod/abc123def456.mp3"

    def test_versioned(self):
        from api.episodes import _processed_url
        url = _processed_url("https://example.com", "pod", "abc123def456", 3)
        assert url == "https://example.com/episodes/pod/abc123def456-v3.mp3"


class TestRssEnclosureUrl:

    def test_db_item_url_unversioned(self):
        from rss_parser import RSSParser
        parser = RSSParser.__new__(RSSParser)
        parser.base_url = "https://example.com"
        lines: list = []
        ep = {
            "episode_id": "abc123def456",
            "title": "T",
            "processed_version": 0,
        }
        parser._append_db_episode_item(lines, "pod", ep, storage=None)
        joined = "\n".join(lines)
        assert 'url="https://example.com/episodes/pod/abc123def456.mp3"' in joined

    def test_db_item_url_versioned(self):
        from rss_parser import RSSParser
        parser = RSSParser.__new__(RSSParser)
        parser.base_url = "https://example.com"
        lines: list = []
        ep = {
            "episode_id": "abc123def456",
            "title": "T",
            "processed_version": 2,
        }
        parser._append_db_episode_item(lines, "pod", ep, storage=None)
        joined = "\n".join(lines)
        assert 'url="https://example.com/episodes/pod/abc123def456-v2.mp3"' in joined

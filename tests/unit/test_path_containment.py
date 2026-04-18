"""Tests for slug / episode_id path containment in storage."""
import pytest

from storage import PathContainmentError, Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(data_dir=str(tmp_path))


def test_safe_slug_returns_path_inside_root(storage):
    path = storage.get_podcast_dir("valid-podcast")
    assert path.is_relative_to(storage.podcasts_dir.resolve())


@pytest.mark.parametrize("slug", ["../escape", "..%2fescape", "foo/bar", "foo\\bar", "with\x00null", ""])
def test_dangerous_slug_refused(storage, slug):
    with pytest.raises(PathContainmentError):
        storage.get_podcast_dir(slug)


def test_valid_episode_id_returns_path_inside_podcast(storage):
    slug = "a-pod"
    path = storage.get_episode_path(slug, "0123456789ab")
    assert path.is_relative_to(storage.get_podcast_dir(slug).resolve())


@pytest.mark.parametrize("episode_id", ["..", "0123", "XYZ", "0123456789abc", ""])
def test_invalid_episode_id_refused(storage, episode_id):
    with pytest.raises(PathContainmentError):
        storage.get_episode_path("a-pod", episode_id)


def test_original_path_validates_same_way(storage):
    with pytest.raises(PathContainmentError):
        storage.get_original_path("../etc", "0123456789ab")
    with pytest.raises(PathContainmentError):
        storage.get_original_path("a-pod", "not-hex")


def test_valid_slug_special_case_unchanged(storage):
    """Legitimate slugs that resemble reserved prefixes still resolve."""
    valid = storage.get_podcast_dir("regular-podcast-slug")
    assert valid.exists()

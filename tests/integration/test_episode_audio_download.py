"""Download links for an episode's original and cut audio."""
from unittest.mock import patch

import pytest

from api import get_database
from utils.constants import EpisodeStatus

SLUG = 'download-test-slug'
EPISODE_ID = 'abcdef012345'
URL = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}'


def _seed(tmp_path, status):
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Download Test')
    cut = tmp_path / 'cut.mp3'
    cut.write_bytes(b'\x00' * 2048)
    original = tmp_path / 'original.mp3'
    original.write_bytes(b'\x01' * 1024)
    db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                      original_url='https://example.com/ep.mp3',
                      title='Episode 42: Hello, World!', status=status,
                      original_file=str(original))
    # The insert path ignores processed_version; only an update sets it.
    db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, processed_version=2)
    return db, cut, original


@pytest.fixture
def seeded(app_client, tmp_path, request):
    status = getattr(request, 'param', EpisodeStatus.PROCESSED.value)
    db, cut, original = _seed(tmp_path, status)
    with patch('api.episodes.get_storage') as storage:
        storage.return_value.get_episode_path.return_value = cut
        storage.return_value.get_original_path.return_value = original
        yield cut, storage
    db.delete_podcast(SLUG)


def test_processed_audio_streams_current_version(app_client, seeded):
    _, storage = seeded
    r = app_client.get(f'{URL}/processed.mp3')
    assert r.status_code == 200
    assert r.mimetype == 'audio/mpeg'
    assert r.headers['Accept-Ranges'] == 'bytes'
    assert not r.headers['Content-Disposition'].startswith('attachment')
    assert storage.return_value.get_episode_path.call_args.kwargs['version'] == 2


@pytest.mark.parametrize('seeded', [EpisodeStatus.PENDING.value], indirect=True)
def test_last_cut_stays_downloadable_during_a_reprocess(app_client, seeded):
    assert app_client.get(f'{URL}/processed.mp3').status_code == 200


def test_processed_audio_404_for_unknown_episode(app_client, seeded):
    assert app_client.get(f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/processed.mp3').status_code == 404


def test_processed_audio_404_when_file_missing(app_client, seeded):
    cut, _ = seeded
    cut.unlink()
    assert app_client.get(f'{URL}/processed.mp3').status_code == 404


def test_download_flag_names_the_cut_file_after_the_title(app_client, seeded):
    r = app_client.get(f'{URL}/processed.mp3?download=1')
    assert r.status_code == 200
    assert r.headers['Content-Disposition'] == 'attachment; filename=Episode_42_Hello_World-cut.mp3'


def test_download_flag_names_the_original_file_after_the_title(app_client, seeded):
    r = app_client.get(f'{URL}/original.mp3?download=1')
    assert r.headers['Content-Disposition'] == 'attachment; filename=Episode_42_Hello_World-original.mp3'
    inline = app_client.get(f'{URL}/original.mp3')
    assert not inline.headers['Content-Disposition'].startswith('attachment')

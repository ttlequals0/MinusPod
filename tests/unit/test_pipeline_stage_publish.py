from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('pipeline_stage_publish_')

from main_app import processing
from main_app.pipeline import transcribe, common

SLUG = 'example-podcast'
EPISODE_ID = 'a1b2c3d4e5f6'
URL = 'https://example.com/a.mp3'


def _resolve(podcast, original_path):
    published = []
    with patch.object(common, '_publish_status', side_effect=lambda *a: published.append(a)), \
         patch.object(processing.storage, 'get_original_path', return_value=original_path), \
         patch.object(common, '_copy_retained_original_to_temp', return_value='/tmp/copy.mp3'), \
         patch.object(transcribe, '_download_episode_audio', return_value='/tmp/dl.mp3') as download:
        path = transcribe._resolve_audio_path(SLUG, EPISODE_ID, URL, podcast, {})
    return path, published, download


def test_retained_original_skips_downloading_stage(tmp_path):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'ID3')
    path, published, download = _resolve({}, str(original))
    assert path == '/tmp/copy.mp3'
    download.assert_not_called()
    assert all('downloading' not in call for call in published)


def test_download_publishes_downloading_stage():
    path, published, download = _resolve({}, None)
    assert path == '/tmp/dl.mp3'
    download.assert_called_once()
    assert ('update_job_stage', SLUG, EPISODE_ID, 'downloading', 5) in published


def test_local_feed_without_original_raises():
    with patch.object(transcribe, 'is_local_feed', return_value=True):
        with pytest.raises(Exception, match='original audio missing'):
            _resolve({}, None)

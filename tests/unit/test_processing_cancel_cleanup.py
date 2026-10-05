from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('processing_cancel_cleanup_')

from main_app import processing
from utils.episode_paths import episode_relative_path

SLUG = 'processing-cancel-cleanup'
EPISODE_ID = 'aa11bb22cc33'


@pytest.fixture
def db():
    handle = processing.db
    if handle.get_podcast_by_slug(SLUG):
        handle.delete_podcast(SLUG)
    handle.create_podcast(SLUG, 'https://example.com/feed.xml', 'Cancel cleanup')
    yield handle
    handle.delete_podcast(SLUG)


def _cancel_run(db, queue):
    with (
        patch.object(processing, 'ProcessingQueue', return_value=queue),
        patch.object(processing, 'process_episode',
                     side_effect=processing.ProcessingCancelled),
        patch.object(processing, 'pattern_catalog_scope', return_value=nullcontext()),
        patch.object(processing, '_start_run_log', return_value=None),
        patch.object(processing, '_end_run_log'),
        patch.object(processing.status_service, 'complete_job'),
    ):
        processing._process_episode_background(
            SLUG, EPISODE_ID, 'https://example.com/episode.mp3',
            'Episode', 'Cancel cleanup', None, None, run_id='cancel-run')


@pytest.mark.parametrize('version', [0, 1])
def test_cancelled_rerun_deletes_only_replacement_and_preserves_published_files(
        db, version):
    reprocess_at = '2026-10-05T00:00:00Z'
    published = processing.storage.get_episode_path(
        SLUG, EPISODE_ID, version=version)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b'published audio')
    original = processing.storage.get_original_path(SLUG, EPISODE_ID)
    original.write_bytes(b'retained original')
    prior_other = processing.storage.get_episode_path(
        SLUG, EPISODE_ID, version=0)
    if version:
        prior_other.write_bytes(b'older audio')

    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    db.upsert_episode(
        SLUG, EPISODE_ID,
        processed_file=episode_relative_path(EPISODE_ID, version),
        processed_version=version, reprocess_requested_at=reprocess_at)
    replacement_version = processing._next_processed_version(
        db.get_episode(SLUG, EPISODE_ID))
    replacement = processing.storage.get_episode_path(
        SLUG, EPISODE_ID, version=replacement_version)
    replacement.write_bytes(b'partial replacement')
    queue = MagicMock()
    queue.owns.return_value = True

    _cancel_run(db, queue)

    assert published.read_bytes() == b'published audio'
    assert not replacement.exists()
    assert original.read_bytes() == b'retained original'
    if version:
        assert prior_other.read_bytes() == b'older audio'
    episode = db.get_episode(SLUG, EPISODE_ID)
    assert episode['processed_file'] == episode_relative_path(EPISODE_ID, version)
    assert episode['status'] == 'pending'


@pytest.mark.parametrize('feed_type,keep_original', [
    ('rss', False), ('local', True),
])
def test_first_run_cancel_keeps_existing_cleanup_behavior(db, feed_type,
                                                          keep_original):
    db.delete_podcast(SLUG)
    db.create_podcast(
        SLUG, 'https://example.com/feed.xml', 'Cancel cleanup',
        feed_type=feed_type)
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    assert processing._next_processed_version(db.get_episode(SLUG, EPISODE_ID)) == 0
    unversioned = processing.storage.get_episode_path(SLUG, EPISODE_ID)
    unversioned.parent.mkdir(parents=True, exist_ok=True)
    unversioned.write_bytes(b'partial output')
    original = processing.storage.get_original_path(SLUG, EPISODE_ID)
    original.write_bytes(b'original input')
    queue = MagicMock()
    queue.owns.return_value = True

    _cancel_run(db, queue)

    assert not unversioned.exists()
    assert original.exists() is keep_original


def test_cancel_after_publication_preserves_newly_published_output(db):
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    published = processing.storage.get_episode_path(SLUG, EPISODE_ID, version=0)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b'published output')
    queue = MagicMock()
    queue.owns.return_value = True

    def publish_then_cancel(*args, **kwargs):
        db.upsert_episode(
            SLUG, EPISODE_ID, status='processed',
            processed_file=episode_relative_path(EPISODE_ID, 0),
            processed_version=0)
        raise processing.ProcessingCancelled()

    with (
        patch.object(processing, 'ProcessingQueue', return_value=queue),
        patch.object(processing, 'process_episode', side_effect=publish_then_cancel),
        patch.object(processing, 'pattern_catalog_scope', return_value=nullcontext()),
        patch.object(processing, '_start_run_log', return_value=None),
        patch.object(processing, '_end_run_log'),
        patch.object(processing.status_service, 'complete_job'),
    ):
        processing._process_episode_background(
            SLUG, EPISODE_ID, 'https://example.com/episode.mp3',
            'Episode', 'Cancel cleanup', None, None, run_id='cancel-run')

    assert published.read_bytes() == b'published output'
    assert db.get_episode(SLUG, EPISODE_ID)['status'] == 'processed'


def test_lost_owner_cannot_delete_cancelled_replacement(db):
    version = 1
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    db.upsert_episode(
        SLUG, EPISODE_ID,
        processed_file=episode_relative_path(EPISODE_ID, version),
        processed_version=version, reprocess_requested_at='2026-10-05T00:00:00Z')
    published = processing.storage.get_episode_path(
        SLUG, EPISODE_ID, version=version)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b'published audio')
    replacement = processing.storage.get_episode_path(
        SLUG, EPISODE_ID, version=version + 1)
    replacement.write_bytes(b'partial replacement')
    queue = MagicMock()
    queue.owns.side_effect = [True, True, False]

    _cancel_run(db, queue)

    assert published.read_bytes() == b'published audio'
    assert replacement.read_bytes() == b'partial replacement'
    assert db.get_episode(SLUG, EPISODE_ID)['status'] == 'processing'


def test_lost_owner_before_first_run_cleanup_preserves_files(db):
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    partial = processing.storage.get_episode_path(SLUG, EPISODE_ID)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b'partial output')
    original = processing.storage.get_original_path(SLUG, EPISODE_ID)
    original.write_bytes(b'original input')
    queue = MagicMock()
    queue.owns.side_effect = [True, True, False, False]

    _cancel_run(db, queue)

    assert partial.read_bytes() == b'partial output'
    assert original.read_bytes() == b'original input'
    assert db.get_episode(SLUG, EPISODE_ID)['status'] == 'processing'


@pytest.mark.parametrize('metadata', [
    {'processed_at': '2026-10-04T00:00:00Z'},
    {'processed_version': 1},
    {'status': 'processed'},
])
def test_ambiguous_unpublished_metadata_preserves_existing_audio(db, metadata):
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    db.upsert_episode(SLUG, EPISODE_ID, **metadata)
    partial = processing.storage.get_episode_path(SLUG, EPISODE_ID)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b'partial output')
    queue = MagicMock()
    queue.owns.return_value = True

    _cancel_run(db, queue)

    assert partial.read_bytes() == b'partial output'


def test_missing_publication_pointer_during_rerun_preserves_all_audio(db):
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    db.upsert_episode(
        SLUG, EPISODE_ID,
        processed_file=episode_relative_path(EPISODE_ID, 0),
        processed_version=0)
    published = processing.storage.get_episode_path(SLUG, EPISODE_ID, version=0)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b'published audio')
    replacement = processing.storage.get_episode_path(SLUG, EPISODE_ID, version=1)
    replacement.write_bytes(b'partial replacement')
    queue = MagicMock()
    queue.owns.return_value = True

    def remove_pointer_then_cancel(*args, **kwargs):
        db.upsert_episode(SLUG, EPISODE_ID, processed_file=None)
        raise processing.ProcessingCancelled()

    with (
        patch.object(processing, 'ProcessingQueue', return_value=queue),
        patch.object(processing, 'process_episode', side_effect=remove_pointer_then_cancel),
        patch.object(processing, 'pattern_catalog_scope', return_value=nullcontext()),
        patch.object(processing, '_start_run_log', return_value=None),
        patch.object(processing, '_end_run_log'),
        patch.object(processing.status_service, 'complete_job'),
    ):
        processing._process_episode_background(
            SLUG, EPISODE_ID, 'https://example.com/episode.mp3',
            'Episode', 'Cancel cleanup', None, None, run_id='cancel-run')

    assert published.read_bytes() == b'published audio'
    assert replacement.read_bytes() == b'partial replacement'


def test_unstamped_version_zero_publication_uses_new_version_on_cancel(db):
    db.upsert_episode(SLUG, EPISODE_ID, status='processing')
    db.upsert_episode(
        SLUG, EPISODE_ID,
        processed_file=episode_relative_path(EPISODE_ID, 0),
        processed_version=0)
    published = processing.storage.get_episode_path(SLUG, EPISODE_ID, version=0)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b'published audio')
    assert processing._next_processed_version(db.get_episode(SLUG, EPISODE_ID)) == 1
    replacement = processing.storage.get_episode_path(SLUG, EPISODE_ID, version=1)
    replacement.write_bytes(b'partial replacement')
    queue = MagicMock()
    queue.owns.return_value = True

    _cancel_run(db, queue)

    assert published.read_bytes() == b'published audio'
    assert not replacement.exists()

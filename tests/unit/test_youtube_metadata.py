import json
import subprocess
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('youtube_metadata_test_')
from utils.time import utc_now  # noqa: E402
from youtube import download, metadata, ytdlp  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_throttle():
    metadata._next_run = None
    yield
    metadata._next_run = None


def _run_result(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_fill_fields_replaces_placeholder_title_only():
    assert metadata.fill_fields({'title': 'Private episode'}, {'title': 'Real'}) == {'title': 'Real'}
    assert metadata.fill_fields({'title': 'Mine'}, {'title': 'Real'}) == {}


def test_fill_fields_description_only_when_empty():
    assert metadata.fill_fields({'description': ''}, {'description': 'Hi'}) == {'description': 'Hi'}
    assert metadata.fill_fields({'description': 'Old'}, {'description': 'New'}) == {}


def test_fill_fields_publish_time_upgrade():
    info = {'timestamp': 1790000000}
    out = metadata.fill_fields({'published_at': '2026-10-29'}, info)
    assert out['published_at'].startswith('2026-09-21T')
    assert metadata.fill_fields({'published_at': '2026-10-29T08:00:00+00:00'}, info) == {}
    out = metadata.fill_fields({'published_at': '2026-10-27T00:00:00Z'}, {'timestamp': 1790000000})
    assert out['published_at'].startswith('2026-09-21T')
    assert metadata.fill_fields({'published_at': '2026-10-27T00:00:01Z'}, info) == {}
    assert metadata.fill_fields({'published_at': '2026-10-27T00:00:00-05:00'}, info) == {}
    assert 'published_at' in metadata.fill_fields({'published_at': None}, info)
    assert metadata.fill_fields({}, {}) == {}


def test_fill_fields_falls_back_to_release_timestamp():
    out = metadata.fill_fields({'published_at': '2026-10-29'}, {'release_timestamp': 1790000000})
    assert out['published_at'].startswith('2026-09-21T')


def _patched_fetch(run):
    return patch.multiple(download, cookies_path=MagicMock(return_value=None),
                          _impersonate_target=MagicMock(return_value=None)), \
        patch.object(ytdlp, 'run', run)


def test_fetch_info_parses_lines_in_one_call():
    out = '\n'.join([json.dumps({'id': 'vid1', 'description': 'a'}), 'junk',
                     json.dumps({'id': 'vid2', 'description': 'b'})])
    run = MagicMock(return_value=_run_result(out))
    p1, p2 = _patched_fetch(run)
    with p1, p2:
        infos = metadata.fetch_info(['vid1', 'vid2'])
    assert set(infos) == {'vid1', 'vid2'}
    run.assert_called_once()
    args = run.call_args[0][0]
    assert 'https://www.youtube.com/watch?v=vid1' in args and 'https://www.youtube.com/watch?v=vid2' in args
    assert run.call_args[1]['timeout'] == metadata.METADATA_TIMEOUT_S


def test_fetch_info_partial_success_returns_parsed():
    run = MagicMock(return_value=_run_result(json.dumps({'id': 'vid1'}), returncode=1, stderr='ERROR: x'))
    p1, p2 = _patched_fetch(run)
    with p1, p2:
        assert set(metadata.fetch_info(['vid1', 'vid2'])) == {'vid1'}


def test_fetch_info_error_when_nothing_parsed():
    stderr = 'ERROR: Unable to extract player version; please report this issue'
    run = MagicMock(return_value=_run_result('', returncode=1, stderr=stderr))
    p1, p2 = _patched_fetch(run)
    with p1, p2, pytest.raises(download.DownloadError) as exc:
        metadata.fetch_info(['vid1'])
    assert exc.value.needs_update is True
    assert str(exc.value).startswith('ERROR: Unable')


def _db(rows):
    db = MagicMock()
    db.get_youtube_episodes_missing_metadata.return_value = rows
    return db


def _enabled(value=True):
    return patch.object(metadata.yt_settings, 'enabled', return_value=value)


def test_tick_disabled_makes_no_db_call():
    db = _db([])
    with _enabled(False):
        metadata.backfill_tick(db)
    db.get_youtube_episodes_missing_metadata.assert_not_called()


def test_batch_empty_selection_sets_throttle_without_ytdlp():
    db = _db([])
    with _enabled(), patch.object(metadata, 'fetch_info') as fetch, patch.object(metadata, '_executor') as executor:
        metadata._run_batch(db)
        assert metadata._next_run > utc_now() + timedelta(minutes=9)
        db.get_youtube_episodes_missing_metadata.reset_mock()
        metadata.backfill_tick(db)
    fetch.assert_not_called()
    executor.submit.assert_not_called()
    db.get_youtube_episodes_missing_metadata.assert_not_called()


def test_tick_upserts_fields_and_marker():
    rows = [{'slug': 's', 'episode_id': 'e1', 'youtube_video_id': 'vid1', 'original_url': 'u',
             'published_at': '2026-10-29', 'description': ''},
            {'slug': 's', 'episode_id': 'e2', 'youtube_video_id': None,
             'original_url': 'https://www.youtube.com/watch?v=vid2', 'published_at': None, 'description': None}]
    db = _db(rows)
    with _enabled(), patch.object(metadata, 'fetch_info', return_value={
            'vid1': {'id': 'vid1', 'description': 'Hello'}, 'vid2': {'id': 'vid2'}}) as fetch:
        metadata._run_batch(db)
    fetch.assert_called_once_with(['vid1', 'vid2'])
    first, second = db.upsert_episode.call_args_list
    assert first[0] == ('s', 'e1') and first[1]['description'] == 'Hello' and first[1]['yt_metadata_at']
    assert second[0] == ('s', 'e2') and set(second[1]) == {'yt_metadata_at'}


def test_tick_error_does_not_mark_rows_and_throttles():
    db = _db([{'slug': 's', 'episode_id': 'e1', 'youtube_video_id': 'vid1', 'original_url': 'u',
               'published_at': None, 'description': ''}])
    with _enabled(), patch.object(metadata, 'fetch_info', side_effect=download.DownloadError('boom')):
        metadata._run_batch(db)
    db.upsert_episode.assert_not_called()
    assert metadata._next_run > utc_now()


def test_tick_in_flight_batch_makes_no_db_call():
    db = _db([])
    running = MagicMock()
    running.done.return_value = False
    with _enabled(), patch.object(metadata, '_future', running), patch.object(metadata, '_executor') as executor:
        metadata.backfill_tick(db)
    executor.submit.assert_not_called()
    db.get_youtube_episodes_missing_metadata.assert_not_called()


def test_tick_submits_one_batch():
    db = _db([])
    with _enabled(), patch.object(metadata, '_future', None), patch.object(metadata, '_executor') as executor:
        metadata.backfill_tick(db)
    executor.submit.assert_called_once_with(metadata._run_batch, db)


def _two_rows():
    return [{'slug': 's', 'episode_id': 'e1', 'youtube_video_id': 'vid1', 'original_url': 'u',
             'published_at': None, 'description': ''},
            {'slug': 's', 'episode_id': 'e2', 'youtube_video_id': 'vid2', 'original_url': 'u',
             'published_at': None, 'description': ''}]


def test_partial_batch_stamps_only_present_then_gives_up_after_max_misses():
    metadata._misses.clear()
    present = {'vid1': {'id': 'vid1', 'description': 'Hi'}}
    with _enabled(), patch.object(metadata, 'fetch_info', return_value=present):
        db = _db(_two_rows())
        metadata._run_batch(db)
        assert [c[0][1] for c in db.upsert_episode.call_args_list] == ['e1']
        for _ in range(metadata.MAX_MISSES - 1):
            db = _db(_two_rows())
            metadata._run_batch(db)
        assert [c[0][1] for c in db.upsert_episode.call_args_list] == ['e1', 'e2']
    assert 'vid2' not in metadata._misses


def test_failing_batch_stamps_rows_after_max_misses():
    metadata._misses.clear()
    with _enabled(), patch.object(metadata, 'fetch_info', side_effect=download.DownloadError('private')):
        for _ in range(metadata.MAX_MISSES - 1):
            db = _db(_two_rows())
            metadata._run_batch(db)
            db.upsert_episode.assert_not_called()
        db = _db(_two_rows())
        metadata._run_batch(db)
    assert [c[0][1] for c in db.upsert_episode.call_args_list] == ['e1', 'e2']
    assert all(c[1]['yt_metadata_at'] for c in db.upsert_episode.call_args_list)
    assert not metadata._misses


def test_batch_exception_throttles_and_clears_transaction():
    db = _db(_two_rows())
    with _enabled(), patch.object(metadata, 'fetch_info', side_effect=RuntimeError('x')):
        metadata._run_batch(db)
    assert metadata._next_run > utc_now()
    db.clear_leaked_transaction.assert_called_once()

import json
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('recut_notranscript_')

from api.episodes import runs  # noqa: E402
from main_app import processing  # noqa: E402
from main_app.pipeline import finalize, publish, recut  # noqa: E402
from tests.unit.recut_test_utils import stub_recut  # noqa: E402


def _check(has_segments, **episode):
    db = MagicMock()
    db.get_original_segments.return_value = [{'start': 0, 'end': 1}] if has_segments else []
    row = {'ad_markers_json': json.dumps([{'start': 10, 'end': 20}]), **episode}
    with patch.object(runs, 'get_storage') as storage, \
            patch.object(runs, 'error_response', lambda msg, code: (msg, code)):
        storage.return_value.get_original_path.return_value.exists.return_value = True
        return runs._check_recut_preconditions(db, 'example-podcast', 'a1b2c3d4e5f6', row)


def test_no_transcript_and_no_segments_passes():
    assert _check(False) is None


def test_transcript_without_segments_still_409():
    message, status = _check(False, has_transcript=1)
    assert status == 409
    assert 'No saved transcript segments' in message


def test_transcript_with_segments_passes():
    assert _check(True, has_transcript=1) is None


def test_recut_with_empty_segments_renders_and_finalizes():
    ad = {'start': 100.0, 'end': 160.0, 'confidence': 0.99, 'detection_stage': 'audio_cue'}
    with ExitStack() as stack:
        db, storage, local_ap = stub_recut(
            stack, [ad], [ad], episode_row={'podcast_id': 1, 'processed_version': 0}, duration=600.0)
        db.get_original_segments.return_value = []
        assets = stack.enter_context(patch.object(publish, '_generate_assets'))
        fin = stack.enter_context(patch.object(finalize, '_finalize_episode'))
        stack.enter_context(patch.object(processing.shutil, 'move'))
        local_ap.get_audio_duration.return_value = 600.0
        local_ap.process_episode.side_effect = (
            lambda work_path, segs, cut_barriers=None, hard_barriers=None: (
                '/tmp/recut-cut.mp3', [{'start': s['start'], 'end': s['end']} for s in segs]))
        assert recut._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time()) is True
    assert local_ap.process_episode.call_args.args[1][0]['start'] == 100.0
    assert assets.call_args.args[2] == []
    assert assets.call_args.kwargs['regenerate_chapters'] is False
    fin.assert_called_once()
    storage.save_combined_ads.assert_called_once()


def test_vtt_only_row_counts_as_transcript_in_both_queries(temp_db):
    temp_db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode('example-podcast', 'a1b2c3d4e5f6',
                           original_url='https://example.com/ep.mp3')
    assert temp_db.get_episode('example-podcast', 'a1b2c3d4e5f6')['has_transcript'] == 0
    temp_db.save_episode_details('example-podcast', 'a1b2c3d4e5f6', transcript_vtt='WEBVTT')
    assert temp_db.get_episode('example-podcast', 'a1b2c3d4e5f6')['has_transcript'] == 1
    temp_db.get_connection().execute(
        "UPDATE episodes SET pending_recut_at = '2026-01-01T00:00:00Z'")
    temp_db.get_connection().commit()
    rows = temp_db.get_episodes_pending_recut()
    assert [r['has_transcript'] for r in rows] == [1]


def _recut_with_no_segments(episode_row):
    with ExitStack() as stack:
        db, _storage, local_ap = stub_recut(stack, [], [], episode_row=episode_row, duration=600.0)
        db.get_original_segments.return_value = []
        stack.enter_context(patch.object(processing.shutil, 'move'))
        stack.enter_context(patch.object(publish, '_generate_assets'))
        stack.enter_context(patch.object(finalize, '_finalize_episode'))
        local_ap.get_audio_duration.return_value = 600.0
        local_ap.process_episode.return_value = ('/tmp/recut-cut.mp3', [])
        recut._recut_episode('example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time())
        return local_ap


def test_recut_with_transcript_but_no_segments_raises(caplog):
    local_ap = _recut_with_no_segments({'podcast_id': 1, 'processed_version': 0, 'has_transcript': 1})
    assert 'No saved transcript segments; cannot recut' in caplog.text
    local_ap.process_episode.assert_not_called()


def test_recut_without_transcript_flag_proceeds_past_the_segment_guard(caplog):
    local_ap = _recut_with_no_segments({'podcast_id': 1, 'processed_version': 0, 'has_transcript': 0})
    assert 'No saved transcript segments' not in caplog.text
    local_ap.process_episode.assert_called_once()

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('youtube_watch_test_')
from youtube import watch  # noqa: E402
from youtube.settings import YouTubeSettings  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
SETTINGS = YouTubeSettings(enabled=True, process_delay_minutes=60, probe_attempts=3, probe_interval_minutes=30,
                           watch_interval_hours=6, watch_max_days=7, llm_fallback=True,
                           captions_as_transcript=True, ytdlp_auto_update=True)
MARK = [{'UUID': 'u1', 'segment': [1, 2], 'category': 'sponsor', 'actionType': 'skip', 'votes': 1, 'locked': 0}]


def _row(state, attempts=0, published=NOW - timedelta(days=1), uuids='[]', last_gap_minutes=30):
    return {'sb_state': state, 'sb_attempts': attempts, 'published_at': published.isoformat(),
            'created_at': published.isoformat(), 'sb_segment_uuids': uuids,
            'sb_next_check_at': (NOW - timedelta(minutes=last_gap_minutes)).isoformat()}


def test_marks_found_queues():
    out = watch.transition(_row('waiting'), SETTINGS, MARK, NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_state'] == 'probing' and out['sb_next_check_at'] is None


def test_no_marks_probes_then_falls_back_to_llm():
    out = watch.transition(_row('waiting', attempts=0), SETTINGS, [], NOW, has_retained_original=False)
    assert out['sb_state'] == 'probing' and out['sb_attempts'] == 1
    assert out['sb_next_check_at'] == (NOW + timedelta(minutes=30)).isoformat()
    out = watch.transition(_row('probing', attempts=2), SETTINGS, [], NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_state'] == 'llm'


def test_no_marks_without_fallback_watches_until_ceiling():
    s = watch.replace_settings(SETTINGS, llm_fallback=False)
    out = watch.transition(_row('probing', attempts=2), s, [], NOW, has_retained_original=False)
    assert out['queue'] is False and out['sb_state'] == 'watching'
    assert out['sb_next_check_at'] == (NOW + timedelta(hours=6)).isoformat()
    old = _row('watching', published=NOW - timedelta(days=8))
    out = watch.transition(old, s, [], NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_state'] == 'exhausted'


def test_watching_young_episode_keeps_watching_and_marks_queue():
    out = watch.transition(_row('watching'), SETTINGS, [], NOW, has_retained_original=False)
    assert out['sb_state'] == 'watching' and out['queue'] is False
    assert out['sb_next_check_at'] == (NOW + timedelta(hours=6)).isoformat()
    out = watch.transition(_row('watching'), SETTINGS, MARK, NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_next_check_at'] is None


def test_zero_probe_attempts_skips_probing():
    s = watch.replace_settings(SETTINGS, probe_attempts=0)
    out = watch.transition(_row('waiting'), s, [], NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_state'] == 'llm'


def test_done_recuts_when_uuid_set_changes():
    out = watch.transition(_row('done', uuids='["u0"]'), SETTINGS, MARK, NOW, has_retained_original=True)
    assert out['recut'] is True and out['queue'] is True
    out = watch.transition(_row('done', uuids='["u1"]'), SETTINGS, MARK, NOW, has_retained_original=True)
    assert out['recut'] is False and out['sb_next_check_at'] == (NOW + timedelta(hours=6)).isoformat()
    out = watch.transition(_row('done', uuids='["u0"]'), SETTINGS, MARK, NOW, has_retained_original=False)
    assert out['recut'] is False


def test_done_stops_watching_past_ceiling():
    out = watch.transition(_row('done', published=NOW - timedelta(days=8), uuids='["u1"]'), SETTINGS, MARK, NOW, True)
    assert out['sb_next_check_at'] is None and out['queue'] is False


def test_ceiling_falls_back_to_created_at():
    row = _row('watching')
    row['published_at'] = None
    row['created_at'] = (NOW - timedelta(days=8)).isoformat()
    out = watch.transition(row, SETTINGS, [], NOW, has_retained_original=False)
    assert out['sb_state'] == 'exhausted'


def test_terminal_states_never_scheduled():
    for state in ('llm', 'exhausted'):
        out = watch.transition(_row(state), SETTINGS, MARK, NOW, True)
        assert out['sb_next_check_at'] is None and out['queue'] is False and out['sb_state'] == state


def test_error_backs_off_and_records():
    out = watch.transition(_row('probing', attempts=1, last_gap_minutes=30), SETTINGS, None, NOW, False)
    assert out['sb_state'] == 'probing' and out['sb_attempts'] == 1
    assert out['sb_next_check_at'] == (NOW + timedelta(minutes=60)).isoformat()
    assert out['sb_last_error']
    out = watch.transition(_row('probing', last_gap_minutes=20 * 60), SETTINGS, None, NOW, False)
    assert out['sb_next_check_at'] == (NOW + timedelta(hours=24)).isoformat()


def _tick_db(rows):
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_due_sponsorblock_episodes.return_value = rows
    db.get_podcast_by_slug.return_value = {'slug': 'example-podcast', 'title': 'Example', 'queue_priority': None}
    return db


def _tick_row(episode_id, state='done', uuids='["u0"]'):
    row = _row(state, uuids=uuids)
    row.update(slug='example-podcast', episode_id=episode_id, youtube_video_id='vid' + episode_id,
               original_url='https://example.com/v', title='T', description='', original_duration=100.0)
    return row


def _patch_tick(monkeypatch, retained=True):
    original = MagicMock()
    original.exists.return_value = retained
    monkeypatch.setattr('main_app.storage.get_original_path', lambda slug, eid: original)
    monkeypatch.setattr(watch.yt_settings, 'resolve_feed', lambda db, podcast: SETTINGS)
    queued = []
    monkeypatch.setattr(watch, 'queue_for_processing',
                        lambda db, row, podcast, *, recut: queued.append((row['episode_id'], recut)))
    return queued


def test_tick_recut_writes_markers_and_stamps_uuids(monkeypatch):
    queued = _patch_tick(monkeypatch)
    markers = [{'start': 1.0, 'end': 2.0}]
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    monkeypatch.setattr(watch.sponsorblock, 'to_markers', lambda marks, dur: (markers, ''))
    monkeypatch.setattr(watch.sponsorblock, 'uuid_set', lambda m: ['u1'])
    db = _tick_db([_tick_row('a1')])
    watch.sponsorblock_tick(db)
    db.save_episode_details.assert_called_once_with('example-podcast', 'a1', ad_markers=markers)
    fields = db.update_episode_sponsorblock.call_args.kwargs
    assert fields['sb_segment_uuids'] == json.dumps(['u1'])
    assert queued == [('a1', True)]


def test_tick_recut_with_no_convertible_markers_only_reschedules(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    monkeypatch.setattr(watch.sponsorblock, 'to_markers', lambda marks, dur: ([], 'duration mismatch'))
    db = _tick_db([_tick_row('a1')])
    watch.sponsorblock_tick(db)
    db.save_episode_details.assert_not_called()
    fields = db.update_episode_sponsorblock.call_args.kwargs
    assert 'sb_segment_uuids' not in fields and fields['sb_next_check_at']
    assert queued == []


def test_tick_row_failure_does_not_abort_batch(monkeypatch):
    queued = _patch_tick(monkeypatch)

    def fetch(vid):
        if vid == 'vida1':
            raise RuntimeError('boom')
        return MARK

    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', fetch)
    db = _tick_db([_tick_row('a1', state='waiting', uuids='[]'), _tick_row('a2', state='waiting', uuids='[]')])
    watch.sponsorblock_tick(db)
    assert queued == [('a2', False)]


def test_tick_disabled_does_nothing():
    db = _tick_db([])
    db.get_setting_bool.return_value = False
    watch.sponsorblock_tick(db)
    db.get_due_sponsorblock_episodes.assert_not_called()

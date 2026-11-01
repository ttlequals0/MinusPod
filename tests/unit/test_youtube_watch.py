import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('youtube_watch_test_')
from youtube import watch  # noqa: E402
from youtube.settings import YouTubeSettings  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
SETTINGS = YouTubeSettings(enabled=True, process_delay_minutes=60, probe_attempts=3, probe_interval_minutes=30,
                           watch_interval_hours=6, watch_max_days=7, llm_fallback=True,
                           captions_as_transcript=True, hide_private=True, transcribe_fallback=False, ytdlp_auto_update=True, ytdlp_extra_args='')
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
    s = replace(SETTINGS, llm_fallback=False)
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
    s = replace(SETTINGS, probe_attempts=0)
    out = watch.transition(_row('waiting'), s, [], NOW, has_retained_original=False)
    assert out['queue'] is True and out['sb_state'] == 'llm'


def test_done_recuts_when_uuid_set_changes():
    out = watch.transition(_row('done', uuids='["u0"]'), SETTINGS, MARK, NOW, True, {'u1'})
    assert out['recut'] is True and out['queue'] is True
    out = watch.transition(_row('done', uuids='["u1"]'), SETTINGS, MARK, NOW, True, {'u1'})
    assert out['recut'] is False and out['sb_next_check_at'] == (NOW + timedelta(hours=6)).isoformat()
    out = watch.transition(_row('done', uuids='["u0"]'), SETTINGS, MARK, NOW, False, {'u1'})
    assert out['recut'] is False


def test_done_stops_watching_past_ceiling():
    out = watch.transition(_row('done', published=NOW - timedelta(days=8), uuids='["u1"]'), SETTINGS, MARK, NOW, True, {'u1'})
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


def test_error_retries_at_normal_interval_and_records():
    out = watch.transition(_row('probing', attempts=1), SETTINGS, None, NOW, False)
    assert out['sb_state'] == 'probing' and out['sb_attempts'] == 1
    assert out['sb_next_check_at'] == (NOW + timedelta(minutes=30)).isoformat()
    assert out['sb_last_error'] == 'SponsorBlock request failed'
    for state in ('watching', 'done'):
        out = watch.transition(_row(state), SETTINGS, None, NOW, False)
        assert out['sb_next_check_at'] == (NOW + timedelta(hours=6)).isoformat()
    s = replace(SETTINGS, probe_interval_minutes=2000)
    out = watch.transition(_row('probing'), s, None, NOW, False)
    assert out['sb_next_check_at'] == (NOW + timedelta(hours=24)).isoformat()


def _tick_db(rows):
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_due_sponsorblock_episodes.return_value = rows
    db.get_podcast_by_slug.return_value = {'slug': 'example-podcast', 'title': 'Example', 'queue_priority': None}
    return db


def _tick_row(episode_id, state='done', uuids='["u0"]'):
    row = _row(state, uuids=uuids)
    row.update(id=7, slug='example-podcast', episode_id=episode_id, youtube_video_id='vid' + episode_id,
               original_url='https://example.com/v', title='T', description='', original_duration=100.0)
    return row


def _patch_tick(monkeypatch, retained=True):
    original = MagicMock()
    original.exists.return_value = retained
    monkeypatch.setattr('main_app.storage.get_original_path', lambda slug, eid: original)
    monkeypatch.setattr(watch.yt_settings, 'resolve_feed', lambda db, podcast, base=None: SETTINGS)
    queued = []
    monkeypatch.setattr(watch.sponsorblock, 'record_availability', lambda db: True)
    monkeypatch.setattr(watch.sponsorblock, 'availability', lambda db: (True, None))
    monkeypatch.setattr(watch, 'queue_for_processing',
                        lambda db, row, podcast, *, recut: queued.append((row['episode_id'], recut)))
    return queued


def test_tick_recut_writes_merged_markers_and_leaves_uuids_to_the_recut(monkeypatch):
    queued = _patch_tick(monkeypatch)
    llm_marker = {'start': 50.0, 'end': 60.0, 'source': 'llm'}
    old_sb = {'start': 5.0, 'end': 6.0, 'source': 'sponsorblock', 'sb_uuid': 'u0'}
    new_sb = {'start': 1.0, 'end': 2.0, 'source': 'sponsorblock', 'sb_uuid': 'u1'}
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    monkeypatch.setattr(watch.sponsorblock, 'to_markers', lambda marks, dur: ([new_sb], None))
    db = _tick_db([_tick_row('a1')])
    db.get_episode.return_value = {'ad_markers_json': json.dumps([llm_marker, old_sb])}
    watch.sponsorblock_tick(db)
    saved = db.save_episode_details.call_args.kwargs['ad_markers']
    assert [m['start'] for m in saved] == [1.0, 50.0] and llm_marker in saved and old_sb not in saved
    for call in db.update_episode_sponsorblock_by_pk.call_args_list:
        assert 'sb_segment_uuids' not in call.kwargs
    assert queued == [('a1', True)]


def test_tick_removal_of_the_only_mark_triggers_recut(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: [])
    db = _tick_db([_tick_row('a1', uuids='["u1"]')])
    db.get_episode.return_value = {'ad_markers_json': json.dumps(
        [{'start': 1.0, 'end': 2.0, 'source': 'sponsorblock', 'sb_uuid': 'u1'}])}
    watch.sponsorblock_tick(db)
    assert queued == [('a1', True)]
    assert db.save_episode_details.call_args.kwargs['ad_markers'] == []


def test_tick_duration_mismatch_only_reschedules(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    monkeypatch.setattr(watch.sponsorblock, 'to_markers', lambda marks, dur: ([], 'duration_mismatch'))
    db = _tick_db([_tick_row('a1', uuids='["u1"]')])
    watch.sponsorblock_tick(db)
    db.save_episode_details.assert_not_called()
    assert queued == []
    assert db.update_episode_sponsorblock_by_pk.call_args.kwargs['sb_next_check_at']


def test_tick_filtered_marks_count_as_no_marks_while_waiting(monkeypatch):
    queued = _patch_tick(monkeypatch)
    raw = [{'UUID': 'u2', 'segment': [5, 6], 'category': 'sponsor', 'actionType': 'skip', 'votes': -3, 'locked': 0}]
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: raw)
    db = _tick_db([_tick_row('a1', state='waiting', uuids='[]')])
    watch.sponsorblock_tick(db)
    assert queued == []
    assert db.update_episode_sponsorblock_by_pk.call_args.kwargs['sb_state'] == 'probing'


def test_tick_skips_in_flight_episodes(monkeypatch):
    queued = _patch_tick(monkeypatch)
    fetched = []
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: fetched.append(vid) or MARK)
    rows = [dict(_tick_row('a1', state='waiting', uuids='[]'), status=status) for status in ('pending', 'processing')]
    db = _tick_db(rows)
    before = datetime.now(timezone.utc)
    watch.sponsorblock_tick(db)
    assert queued == [] and fetched == []
    for call in db.update_episode_sponsorblock_by_pk.call_args_list:
        assert set(call.kwargs) == {'sb_next_check_at'}
        assert timedelta(minutes=4) < datetime.fromisoformat(call.kwargs['sb_next_check_at']) - before < timedelta(minutes=6)


def test_tick_request_failure_stops_the_batch(monkeypatch):
    queued = _patch_tick(monkeypatch)
    fetched = []
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: fetched.append(vid))
    db = _tick_db([_tick_row(f'a{i}', state='waiting', uuids='[]') for i in range(3)])
    watch.sponsorblock_tick(db)
    assert fetched == ['vida0'] and queued == []
    calls = db.update_episode_sponsorblock_by_pk.call_args_list
    assert len(calls) == 3
    for call in calls[1:]:
        assert call.kwargs['sb_last_error'] == 'SponsorBlock request failed' and call.kwargs['sb_next_check_at']


def test_tick_persists_state_with_a_check_time_before_queueing(monkeypatch):
    _patch_tick(monkeypatch)
    order = []
    db = _tick_db([_tick_row('a1', state='waiting', uuids='[]')])
    db.update_episode_sponsorblock_by_pk.side_effect = lambda pk, **f: order.append(('write', f))
    monkeypatch.setattr(watch, 'queue_for_processing', lambda *a, **k: order.append(('queue', None)))
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    watch.sponsorblock_tick(db)
    assert [kind for kind, _ in order] == ['write', 'queue', 'write']
    assert order[0][1]['sb_state'] == 'probing' and order[0][1]['sb_next_check_at']
    assert order[2][1] == {'sb_next_check_at': None}


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


def test_tick_filtered_segment_does_not_recut(monkeypatch):
    queued = _patch_tick(monkeypatch)
    raw = MARK + [{'UUID': 'u2', 'segment': [5, 6], 'category': 'sponsor', 'actionType': 'skip',
                   'votes': -3, 'locked': 0}]
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: raw)
    db = _tick_db([_tick_row('a1', uuids='["u1"]')])
    watch.sponsorblock_tick(db)
    assert queued == []
    db.save_episode_details.assert_not_called()
    assert db.update_episode_sponsorblock_by_pk.call_args.kwargs['sb_next_check_at']


def test_tick_queue_failure_keeps_state_and_retries_soon(monkeypatch):
    _patch_tick(monkeypatch)

    def boom(db, row, podcast, *, recut):
        raise RuntimeError('queue down')

    monkeypatch.setattr(watch, 'queue_for_processing', boom)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    db = _tick_db([_tick_row('a1', state='waiting', uuids='[]')])
    before = datetime.now(timezone.utc)
    watch.sponsorblock_tick(db)
    calls = db.update_episode_sponsorblock_by_pk.call_args_list
    assert calls[1].kwargs['sb_state'] == 'waiting'
    fields = calls[-1].kwargs
    assert 'sb_state' not in fields and fields['sb_last_error'] == 'RuntimeError'
    due = datetime.fromisoformat(fields['sb_next_check_at'])
    assert timedelta(minutes=4) < due - before < timedelta(minutes=6)


def test_tick_corrupt_segment_uuids_do_not_raise(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    db = _tick_db([_tick_row('a1', uuids='not json')])
    watch.sponsorblock_tick(db)
    assert queued == [('a1', True)]
    assert db.update_episode_sponsorblock_by_pk.call_args.args == (7,)


def test_tick_known_duration_mismatch_counts_as_no_marks(monkeypatch):
    queued = _patch_tick(monkeypatch)
    raw = [dict(MARK[0], videoDuration=500)]
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: raw)
    db = _tick_db([dict(_tick_row('a1', state='probing', uuids='[]'), sb_attempts=1)])
    watch.sponsorblock_tick(db)
    assert queued == []
    fields = db.update_episode_sponsorblock_by_pk.call_args.kwargs
    assert fields['sb_attempts'] == 2 and fields['sb_state'] == 'probing' and fields['sb_next_check_at']


def test_tick_returns_early_when_sponsorblock_is_unreachable(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'availability', lambda db: (False, None))
    monkeypatch.setattr(watch.sponsorblock, 'record_availability', lambda db: False)
    fetched = []
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: fetched.append(vid) or MARK)
    db = _tick_db([_tick_row('a1', state='waiting', uuids='[]')])
    watch.sponsorblock_tick(db)
    assert fetched == [] and queued == []
    db.update_episode_sponsorblock_by_pk.assert_not_called()


def _probe_calls(monkeypatch, stored, verdict, rows):
    _patch_tick(monkeypatch)
    probes = []
    monkeypatch.setattr(watch.sponsorblock, 'availability', lambda db: (stored, None))
    monkeypatch.setattr(watch.sponsorblock, 'record_availability', lambda db: probes.append(1) or verdict)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    watch.sponsorblock_tick(_tick_db(rows))
    return probes


def test_tick_probes_only_when_the_stored_verdict_is_not_true(monkeypatch):
    row = [_tick_row('a1', state='waiting', uuids='[]')]
    assert _probe_calls(monkeypatch, True, True, row) == []
    assert _probe_calls(monkeypatch, True, True, []) == []
    assert _probe_calls(monkeypatch, False, True, []) == [1]
    assert _probe_calls(monkeypatch, None, True, []) == [1]


def test_tick_false_verdict_probes_and_returns_early_while_down(monkeypatch):
    probes = _probe_calls(monkeypatch, False, False, [_tick_row('a1', state='waiting', uuids='[]')])
    assert probes == [1]


def test_tick_fetch_failure_with_good_verdict_records_the_outage(monkeypatch):
    _patch_tick(monkeypatch)
    recorded = []
    monkeypatch.setattr(watch, 'record_probe_state', lambda db, service, ok: recorded.append((service, ok)))
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: None)
    db = _tick_db([_tick_row(f'a{i}', state='waiting', uuids='[]') for i in range(2)])
    watch.sponsorblock_tick(db)
    assert recorded == [('sponsorblock', False)]
    assert db.update_episode_sponsorblock_by_pk.call_count == 2


def test_tick_logs_transitions_from_the_stored_verdict(monkeypatch, caplog):
    def run(stored, verdict):
        caplog.clear()
        with caplog.at_level('INFO', logger=watch.logger.name):
            _probe_calls(monkeypatch, stored, verdict, [_tick_row('a1', state='waiting', uuids='[]')])
        return [r.levelname for r in caplog.records if 'SponsorBlock is' in r.getMessage()]
    assert run(None, False) == ['WARNING']
    assert run(False, False) == []
    assert run(False, True) == ['INFO']
    assert run(None, True) == []


def test_tick_processes_when_sponsorblock_is_reachable(monkeypatch):
    queued = _patch_tick(monkeypatch)
    monkeypatch.setattr(watch.sponsorblock, 'fetch_segments', lambda vid: MARK)
    watch.sponsorblock_tick(_tick_db([_tick_row('a1', state='waiting', uuids='[]')]))
    assert queued == [('a1', False)]

"""YouTube episodes through process_episode: SponsorBlock-only, LLM fallback, pass 1 detection, waiting."""
import os
import shutil
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('youtube_pipeline_test_')

from main_app import processing
from main_app.pipeline import common, detect as detect_stage, finalize as finalize_stage, sponsorblock as sb_stage
from main_app.pipeline import transcribe, verify, signals, recut, publish
from youtube.settings import YouTubeSettings

PODCAST = {'id': 7, 'slug': 'yt-feed', 'feed_type': 'youtube', 'youtube_source_kind': 'playlist',
           'youtube_source_id': 'PL1', 'description': None, 'tags': None, 'dai_platform': None,
           'passthrough_enabled': None, 'skip_ad_detection': None, 'detection_mode': None,
           'yt_llm_on_top_override': None}
SEGMENTS = [{'start': 0.0, 'end': 60.0, 'text': 'hello', 'words': []}]
MARKERS = [{'start': 10.0, 'end': 40.0, 'category': 'sponsor', 'confidence': 1.0, 'source': 'sponsorblock',
            'sb_uuid': 'u1', 'sb_action': 'skip', 'sb_votes': 3, 'sb_locked': False,
            'reason': 'SponsorBlock sponsor', 'sponsor': None, 'detection_stage': 'sponsorblock', 'was_cut': True}]
ACTIONS = {'sponsor': 'remove', 'self_promo': 'remove', 'intro': 'keep', 'outro': 'remove',
           'recap': 'remove', 'interaction': 'remove', 'cross_promo': 'remove'}


def _settings(**over):
    base = dict(enabled=True, process_delay_minutes=0, probe_attempts=6, probe_interval_minutes=30,
                watch_interval_hours=6, watch_max_days=7, llm_fallback=True, captions_as_transcript=True,
                transcribe_fallback=True, ytdlp_auto_update=True)
    base.update(over)
    return YouTubeSettings(**base)


def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
    return ads_to_remove


def _run(markers, *, llm_on_top=False, llm_fallback=True, episode_row=None, captions=True,
         saved_transcript=False, download_error=None, fetch_result=..., info=None, transcribe_fallback=True):
    """Drive process_episode for a YouTube feed; extra mocks land in _run.extras."""
    podcast = dict(PODCAST, yt_llm_on_top_override=1 if llm_on_top else None)
    work_dir = tempfile.mkdtemp(prefix='minuspod-yt-test-')
    audio = os.path.join(work_dir, 'vid000000001.mp3')
    open(audio, 'wb').write(b'ID3')
    try:
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            db.get_podcast_by_slug.return_value = podcast
            db.get_episode.return_value = episode_row or {'episode_id': 'ep1', 'status': 'pending',
                                                           'sb_state': 'probing',
                                                           'youtube_video_id': 'vid000000001'}
            db.get_podcast_cue_settings_overrides.return_value = {}
            db.get_setting.side_effect = lambda key: 'false'
            db.get_setting_bool.return_value = False
            db.get_false_positive_corrections.return_value = []
            db.get_confirmed_corrections.return_value = []
            db.get_setting_float.side_effect = lambda key, default=None: default
            db.get_all_settings.return_value = {}
            db.resolve_segment_actions.return_value = ACTIONS
            p(processing, 'status_service')
            storage = p(processing, 'storage')
            storage.get_original_path.return_value = MagicMock(exists=lambda: False)
            storage.get_transcript.return_value = 'saved text' if saved_transcript else None
            db.get_original_segments.return_value = SEGMENTS if saved_transcript else None
            db.get_repair_holes.return_value = []
            storage.get_episode_path.return_value = '/tmp/final.mp3'
            ap_singleton = p(processing, 'audio_processor')
            ap_singleton.get_audio_duration.return_value = 3600.0
            p(processing.ad_detector, 'get_model', return_value='m')
            p(processing.ad_detector, 'get_verification_model', return_value='m')
            p(processing, 'start_episode_token_tracking')
            p(processing, 'get_available_memory_gb', return_value=None)
            p(finalize_stage, 'get_available_memory_gb', return_value=None)
            p(common, 'get_min_cut_confidence', return_value=0.8)
            p(sb_stage.yt_settings, 'resolve_feed', return_value=_settings(llm_fallback=llm_fallback,
                                                                           transcribe_fallback=transcribe_fallback))
            dl = MagicMock(audio_path=audio, info=info or {'id': 'vid000000001'},
                           captions_path='/tmp/c.json3' if captions else None)
            p(sb_stage.download, 'download', return_value=dl, side_effect=download_error)
            update_binary = p(sb_stage.ytdlp, 'update_binary')
            repair = p(transcribe, '_repair_transcript',
                       side_effect=lambda slug, ep, path, segs, *a, **k: (segs, [], []))
            p(sb_stage.captions, 'load_segments', return_value=SEGMENTS if captions else [])
            p(sb_stage.sponsorblock, 'fetch_segments',
              return_value=([{'UUID': 'u1'}] if markers else []) if fetch_result is ... else fetch_result)
            if fetch_result is ...:
                p(sb_stage.sponsorblock, 'to_markers', return_value=(list(markers), None))
            tr = p(processing, 'transcriber')
            tr.transcribe_chunked.return_value = SEGMENTS
            tr.repair_gaps.return_value = ([], [])
            tr.get_audio_duration.return_value = 60.0
            whisper = tr.transcribe_chunked
            p(transcribe, '_download_episode_audio', return_value=audio)
            p(signals, '_run_differential_fetch', return_value=None)
            diff_spy = signals._run_differential_fetch
            p(signals, '_run_audio_analysis', return_value=None)
            p(processing, 'load_positional_prior', return_value=None)
            detect = p(detect_stage, '_detect_ads_first_pass', return_value=([], 0, {}))
            p(detect_stage, '_refine_and_validate',
              side_effect=lambda s, e, ads, *a, **k: (list(ads), list(ads)))
            reviewer = p(processing.review, '_run_ad_reviewer',
              side_effect=lambda s, e, pid, ads, all_ads, *a, **k: (ads, all_ads))
            sweeps = [p(processing.cuts, name, side_effect=_pass_through_ads)
                      for name in ('_snap_terminal_starts', '_complete_cut_tails',
                                   '_snap_completed_cut_tails_to_splice')]
            ap = p(processing, 'AudioProcessor')
            p(recut, 'AudioProcessor', new=ap)
            ap.return_value.process_episode.side_effect = (
                lambda audio_path, cuts, **kw: ('/tmp/out.mp3', [dict(c) for c in cuts]))
            ap.return_value.get_audio_duration.return_value = 3000.0
            verification = p(verify, '_run_verification_pass',
                             return_value=(0, [], [], [], '/tmp/out.mp3', 0, True, 0))
            p(publish, '_generate_assets')
            fin = p(finalize_stage, '_finalize_episode')
            after = p(sb_stage, 'after_success')
            p(processing.shutil, 'move')
            p(processing.os, 'unlink')
            p(processing.os.path, 'exists', return_value=False)
            ok = processing.process_episode('yt-feed', 'ep1', 'https://www.youtube.com/watch?v=vid000000001',
                                            'Ep', 'YT', None, None, None)
            _run.extras = dict(db=db, sweeps=sweeps, storage=storage, update_binary=update_binary, repair=repair,
                          reviewer=reviewer)
            return ok, whisper, detect, verification, ap, fin, after, diff_spy
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_sponsorblock_only_skips_whisper_detection_and_verification():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_not_called()
    assert verification.call_args.kwargs['skip_verification'] is True
    cut = ap.return_value.process_episode.call_args.args[1]
    assert [c['sb_uuid'] for c in cut] == ['u1']
    after.assert_called_once()
    assert after.call_args.args[2].mode == 'sponsorblock'


def test_sponsorblock_only_never_calls_the_reviewer_but_llm_on_top_does():
    _run(MARKERS)
    assert not _run.extras['reviewer'].called
    _run(MARKERS, llm_on_top=True)
    assert _run.extras['reviewer'].called


def test_download_fills_empty_description_and_date_only_published_at():
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'probing', 'youtube_video_id': 'vid000000001',
           'description': '', 'published_at': '2026-10-01'}
    info = {'id': 'vid000000001', 'description': 'Full text', 'timestamp': 1790000000}
    _run(MARKERS, episode_row=row, info=info)
    calls = [c.kwargs for c in _run.extras['db'].upsert_episode.call_args_list]
    assert {'description': 'Full text', 'published_at': '2026-09-21T14:13:20Z'} in calls


def test_download_keeps_existing_description_and_full_published_at():
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'probing', 'youtube_video_id': 'vid000000001',
           'description': 'Listed', 'published_at': '2026-10-01T12:00:00+00:00'}
    info = {'id': 'vid000000001', 'description': 'Full text', 'timestamp': 1790000000}
    _run(MARKERS, episode_row=row, info=info)
    assert not any(c.kwargs.get('description') == 'Full text' or c.kwargs.get('published_at', '').startswith('2026-09')
                   for c in _run.extras['db'].upsert_episode.call_args_list)


def test_never_processed_youtube_episode_serializes_its_thumbnail():
    from api.episodes import core
    ep = {'episode_id': 'e', 'title': 't', 'status': 'discovered', 'created_at': 'x', 'processed_at': None,
          'original_duration': None, 'new_duration': None, 'ads_removed': 0,
          'artwork_url': 'https://i.ytimg.com/vi/vid000000001/hqdefault.jpg'}
    assert core._episode_base_json(ep, slug='yt-feed', is_youtube=True)['artworkUrl'] == ep['artwork_url']


def test_no_marks_with_fallback_runs_llm_on_captions():
    ok, whisper, detect, verification, ap, fin, after, diff = _run([])
    assert ok is True
    whisper.assert_not_called()
    detect.assert_called_once()
    assert after.call_args.args[2].mode == 'llm'


def test_no_marks_no_captions_runs_whisper():
    ok, whisper, detect, *_ = _run([], captions=False)
    assert ok is True
    whisper.assert_called_once()
    detect.assert_called_once()


def test_no_captions_transcribe_off_with_marks_runs_sponsorblock_only():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS, captions=False, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_not_called()
    cut = ap.return_value.process_episode.call_args.args[1]
    assert [c['sb_uuid'] for c in cut] == ['u1']
    run = after.call_args.args[2]
    assert run.mode == 'sponsorblock' and run.note == 'no_captions'


def test_no_captions_transcribe_off_skips_llm_on_top():
    ok, whisper, detect, *_ = _run(MARKERS, captions=False, llm_on_top=True, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_not_called()


def _no_caption_run(row):
    with patch.object(recut, 'get_audio_codec', return_value='mp3'), patch.object(recut.shutil, 'move'):
        return _run([], captions=False, transcribe_fallback=False, llm_fallback=True, episode_row=row)


def test_no_captions_transcribe_off_without_marks_defers_when_not_forced():
    with patch.object(processing.failure, '_handle_processing_failure') as handled:
        ok, *_ = _no_caption_run({'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'watching',
                                  'youtube_video_id': 'vid000000001'})
    error = handled.call_args.args[5]
    assert ok is False and isinstance(error, sb_stage.SponsorBlockDeferred)
    assert 'No captions and transcription is off' in str(error)


def test_no_captions_transcribe_off_without_marks_serves_unmodified_when_exhausted_or_manual():
    for row in ({'sb_state': 'exhausted'}, {'sb_state': 'watching', 'reprocess_mode': 'llm'}):
        row = {'episode_id': 'ep1', 'status': 'pending', 'youtube_video_id': 'vid000000001', **row}
        ok, whisper, detect, verification, ap, fin, after, diff = _no_caption_run(row)
        assert ok is True
        whisper.assert_not_called()
        detect.assert_not_called()
        ap.return_value.process_episode.assert_not_called()
        fin.assert_called_once()
        run = after.call_args.args[2]
        assert run.mode == 'passthrough' and run.note == 'no_captions'


def test_no_captions_transcribe_on_without_marks_runs_whisper():
    ok, whisper, detect, *_ = _run([], captions=False, transcribe_fallback=True)
    assert ok is True
    whisper.assert_called_once()


def test_captions_present_ignore_transcribe_off():
    ok, whisper, detect, *_ = _run([], captions=True, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_called_once()


def test_llm_on_top_merges_sponsorblock_markers_into_detection():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS, llm_on_top=True)
    assert ok is True
    detect.assert_called_once()
    cut = ap.return_value.process_episode.call_args.args[1]
    assert any(c.get('sb_uuid') == 'u1' for c in cut)


def test_mute_segment_is_rendered_as_beep():
    muted = [dict(MARKERS[0], sb_action='mute')]
    ok, whisper, detect, verification, ap, *_ = _run(muted)
    assert ok is True
    cut = ap.return_value.process_episode.call_args.args[1]
    assert cut[0]['beep'] is True
    saved = _run.extras['storage'].save_combined_ads.call_args.args[2]
    assert [m['action_applied'] for m in saved] == ['beep']


def test_sponsorblock_only_skips_boundary_sweeps_but_llm_path_runs_them():
    _run(MARKERS)
    assert all(not sweep.called for sweep in _run.extras['sweeps'])
    _run([])
    assert all(sweep.called for sweep in _run.extras['sweeps'])


def test_saved_caption_transcript_is_reused_without_whisper_or_repair():
    ok, whisper, detect, *_ = _run([], saved_transcript=True)
    assert ok is True
    whisper.assert_not_called()
    _run.extras['repair'].assert_not_called()
    detect.assert_called_once()


def _failed(error, **kw):
    with patch.object(processing.failure, '_handle_processing_failure') as handled:
        ok, *_ = _run([], llm_fallback=False, download_error=error, **kw)
    return ok, handled


def test_outdated_ytdlp_triggers_one_update_and_fails():
    ok, handled = _failed(sb_stage.download.DownloadError('Unable to extract', needs_update=True))
    assert ok is False and handled.called
    _run.extras['update_binary'].assert_called_once()


def test_bot_wall_error_does_not_update_ytdlp():
    ok, handled = _failed(sb_stage.download.DownloadError('Sign in to confirm', needs_update=False))
    assert ok is False and handled.called
    _run.extras['update_binary'].assert_not_called()


def test_manual_llm_reprocess_runs_llm_without_marks_or_fallback():
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'watching',
           'youtube_video_id': 'vid000000001', 'reprocess_mode': 'llm'}
    ok, whisper, detect, verification, ap, fin, after, diff = _run([], llm_fallback=False, episode_row=row)
    assert ok is True
    detect.assert_called_once()


def test_exhausted_episode_runs_llm_without_marks_or_fallback():
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'exhausted',
           'youtube_video_id': 'vid000000001'}
    ok, whisper, detect, *_ = _run([], llm_fallback=False, episode_row=row)
    assert ok is True
    detect.assert_called_once()


def test_differential_fetch_skips_youtube_feeds():
    with patch.object(signals, 'resolve_differential_fetch_mode', side_effect=AssertionError('reached')):
        assert signals._run_differential_fetch('yt-feed', 'ep1', 'https://x', '/a.mp3', 7, podcast=PODCAST) is None


def _after(run, episode_data=None):
    with patch.object(processing, 'db') as db:
        sb_stage.after_success('yt-feed', 'ep1', run, episode_data)
    return db.update_episode_sponsorblock.call_args


def _yt_run(mode, markers):
    return sb_stage.YouTubeRun('/a.mp3', [], markers, None, mode, _settings())


def test_after_success_states():
    done = _after(_yt_run('sponsorblock', MARKERS))
    assert done.kwargs['sb_state'] == 'done' and done.kwargs['sb_next_check_at']
    assert done.kwargs['sb_last_error'] is None and done.kwargs['sb_segment_uuids'] == '["u1"]'
    top = _after(_yt_run('llm', MARKERS))
    assert top.kwargs['sb_state'] == 'done'
    llm = _after(_yt_run('llm', []), {'sb_state': 'watching'})
    assert llm.kwargs['sb_state'] == 'llm' and llm.kwargs['sb_next_check_at'] is None
    assert _after(_yt_run('llm', []), {'sb_state': 'llm'}).kwargs['sb_state'] == 'llm'
    assert _after(_yt_run('llm', []), {'sb_state': 'exhausted'}).kwargs['sb_state'] == 'exhausted'
    served = _after(_yt_run('passthrough', []), {'sb_state': 'watching'})
    assert served.kwargs['sb_state'] == 'exhausted' and served.kwargs['sb_next_check_at'] is None


def _after_with_storage(episode_data, has_artwork=False, download_error=None):
    with patch.object(processing, 'db'), patch.object(processing, 'storage') as storage:
        storage.has_episode_artwork.return_value = has_artwork
        storage.download_episode_artwork.side_effect = download_error
        sb_stage.after_success('yt-feed', 'ep1', _yt_run('sponsorblock', MARKERS), episode_data)
    return storage


def test_after_success_caches_episode_thumbnail_once():
    storage = _after_with_storage({'artwork_url': 'https://i.ytimg.com/t.jpg'})
    storage.download_episode_artwork.assert_called_once_with('yt-feed', 'ep1', 'https://i.ytimg.com/t.jpg')


def test_after_success_skips_thumbnail_when_missing_or_cached():
    assert not _after_with_storage({'artwork_url': None}).download_episode_artwork.called
    assert not _after_with_storage(None).download_episode_artwork.called
    assert not _after_with_storage({'artwork_url': 'https://i.ytimg.com/t.jpg'},
                                   has_artwork=True).download_episode_artwork.called


def test_after_success_thumbnail_failure_does_not_propagate():
    storage = _after_with_storage({'artwork_url': 'https://i.ytimg.com/t.jpg'}, download_error=OSError('boom'))
    storage.download_episode_artwork.assert_called_once()


def _deferred(**kw):
    with patch.object(processing.failure, '_handle_processing_failure') as handled:
        ok, *_ = _run([], llm_fallback=False, **kw)
    assert ok is False
    error = handled.call_args.args[5]
    assert isinstance(error, sb_stage.SponsorBlockDeferred) and error.next_check_at
    return error


def test_no_marks_without_fallback_defers_instead_of_failing():
    _deferred()


def test_request_failure_defers_even_with_fallback_on():
    with patch.object(processing.failure, '_handle_processing_failure') as handled:
        ok, *_ = _run([], llm_fallback=True, fetch_result=None)
    assert ok is False and isinstance(handled.call_args.args[5], sb_stage.SponsorBlockDeferred)


def test_all_filtered_marks_defer():
    raw = [{'UUID': 'u2', 'votes': -3}]
    _deferred(fetch_result=raw)


def test_duration_mismatch_without_fallback_defers_and_persists_the_duration():
    raw = [{'UUID': 'u1', 'segment': [1, 2], 'category': 'sponsor', 'actionType': 'skip', 'votes': 1,
            'locked': 0, 'videoDuration': 100}]
    _deferred(fetch_result=raw)
    _run.extras['db'].upsert_episode.assert_any_call('yt-feed', 'ep1', original_duration=3600.0)


def test_duration_mismatch_with_fallback_runs_llm():
    raw = [{'UUID': 'u1', 'segment': [1, 2], 'category': 'sponsor', 'actionType': 'skip', 'votes': 1,
            'locked': 0, 'videoDuration': 100}]
    ok, *_ = _run([], llm_fallback=True, fetch_result=raw)
    assert ok is True


def _handle_deferral(episode_data, error=None):
    from main_app.pipeline import failure
    error = error or sb_stage.SponsorBlockDeferred('No SponsorBlock marks yet', '2026-10-29T18:00:00+00:00')
    with patch.object(failure, 'db') as db, patch.object(failure.common, '_require_publication_owner'), \
            patch.object(failure.common, '_publish_status'), patch.object(failure, 'finalize') as fin:
        failure._handle_processing_failure('yt-feed', 'ep1', 'Ep', 'YT', episode_data, error, 0.0)
    return db, fin


def test_deferral_restores_status_keeps_retry_count_and_arms_watch():
    db, fin = _handle_deferral({'status': 'pending', 'sb_state': 'watching', 'retry_count': 2})
    kwargs = db.upsert_episode.call_args.kwargs
    assert kwargs['status'] == 'discovered' and kwargs['error_message'] is None
    assert 'retry_count' not in kwargs
    sb = db.update_episode_sponsorblock.call_args.kwargs
    assert sb['sb_state'] == 'watching' and sb['sb_next_check_at'] == '2026-10-29T18:00:00+00:00'
    fin._record_history_row.assert_not_called()


def test_deferral_keeps_probing_state_while_probing():
    db, _ = _handle_deferral({'status': 'pending', 'sb_state': 'probing'})
    assert db.update_episode_sponsorblock.call_args.kwargs['sb_state'] == 'probing'


def test_deferral_leaves_an_exhausted_row_exhausted():
    db, _ = _handle_deferral({'status': 'pending', 'sb_state': 'exhausted'})
    assert db.update_episode_sponsorblock.call_args.kwargs['sb_state'] == 'exhausted'


def test_recut_success_stamps_uuids_from_stored_markers_and_failure_does_not():
    import json
    stored = json.dumps(MARKERS + [{'start': 1.0, 'end': 2.0, 'source': 'llm'}])
    with patch.object(sb_stage, 'db') as db, patch.object(sb_stage.yt_settings, 'resolve_feed', return_value=_settings()):
        db.get_episode.return_value = {'ad_markers_json': stored}
        sb_stage.stamp_recut('yt-feed', 'ep1', PODCAST)
    fields = db.update_episode_sponsorblock.call_args.kwargs
    assert fields['sb_segment_uuids'] == '["u1"]' and fields['sb_next_check_at']
    for outcome in (True, False):
        with patch.object(processing, 'db') as pdb, patch.object(processing.recut, '_recut_episode', return_value=outcome), \
                patch.object(processing, 'start_episode_token_tracking'), \
                patch.object(processing.admission, '_resolve_or_load_route_snapshot', return_value=None), \
                patch.object(processing.admission, '_assert_route_snapshot_current'), \
                patch.object(sb_stage, 'stamp_recut') as stamp:
            pdb.get_episode.return_value = {'reprocess_mode': 'recut'}
            pdb.get_podcast_by_slug.return_value = PODCAST
            assert processing.process_episode('yt-feed', 'ep1', 'u', 'Ep', 'YT', None, None, None) is outcome
        assert stamp.called is outcome


def test_sponsorblock_markers_are_never_learned_from():
    from main_app.pipeline import keep_rules
    sb = dict(MARKERS[0])
    with patch.object(processing, 'ad_detector') as detector:
        assert keep_rules._learn_from_kept_ads('yt-feed', 'ep1', [sb], SEGMENTS, '/a.mp3') == 0
        assert keep_rules._learn_from_applied_cut_ads(
            'yt-feed', 'ep1', [sb], [sb], [(10.0, 40.0)], 3600.0, SEGMENTS, '/a.mp3') == 0
    detector.learn_from_detections.assert_not_called()


def test_episode_json_carries_sponsorblock_block_only_for_youtube():
    from api.episodes import core
    ep = {'episode_id': 'e', 'title': 't', 'status': 'pending', 'created_at': 'x', 'processed_at': None,
          'original_duration': None, 'new_duration': None, 'ads_removed': 0, 'sb_state': 'done',
          'sb_attempts': 2, 'sb_next_check_at': 'n', 'sb_segment_uuids': '["a","b"]', 'sb_note': 'nt',
          'sb_last_error': None}
    assert core._episode_base_json(ep)['sponsorBlock'] is None
    block = core._episode_base_json(ep, is_youtube=True)['sponsorBlock']
    assert block == {'state': 'done', 'attempts': 2, 'nextCheckAt': 'n', 'segmentCount': 2,
                     'note': 'nt', 'lastError': None}

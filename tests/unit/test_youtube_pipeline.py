"""YouTube episodes through process_episode: SponsorBlock-only, LLM fallback, LLM on top, waiting."""
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
                ytdlp_auto_update=True)
    base.update(over)
    return YouTubeSettings(**base)


def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
    return ads_to_remove


def _run(markers, *, llm_on_top=False, llm_fallback=True, episode_row=None, captions=True, cut_override=None):
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
            storage.get_transcript.return_value = None
            storage.get_episode_path.return_value = '/tmp/final.mp3'
            ap_singleton = p(processing, 'audio_processor')
            ap_singleton.get_audio_duration.return_value = 3600.0
            p(processing.ad_detector, 'get_model', return_value='m')
            p(processing.ad_detector, 'get_verification_model', return_value='m')
            p(processing, 'start_episode_token_tracking')
            p(processing, 'get_available_memory_gb', return_value=None)
            p(finalize_stage, 'get_available_memory_gb', return_value=None)
            p(common, 'get_min_cut_confidence', return_value=0.8)
            p(sb_stage.yt_settings, 'resolve_feed', return_value=_settings(llm_fallback=llm_fallback))
            dl = MagicMock(audio_path=audio, info={'id': 'vid000000001'},
                           captions_path='/tmp/c.json3' if captions else None, chapters=[])
            p(sb_stage.download, 'download', return_value=dl)
            p(sb_stage.captions, 'load_segments', return_value=SEGMENTS if captions else [])
            p(sb_stage.sponsorblock, 'fetch_segments', return_value=[{'UUID': 'u1'}] if markers else [])
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
            p(processing.review, '_run_ad_reviewer',
              side_effect=lambda s, e, pid, ads, all_ads, *a, **k: (ads, all_ads))
            p(processing.cuts, '_snap_terminal_starts', side_effect=_pass_through_ads)
            p(processing.cuts, '_complete_cut_tails', side_effect=_pass_through_ads)
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
            return ok, whisper, detect, verification, ap, fin, after, diff_spy
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_sponsorblock_only_skips_whisper_detection_and_verification():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_not_called()
    verification.assert_not_called()
    cut = ap.return_value.process_episode.call_args.args[1]
    assert [c['sb_uuid'] for c in cut] == ['u1']
    after.assert_called_once()
    assert after.call_args.args[2].mode == 'sponsorblock'


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


def test_differential_fetch_skips_youtube_feeds():
    with patch.object(signals, 'resolve_differential_fetch_mode', side_effect=AssertionError('reached')):
        assert signals._run_differential_fetch('yt-feed', 'ep1', 'https://x', '/a.mp3', 7, podcast=PODCAST) is None


def _after(run, episode_data=None):
    with patch.object(processing, 'db') as db:
        sb_stage.after_success('yt-feed', 'ep1', run, episode_data)
    return db.update_episode_sponsorblock.call_args


def _yt_run(mode, markers):
    return sb_stage.YouTubeRun('/a.mp3', [], markers, None, mode, False, _settings())


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


def test_no_marks_without_fallback_fails_with_waiting_message():
    with patch.object(processing.failure, '_handle_processing_failure') as handled:
        ok, *_ = _run([], llm_fallback=False)
    assert ok is False
    error = handled.call_args.args[5]
    assert isinstance(error, sb_stage.SponsorBlockUnavailable)


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

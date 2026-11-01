"""YouTube episodes through process_episode: SponsorBlock-only, LLM fallback, pass 1 detection, waiting."""
import os
import shutil
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

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
RETAINED = '/data/retained/original.mp3'
ACTIONS = {'sponsor': 'remove', 'self_promo': 'remove', 'intro': 'keep', 'outro': 'remove',
           'recap': 'remove', 'interaction': 'remove', 'cross_promo': 'remove'}


def _settings(**over):
    base = dict(enabled=True, process_delay_minutes=0, probe_attempts=6, probe_interval_minutes=30,
                watch_interval_hours=6, watch_max_days=7, llm_fallback=True, captions_as_transcript=True,
                hide_private=True, transcribe_fallback=True, ytdlp_auto_update=True, ytdlp_extra_args='')
    base.update(over)
    return YouTubeSettings(**base)


def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
    return ads_to_remove


def _run(markers, *, llm_on_top=False, llm_fallback=True, episode_row=None, captions=True,
         saved_transcript=False, download_error=None, fetch_result=..., info=None, transcribe_fallback=True,
         retained=False, caption_file=None, reprocess_mode=None, pattern_hits=()):
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
                                                           'youtube_video_id': 'vid000000001',
                                                           'reprocess_mode': reprocess_mode}
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
            storage.get_original_path.return_value = RETAINED if retained else MagicMock(exists=lambda: False)
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
            fetch_captions = p(sb_stage.download, 'fetch_captions', return_value=caption_file)
            p(common, '_copy_retained_original_to_temp', return_value=audio)
            p(sb_stage.captions, 'load_segments', return_value=SEGMENTS if captions else [])
            p(sb_stage.sponsorblock, 'is_unreachable', return_value=False)
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
            audio_analysis = p(signals, '_run_audio_analysis', return_value=None)
            p(processing, 'load_positional_prior', return_value=None)
            detect = p(detect_stage, '_detect_ads_first_pass',
                       return_value=(list(pattern_hits), len(pattern_hits), {}))
            refine = p(detect_stage, '_refine_and_validate',
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
            p(processing.os.path, 'exists', side_effect=lambda path: retained and path == RETAINED)
            ok = processing.process_episode('yt-feed', 'ep1', 'https://www.youtube.com/watch?v=vid000000001',
                                            'Ep', 'YT', None, None, None)
            _run.extras = dict(fetch_captions=fetch_captions, db=db, sweeps=sweeps, storage=storage, update_binary=update_binary, repair=repair,
                          reviewer=reviewer, refine=refine, audio_analysis=audio_analysis)
            return ok, whisper, detect, verification, ap, fin, after, diff_spy
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


PATTERN_HIT = {'start': 50.0, 'end': 58.0, 'category': 'sponsor', 'confidence': 0.95,
               'detection_stage': 'text_pattern', 'reason': 'pattern', 'sponsor': 'Acme', 'was_cut': True}


def test_sponsorblock_only_runs_patterns_with_llm_off_and_skips_verification():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS, pattern_hits=[PATTERN_HIT])
    assert ok is True
    whisper.assert_not_called()
    detect.assert_called_once()
    assert detect.call_args.kwargs['skip_llm'] is True
    assert detect.call_args.kwargs['force_create_from_pairs'] is False
    assert verification.call_args.kwargs['skip_verification'] is True
    refined = _run.extras['refine'].call_args
    assert [a['detection_stage'] for a in refined.args[2]] == ['text_pattern']
    assert refined.kwargs['apply_heuristic_rolls'] is False
    cut = ap.return_value.process_episode.call_args.args[1]
    assert sorted(c['start'] for c in cut) == [10.0, 50.0]
    assert [c.get('sb_uuid') for c in sorted(cut, key=lambda c: c['start'])] == ['u1', None]
    assert after.call_args.args[2].mode == 'sponsorblock'


def test_sponsorblock_only_without_captions_still_runs_the_pattern_stages():
    ok, whisper, detect, *_ = _run(MARKERS, captions=False, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()
    detect.assert_called_once()
    assert detect.call_args.args[1] == [] and detect.call_args.kwargs['skip_llm'] is True


def test_pattern_hit_covered_by_a_mark_is_dropped_and_a_distant_one_kept():
    hits = [dict(PATTERN_HIT, start=100.0, end=130.0), dict(PATTERN_HIT, start=300.0, end=320.0)]
    mark = dict(MARKERS[0], start=100.0, end=132.0)
    ok, whisper, detect, verification, ap, *_ = _run([mark], pattern_hits=hits)
    cut = ap.return_value.process_episode.call_args.args[1]
    assert sorted((c['start'], c['end']) for c in cut) == [(100.0, 132.0), (300.0, 320.0)]
    assert [c.get('sb_uuid') for c in sorted(cut, key=lambda c: c['start'])] == ['u1', None]


def test_llm_detection_covered_by_a_mark_is_dropped_with_pass_1_on():
    llm_hit = dict(PATTERN_HIT, detection_stage='claude', start=100.0, end=130.0)
    mark = dict(MARKERS[0], start=100.0, end=132.0)
    ok, whisper, detect, verification, ap, *_ = _run([mark], llm_on_top=True, pattern_hits=[llm_hit])
    cut = ap.return_value.process_episode.call_args.args[1]
    assert [(c['start'], c['end'], c.get('sb_uuid')) for c in cut] == [(100.0, 132.0, 'u1')]


def test_kept_category_on_a_sponsorblock_mark_is_still_kept():
    kept = [dict(MARKERS[0], category='intro')]
    ok, whisper, detect, verification, ap, *_ = _run(kept)
    assert ok is True
    assert not ap.return_value.process_episode.call_args.args[1]
    saved = _run.extras['storage'].save_combined_ads.call_args.args[2]
    assert [m['category'] for m in saved] == ['intro']


def test_sponsorblock_only_never_calls_the_reviewer_but_llm_on_top_does():
    _run(MARKERS)
    assert not _run.extras['reviewer'].called
    _run(MARKERS, llm_on_top=True)
    assert _run.extras['reviewer'].called


def test_full_and_llm_reprocess_force_pass_1_while_plain_reprocess_keeps_marks_only():
    for mode, expected in (('full', 'llm'), ('llm', 'llm'), ('reprocess', 'sponsorblock')):
        ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS, reprocess_mode=mode)
        assert after.call_args.args[2].mode == expected
        assert detect.call_args.kwargs['skip_llm'] is (expected == 'sponsorblock')


def test_download_fills_empty_description_and_date_only_published_at():
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'probing', 'youtube_video_id': 'vid000000001',
           'description': '', 'published_at': '2026-10-01'}
    info = {'id': 'vid000000001', 'description': 'Full text', 'timestamp': 1790000000}
    _run(MARKERS, episode_row=row, info=info)
    calls = [c.kwargs for c in _run.extras['db'].upsert_episode.call_args_list]
    assert any(c.get('description') == 'Full text' and c.get('published_at') == '2026-09-21T14:13:20Z'
               and c.get('yt_metadata_at') for c in calls)


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
    cut = ap.return_value.process_episode.call_args.args[1]
    assert [c['sb_uuid'] for c in cut] == ['u1']
    run = after.call_args.args[2]
    assert run.mode == 'sponsorblock' and run.note == 'no_captions'


def test_no_captions_transcribe_off_skips_llm_on_top():
    ok, whisper, detect, *_ = _run(MARKERS, captions=False, llm_on_top=True, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()
    assert detect.call_args.kwargs['skip_llm'] is True


FORCED_OVERRIDES = [{'reprocess_mode': 'llm'}, {'sb_state': 'exhausted'}, {'reprocess_source': 'jit'}]


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


@pytest.mark.parametrize('override', FORCED_OVERRIDES)
def test_no_captions_transcribe_off_without_marks_serves_unmodified_when_forced(override):
    row = {'episode_id': 'ep1', 'status': 'pending', 'youtube_video_id': 'vid000000001', **override}
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


def test_llm_on_top_appends_sponsorblock_markers_after_validation():
    ok, whisper, detect, verification, ap, fin, after, diff = _run(MARKERS, llm_on_top=True)
    assert ok is True
    detect.assert_called_once()
    assert detect.call_args.kwargs['skip_llm'] is False
    assert not _run.extras['refine'].call_args.args[2]
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


def test_sponsorblock_only_skips_audio_analysis_but_llm_path_runs_it():
    ok, *_, fin, _after_mock, _diff = _run(MARKERS)
    assert ok is True
    _run.extras['audio_analysis'].assert_not_called()
    assert fin.call_args.kwargs['run_stats']['audio_analysis_skipped'] is True
    ok, *_, fin, _after_mock, _diff = _run([])
    _run.extras['audio_analysis'].assert_called_once()
    assert fin.call_args.kwargs['run_stats']['audio_analysis_skipped'] is False


def test_saved_caption_transcript_is_reused_without_whisper_or_repair():
    ok, whisper, detect, *_ = _run([], saved_transcript=True)
    assert ok is True
    whisper.assert_not_called()
    _run.extras['repair'].assert_not_called()
    detect.assert_called_once()


def _caption_file(tmp_path):
    (tmp_path / 'caps').mkdir()
    path = tmp_path / 'caps' / 'vid000000001.en.json3'
    path.write_text('{}')
    return str(path)


def test_retained_original_fetches_captions_as_the_transcript(tmp_path):
    caption_file = _caption_file(tmp_path)
    with patch.object(sb_stage.shutil, 'rmtree') as rmtree:
        ok, whisper, *_ = _run([], retained=True, caption_file=caption_file)
    assert ok is True
    rmtree.assert_any_call(os.path.dirname(caption_file), ignore_errors=True)
    _run.extras['fetch_captions'].assert_called_once_with('vid000000001', language=None)
    whisper.assert_not_called()
    _run.extras['db'].clear_episode_details.assert_not_called()


def test_reprocess_without_captions_keeps_the_saved_transcript():
    ok, whisper, *_ = _run(MARKERS, retained=True, saved_transcript=True, transcribe_fallback=False,
                           reprocess_mode='reprocess')
    assert ok is True
    _run.extras['fetch_captions'].assert_called_once()
    whisper.assert_not_called()
    _run.extras['db'].clear_episode_details.assert_not_called()
    _run.extras['db'].get_original_segments.assert_called()


def test_retained_original_without_captions_or_transcript_skips_transcription():
    ok, whisper, *_ = _run(MARKERS, retained=True, transcribe_fallback=False)
    assert ok is True
    whisper.assert_not_called()


def test_caption_fetch_failure_does_not_fail_the_run():
    with patch.object(sb_stage.download, 'fetch_captions',
                      side_effect=sb_stage.download.DownloadError('timed out')) as fetch:
        segments = sb_stage._fetch_retained_captions('yt-feed', 'ep1', 'vid000000001', 'en')
    fetch.assert_called_once()
    assert segments == []


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


@pytest.mark.parametrize('override', FORCED_OVERRIDES)
def test_forced_episode_runs_llm_without_marks_or_fallback(override):
    row = {'episode_id': 'ep1', 'status': 'pending', 'sb_state': 'watching',
           'youtube_video_id': 'vid000000001', **override}
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


def test_sponsorblock_markers_are_learned_like_detections():
    from main_app.pipeline import keep_rules
    sb = dict(MARKERS[0], action_applied='remove')
    with patch.object(keep_rules, 'ad_detector') as detector:
        detector.learn_from_detections.return_value = 1
        assert keep_rules._learn_from_applied_cut_ads(
            'yt-feed', 'ep1', [sb], [sb], [{'start': 10.0, 'end': 40.0}], 3600.0, SEGMENTS, '/a.mp3') == 1
    assert detector.learn_from_detections.call_args.args[0] == [sb]


def test_marks_are_named_from_caption_text_or_category_label():
    plain = [{'start': 12.0, 'end': 20.0, 'text': 'Support the show today'}]
    named = [{'start': 12.0, 'end': 20.0, 'text': 'This is brought to you by Acme'}]
    with patch.object(sb_stage, 'sponsor_service') as svc:
        svc.find_sponsor_in_text.side_effect = lambda text: 'Acme' if 'acme' in text.lower() else None
        base = {'start': 10.0, 'end': 40.0, 'category': 'sponsor'}
        marks = [dict(base), dict(base), dict(base, category='intro')]
        sb_stage._name_marks(marks[:1], named)
        sb_stage._name_marks(marks[1:], plain)
    assert marks[0]['sponsor'] == 'Acme' and 'label_sponsor' not in marks[0]
    assert marks[1]['sponsor'] == 'Sponsor read' and marks[1]['label_sponsor'] is True
    assert marks[2]['sponsor'] == 'Intro' and marks[2]['label_sponsor'] is True


def test_label_sponsor_mark_is_not_a_learning_candidate():
    from ad_detector import AdDetector
    detector = AdDetector.__new__(AdDetector)
    detector.db = None
    sb = dict(MARKERS[0], category='self_promo', sponsor='Self promotion', label_sponsor=True)
    assert not detector._ad_passes_learning_filters(sb, 0.8)
    assert detector._ad_passes_learning_filters({k: v for k, v in sb.items() if k != 'label_sponsor'}, 0.8)


def test_episode_json_carries_sponsorblock_block_only_for_youtube():
    from api.episodes import core
    ep = {'episode_id': 'e', 'title': 't', 'status': 'pending', 'created_at': 'x', 'processed_at': None,
          'original_duration': None, 'new_duration': None, 'ads_removed': 0, 'sb_state': 'done',
          'sb_attempts': 2, 'sb_next_check_at': 'n', 'sb_segment_uuids': '["a","b"]', 'sb_note': 'nt',
          'sb_last_error': None}
    assert core._episode_base_json(ep)['sponsorBlock'] is None
    block = core._episode_base_json(ep, is_youtube=True)['sponsorBlock']
    assert block == {'state': 'done', 'attempts': 2, 'nextCheckAt': 'n', 'segmentCount': 2,
                     'note': 'nt', 'lastError': None, 'submissions': []}


class _Stop(Exception):
    pass


def _acquire(unreachable, *, episode_data=None, reprocess_mode=None):
    """Call sb_stage.acquire with the download stubbed; returns (download mock, result or exception)."""
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        p(sb_stage, 'db')
        p(sb_stage.yt_settings, 'resolve_feed', return_value=_settings())
        p(sb_stage.sponsorblock, 'is_unreachable', return_value=unreachable)
        p(sb_stage, 'storage').get_original_path.return_value = None
        p(sb_stage.common, '_publish_status')
        download = p(sb_stage.download, 'download', side_effect=_Stop())
        try:
            sb_stage.acquire('yt-feed', 'ep1', 'https://example.com/v', PODCAST,
                             dict(episode_data or {}, youtube_video_id='vid000000001'), reprocess_mode)
            outcome = None
        except (sb_stage.SponsorBlockDeferred, _Stop) as exc:
            outcome = exc
    return download, outcome


def test_acquire_defers_before_download_when_sponsorblock_is_unreachable():
    download, outcome = _acquire(True, episode_data={'sb_state': 'probing'})
    assert isinstance(outcome, sb_stage.SponsorBlockDeferred) and 'unreachable' in str(outcome)
    download.assert_not_called()


def test_acquire_proceeds_when_forced_even_if_unreachable():
    for data, mode in (({'sb_state': 'probing'}, 'full'), ({'sb_state': 'exhausted'}, None)):
        download, outcome = _acquire(True, episode_data=data, reprocess_mode=mode)
        assert isinstance(outcome, _Stop)
        download.assert_called_once()


def test_acquire_proceeds_when_reachable():
    download, outcome = _acquire(False, episode_data={'sb_state': 'probing'})
    assert isinstance(outcome, _Stop)
    download.assert_called_once()

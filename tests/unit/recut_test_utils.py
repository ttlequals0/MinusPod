"""Shared _recut_episode drivers with the cut list and renderer stubbed; import after bootstrap."""
import json
import time
from contextlib import ExitStack
from unittest.mock import patch

import main_app.processing as processing
from config import DEFAULT_SEGMENT_ACTION, SEGMENT_CATEGORIES

ALL_REMOVE = {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


def stub_recut(stack, ads_to_remove, all_ads, keep_ads=(), *, episode_row, duration,
               segment_actions=None, confirmed=(), fp=()):
    """Patch _recut_episode's collaborators around a fixed cut list; returns (db, storage, local_ap)."""
    p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
    db = p(processing, 'db')
    storage = p(processing, 'storage')
    p(processing, 'status_service')
    p(processing, '_copy_retained_original_to_temp', return_value='/tmp/recut-work.mp3')
    p(processing, '_build_recut_ad_list',
      return_value=(ads_to_remove, all_ads, list(keep_ads), []))
    p(processing.os.path, 'exists', return_value=False)
    local_ap = p(processing, 'AudioProcessor').return_value
    db.get_episode.return_value = episode_row
    db.get_original_segments.return_value = [{'start': 0.0, 'end': duration}]
    db.get_all_settings.return_value = {}
    db.resolve_segment_actions.return_value = segment_actions or {}
    db.get_confirmed_corrections.return_value = list(confirmed)
    db.get_false_positive_corrections.return_value = list(fp)
    storage.get_original_path.return_value.exists.return_value = True
    storage.get_applied_cuts.return_value = None
    storage.get_episode_path.return_value = '/tmp/recut-final.mp3'
    return db, storage, local_ap


def run_action_recut(ads_to_remove, all_ads, segment_actions, podcast_id=1,
                     confirmed_corrections=(), render_calls=None, fp_corrections=(),
                     podcast_row=None):
    """Recut after the builder's keep partition; returns (rendered segments, saved markers)."""
    keep_ads, _rest = processing._partition_keep_ads(all_ads, segment_actions)
    keep_ids = {id(ad) for ad in keep_ads}
    ads_to_remove = [ad for ad in ads_to_remove if id(ad) not in keep_ids]
    with ExitStack() as stack:
        _db, storage, local_ap = stub_recut(
            stack, ads_to_remove, all_ads, keep_ads,
            episode_row={'podcast_id': podcast_id, 'processed_version': 0}, duration=60.0,
            segment_actions=segment_actions, confirmed=confirmed_corrections,
            fp=fp_corrections)
        stack.enter_context(patch.object(processing, '_generate_assets'))
        stack.enter_context(patch.object(processing, '_finalize_episode'))
        stack.enter_context(patch.object(processing.shutil, 'move'))
        local_ap.get_audio_duration.return_value = 60.0
        local_ap.process_episode.side_effect = (
            lambda work_path, segs, cut_barriers=None, hard_barriers=None: (
                '/tmp/recut-cut.mp3', [{'start': s['start'], 'end': s['end']} for s in segs]))

        result = processing._recut_episode(
            'segrerender-feed', 'ep1', 'Episode', 'Podcast', 'desc',
            time.time(), cancel_event=None, podcast_row=podcast_row)

        assert result is True
        audio_segments = local_ap.process_episode.call_args.args[1]
        saved_markers = storage.save_combined_ads.call_args.args[2]
        if render_calls is not None:
            render_calls.append(local_ap.process_episode.call_args)

    return audio_segments, saved_markers


def _recut_render_call(tmp_path, markers, confirmed):
    """Drive a real _recut_episode with ffmpeg mocked; return the render call."""
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_finalize_episode')
        p(processing, '_generate_assets')
        p(processing, '_copy_retained_original_to_temp', return_value=str(tmp_path / 'w.mp3'))
        p(processing, 'get_min_cut_confidence', return_value=0.80)
        p(processing.os.path, 'exists', return_value=False)
        p(processing.shutil, 'move')
        local_ap = p(processing, 'AudioProcessor').return_value
        local_ap.get_audio_duration.return_value = 600.0
        local_ap.process_episode.side_effect = (
            lambda path, segs, cut_barriers=None, hard_barriers=None: (
                str(tmp_path / 'cut.mp3'), [{'start': s['start'], 'end': s['end']} for s in segs]))
        db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 1,
                                       'ad_markers_json': json.dumps(markers)}
        db.get_podcast_by_slug.return_value = {'id': 1}
        db.get_original_segments.return_value = [
            {'start': float(t), 'end': float(t + 5), 'text': 'Show talk'} for t in range(0, 600, 5)]
        db.get_all_settings.return_value = {}
        db.get_setting.return_value = None
        db.get_episode_corrections.return_value = []
        db.get_false_positive_corrections.return_value = []
        db.get_confirmed_corrections.return_value = confirmed
        db.get_podcast_cue_settings_overrides.return_value = {}
        db.get_episode_audio_analysis.return_value = None
        db.get_episode_dai_differential.return_value = None
        db.resolve_segment_actions.return_value = {}
        storage.get_applied_cuts.return_value = []
        storage.get_episode_path.return_value = str(tmp_path / 'final.mp3')

        assert processing._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time())
    return local_ap.process_episode.call_args

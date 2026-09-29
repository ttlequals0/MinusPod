"""Full pass-1 pipeline driver with every stage but the partition mocked; import after bootstrap."""
from contextlib import ExitStack
from unittest.mock import patch

import main_app.processing as processing

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _run_pipeline(first_pass_ads, segment_actions, late_synthesized_ad=None,
                  real_sweeps=False, audio_analysis_result=None, segments=None,
                  verification_return=None, held_categories=None,
                  reviewer_side_effect=None, render_fails=False,
                  verification_side_effect=None, real_refine_reviewer=False,
                  confirmed_corrections=None, duration=100.0,
                  false_positive_corrections=None, render=None, new_duration=None,
                  assets_side_effect=None, episode_row=None, finalize_side_effect=None):
    """Drive process_episode's pass-1 flow with the stages around the keep partition mocked.

    real_refine_reviewer keeps validation and review live; late_synthesized_ad is appended
    after the keep partition; held_categories are held instead of cut by the fake validator.
    """
    podcast_row = {'id': 1, 'slug': 'keep-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'detection_mode': None}
    segments = SEGMENTS if segments is None else segments

    held = set(held_categories or ())

    def _fake_refine_and_validate(slug, episode_id, all_ads, *a, **k):
        for ad in all_ads:
            if segment_actions.get(ad.get('category')) == 'keep':
                raise AssertionError(
                    'validator was called with a keep-action marker')
        for ad in all_ads:
            ad['was_cut'] = ad.get('category') not in held
            if not ad['was_cut']:
                ad['held_for_review'] = True
                ad.setdefault('hold_reason', 'max_duration')
        result = list(all_ads)
        if late_synthesized_ad is not None:
            late_synthesized_ad['was_cut'] = True
            result = result + [late_synthesized_ad]
        return [ad for ad in result if ad['was_cut']], result

    def _fake_run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                              all_ads_with_validation, *a, **k):
        if reviewer_side_effect:
            return reviewer_side_effect(ads_to_remove, all_ads_with_validation)
        return ads_to_remove, all_ads_with_validation

    def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
        return ads_to_remove

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing.ad_detector, 'get_model', return_value='test-model')
        p(processing.ad_detector, 'get_verification_model', return_value='test-model')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/keep.mp3', segments))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=audio_analysis_result)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass',
                  return_value=(first_pass_ads, len(first_pass_ads), {}))
        refine = p(processing, '_refine_and_validate',
                  side_effect=(processing._refine_and_validate
                               if real_refine_reviewer else _fake_refine_and_validate))
        reviewer = p(processing, '_run_ad_reviewer',
                     side_effect=(processing._run_ad_reviewer
                                  if real_refine_reviewer and reviewer_side_effect is None
                                  else _fake_run_ad_reviewer))
        if real_refine_reviewer:
            p(processing, '_apply_heuristic_rolls')
        if not real_sweeps:
            p(processing, '_snap_terminal_starts', side_effect=_pass_through_ads)
            p(processing, '_complete_cut_tails', side_effect=_pass_through_ads)
        local_ap_cls = p(processing, 'AudioProcessor')
        if verification_side_effect:
            p(processing, '_run_verification_pass',
              side_effect=verification_side_effect)
        else:
            p(processing, '_run_verification_pass',
              return_value=(verification_return
                            or (0, [], [], [], '/tmp/cut.mp3', 0, True, 0)))
        generate_assets = p(processing, '_generate_assets',
                            side_effect=assets_side_effect)
        finalize = p(processing, '_finalize_episode', side_effect=finalize_side_effect)
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = episode_row or {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_podcast_cue_settings_overrides.return_value = {}
        db.get_setting.side_effect = lambda key: (
            'true' if real_refine_reviewer and key == 'enable_ad_review'
            else 'false')
        db.get_setting_bool.return_value = False
        db.get_false_positive_corrections.return_value = (
            false_positive_corrections or [])
        db.get_confirmed_corrections.return_value = confirmed_corrections or []
        db.get_setting_float.side_effect = lambda key, default=None: default
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = segment_actions
        audio_processor.get_audio_duration.return_value = duration
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.side_effect = (
            lambda audio_path, ads_to_remove, cut_barriers=None, hard_barriers=None:
            None if render_fails else (
                '/tmp/cut.mp3', (render or list)(ads_to_remove)))
        local_ap.get_audio_duration.return_value = new_duration or duration
        storage.get_episode_path.return_value = '/tmp/final.mp3'

        result = processing.process_episode(
            'keep-feed', 'ep1', 'https://example.com/ep1.mp3')

    return {'result': result, 'db': db, 'storage': storage,
            'detect': detect, 'refine': refine, 'reviewer': reviewer,
            'generate_assets': generate_assets, 'finalize': finalize,
            'local_ap': local_ap}

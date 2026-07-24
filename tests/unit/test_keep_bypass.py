"""Tests for the segment-category keep-action bypass in pass 1.

A marker whose resolved segment-category action is 'keep' must be pulled
out before the validator and reviewer ever see it: it is persisted with
was_cut=False/action_applied='keep' but never enters the validator input,
the reviewer input, the cut list, or held-for-review routing. Markers whose
action resolves to 'remove' (the default) flow through exactly as before
Task 4 -- this is the byte-identical regression case.
"""
import os
import sys
import tempfile
from contextlib import ExitStack

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='keepbypass_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch

import main_app.processing as processing

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _sponsor_ad():
    return {'start': 10.0, 'end': 20.0, 'category': 'sponsor',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _cross_promo_ad():
    return {'start': 30.0, 'end': 40.0, 'category': 'cross_promo',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _run_pipeline(first_pass_ads, segment_actions):
    """Drive process_episode's full pass-1 flow with every stage but the
    partition itself mocked out. Returns the recorded mocks so tests can
    inspect what each stage was actually called with.
    """
    podcast_row = {'id': 1, 'slug': 'keep-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'detection_mode': None}

    def _fake_refine_and_validate(slug, episode_id, all_ads, *a, **k):
        for ad in all_ads:
            if segment_actions.get(ad.get('category')) == 'keep':
                raise AssertionError(
                    'validator was called with a keep-action marker')
        for ad in all_ads:
            ad['was_cut'] = True
        return list(all_ads), list(all_ads)

    def _fake_run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                              all_ads_with_validation, *a, **k):
        return ads_to_remove, all_ads_with_validation

    def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
        return ads_to_remove

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/keep.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass',
                  return_value=(first_pass_ads, len(first_pass_ads), {}))
        refine = p(processing, '_refine_and_validate',
                  side_effect=_fake_refine_and_validate)
        reviewer = p(processing, '_run_ad_reviewer',
                    side_effect=_fake_run_ad_reviewer)
        p(processing, '_snap_terminal_starts', side_effect=_pass_through_ads)
        p(processing, '_complete_cut_tails', side_effect=_pass_through_ads)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing, '_run_verification_pass',
          return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
        generate_assets = p(processing, '_generate_assets')
        finalize = p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = segment_actions
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.side_effect = (
            lambda audio_path, ads_to_remove: ('/tmp/cut.mp3', list(ads_to_remove)))
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'

        result = processing.process_episode(
            'keep-feed', 'ep1', 'https://example.com/ep1.mp3')

    return {'result': result, 'db': db, 'storage': storage,
            'detect': detect, 'refine': refine, 'reviewer': reviewer,
            'generate_assets': generate_assets, 'finalize': finalize}


class TestKeepBypass:
    def test_keep_marker_bypasses_validator_and_reviewer(self):
        sponsor = _sponsor_ad()
        cross_promo = _cross_promo_ad()
        segment_actions = {'sponsor': 'remove', 'cross_promo': 'keep',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}

        m = _run_pipeline([sponsor, cross_promo], segment_actions)

        assert m['result'] is True
        m['db'].resolve_segment_actions.assert_called_once_with(
            'keep-feed', podcast=m['db'].get_podcast_by_slug.return_value)

        # Validator/reviewer only ever saw the sponsor marker.
        refine_all_ads = m['refine'].call_args.args[2]
        assert refine_all_ads == [sponsor]
        reviewer_all_ads = m['reviewer'].call_args.args[4]
        assert cross_promo not in reviewer_all_ads

        # Final saved markers include both, correctly stamped.
        saved = m['storage'].save_combined_ads.call_args.args[2]
        by_span = {(a['start'], a['end']): a for a in saved}
        keep_marker = by_span[(cross_promo['start'], cross_promo['end'])]
        assert keep_marker['was_cut'] is False
        assert keep_marker['action_applied'] == 'keep'
        sponsor_marker = by_span[(sponsor['start'], sponsor['end'])]
        assert sponsor_marker['was_cut'] is True
        assert 'action_applied' not in sponsor_marker

    def test_all_remove_is_byte_identical(self):
        sponsor = _sponsor_ad()
        cross_promo = _cross_promo_ad()
        segment_actions = {'sponsor': 'remove', 'cross_promo': 'remove',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        first_pass_ads = [sponsor, cross_promo]

        m = _run_pipeline(first_pass_ads, segment_actions)

        assert m['result'] is True
        # The validator receives every marker, untouched, in original order.
        refine_all_ads = m['refine'].call_args.args[2]
        assert refine_all_ads == first_pass_ads
        assert [id(a) for a in refine_all_ads] == [id(a) for a in first_pass_ads]

        # No action_applied key anywhere; the keep-fold path never ran, so
        # storage.save_combined_ads is never called by processing.py itself
        # (only by the mocked-out stage functions, which record no calls).
        m['storage'].save_combined_ads.assert_not_called()
        for ad in first_pass_ads:
            assert 'action_applied' not in ad

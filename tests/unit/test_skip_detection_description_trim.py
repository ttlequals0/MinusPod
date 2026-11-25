"""Skip detection feeds still apply the description chapter trim rule on the original audio."""
import os
import sys
import tempfile
from contextlib import ExitStack

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='skipdet_trim_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch

import main_app.processing as processing
from main_app.pipeline import common, transcribe, signals, review, publish
from main_app.pipeline import finalize as finalize_stage
from main_app.pipeline import verify as verify_stage
from main_app.pipeline import detect as detect_stage
from main_app.pipeline import recut

SEGMENTS = [{'start': float(t), 'end': float(t + 5), 'text': 'hello there'}
            for t in range(0, 3000, 5)]
DESCRIPTION = "00:01 Introduction\n39:11 Donations\n45:26 Next topic"
DURATION = 3000.0


def _run(pattern='*Donation*', mode='to_next', description=DESCRIPTION, actions=None):
    podcast_row = {'id': 1, 'slug': 'example-podcast', 'description': None,
                   'tags': None, 'dai_platform': None, 'passthrough_enabled': None,
                   'skip_ad_detection': 1, 'description_trim_pattern': pattern,
                   'description_trim_mode': mode}
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
        p(finalize_stage, 'get_available_memory_gb', return_value=None)
        p(common, 'get_min_cut_confidence', return_value=0.8)
        p(transcribe, '_download_and_transcribe', return_value=('/tmp/skip.mp3', SEGMENTS))
        detection = [
            p(signals, '_run_differential_fetch', return_value=None),
            p(signals, '_run_audio_analysis', return_value=None),
            p(detect_stage, '_detect_ads_first_pass', return_value=([], 0, None)),
            p(detect_stage, '_apply_heuristic_rolls'),
            p(review, '_run_ad_reviewer', return_value=([], [])),
        ]
        p(processing, 'load_positional_prior', return_value=None)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(recut, 'AudioProcessor', new=local_ap_cls)
        p(verify_stage, '_run_verification_pass',
          return_value=(0, [], [], [], '/tmp/cut.mp3', 0, False, 0))
        generate_assets = p(publish, '_generate_assets')
        p(finalize_stage, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_podcast_row.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_setting_bool.return_value = False
        db.get_setting_float.side_effect = lambda key, default=None: default
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = actions or {}
        audio_processor.get_audio_duration.return_value = DURATION
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.side_effect = lambda path, segs, **kw: (
            '/tmp/cut.mp3', [dict(seg) for seg in segs])
        local_ap.get_audio_duration.return_value = DURATION
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        result = processing.process_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'https://example.com/ep.mp3',
            podcast_name='The Daily Tech Show', episode_description=description)
    cuts = [(c['start'], c['end']) for c in local_ap.process_episode.call_args.args[1]]
    return {'result': result, 'cuts': cuts, 'detection': detection,
            'assets': generate_assets}


class TestSkipDetectionDescriptionTrim:
    def test_pattern_cuts_to_next_chapter(self):
        m = _run()
        assert m['result'] is True
        assert m['cuts'] == [(2351.0, 2726.0)]
        for fn in m['detection']:
            fn.assert_not_called()

    def test_markers_reach_assets(self):
        m = _run()
        markers = m['assets'].call_args.kwargs['markers']
        assert [(x['start'], x['end'], x['was_cut']) for x in markers] == [
            (2351.0, 2726.0, True)]

    def test_no_pattern_no_cut(self):
        assert _run(pattern=None)['cuts'] == []

    def test_no_timestamps_no_cut(self):
        assert _run(description='Just a plain description.')['cuts'] == []

    def test_no_match_no_cut(self):
        assert _run(pattern='*Sponsors*')['cuts'] == []

    def test_keep_action_for_outro_not_cut(self):
        m = _run(actions={'outro': 'keep'})
        assert m['cuts'] == []
        markers = m['assets'].call_args.kwargs['markers']
        assert [x['was_cut'] for x in markers] == [False]


class TestNoDuplicateChapterTrigger:
    def test_generated_chapter_does_not_requeue(self):
        m = _run()
        markers = list(m['assets'].call_args.kwargs['markers'])
        chapters = [{'startTime': 2351.0, 'title': 'Donations'},
                    {'startTime': 2726.0, 'title': 'Next topic'}]
        queued = publish._queue_chapter_trim(
            'example-podcast', 'a1b2c3d4e5f6', chapters,
            {'description_trim_pattern': '*Donation*', 'description_trim_mode': 'to_next'},
            [], 0.0, None, DURATION, markers, 'generated', original_timeline=True)
        assert queued is False
        assert len(markers) == 1

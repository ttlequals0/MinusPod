"""Cue snap leaves kept markers alone and reaches pass-2 cuts."""
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('cue_snap_kept_pass2_test_')

from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal
from config import is_cue_backed
from main_app import processing
from main_app.pipeline import detect
from tests.unit.pass2_test_utils import _spans, drive_verification_pass

ACTIONS = {'sponsor': 'remove', 'recap': 'keep'}


def _jingle(start, end):
    """A removable ad-break boundary template cue."""
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=0.95,
        details={'source': 'template', 'label': 'break jingle', 'role': 'boundary',
                 'cue_type': 'ad_break_boundary', 'template_id': 7,
                 'remove_with_ad': True})


def _audio(*cues):
    result = AudioAnalysisResult()
    result.signals = list(cues)
    return result


def _first_pass(ads, audio, action_map):
    ctx = MagicMock(slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=None)
    with patch.object(processing.db, 'get_setting_float',
                      side_effect=lambda key, default=None: default), \
         patch.object(processing.db, 'get_setting_bool', return_value=False), \
         patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value={'status': 'success', 'ads': ads}):
        found, _count, _result = detect._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3', skip_patterns=[],
            audio_analysis_result=audio, progress_callback=None,
            action_map=action_map)
    return found


class TestFirstPassSnapSkipsKeptMarkers:
    def test_keep_category_marker_does_not_swallow_the_jingle(self):
        recap = {'start': 1399.7, 'end': 1405.9, 'category': 'recap', 'confidence': 0.9}
        found = _first_pass([recap], _audio(_jingle(1409.41, 1411.37)), ACTIONS)
        assert _spans(found) == [(1399.7, 1405.9)]
        assert 'cue_snap' not in found[0]

    def test_remove_category_marker_still_snaps(self):
        sponsor = {'start': 1399.7, 'end': 1405.9, 'category': 'sponsor', 'confidence': 0.9}
        found = _first_pass([sponsor], _audio(_jingle(1409.41, 1411.37)), ACTIONS)
        assert found[0]['end'] == 1411.42
        assert found[0]['cue_snap']['end']['remove_with_ad'] is True

    def test_same_category_snaps_when_the_feed_removes_it(self):
        recap = {'start': 1399.7, 'end': 1405.9, 'category': 'recap', 'confidence': 0.9}
        found = _first_pass([recap], _audio(_jingle(1409.41, 1411.37)),
                            {**ACTIONS, 'recap': 'remove'})
        assert found[0]['end'] == 1411.42


JINGLES = _audio(_jingle(1409.41, 1411.37), _jingle(1432.01, 1433.97))
RECAP_KEPT = {'start': 1399.7, 'end': 1405.9, 'action_applied': 'keep', 'category': 'recap'}


class TestPass2CutsSnapToCues:
    def test_pass2_end_moves_onto_the_trailing_jingle(self):
        run = drive_verification_pass([(1409.4, 1432.0)], kept=[RECAP_KEPT],
                                      segment_actions=ACTIONS,
                                      audio_analysis_result=JINGLES)
        assert _spans(run.rendered['requested']) == [(1409.36, 1434.02)]
        assert _spans(run.output[1]) == [(1409.36, 1434.02)]
        assert run.output[1][0]['pass2_cue_snap']['end']['cue_end'] == 1433.97

    def test_snapped_edge_never_extends_over_a_kept_marker(self):
        # The kept marker covers the leading jingle, so only the trailing edge may grow.
        kept = dict(RECAP_KEPT, end=1411.42)
        run = drive_verification_pass([(1409.4, 1432.0)], kept=[kept],
                                      segment_actions=ACTIONS,
                                      audio_analysis_result=JINGLES)
        requested = _spans(run.rendered['requested'])
        assert requested == [(1411.42, 1434.02)]
        assert all(start >= kept['end'] for start, _end in requested)

    def test_extension_across_a_pass1_cut_is_skipped(self):
        cuts = [{'start': 1432.5, 'end': 1500.0}]
        run = drive_verification_pass([(1300.0, 1432.0)], cuts=cuts,
                                      segment_actions=ACTIONS,
                                      audio_analysis_result=_audio(_jingle(1432.01, 1433.97)))
        assert _spans(run.output[1]) == [(1300.0, 1432.0)]

    def test_cue_gate_still_holds_every_pass2_finding(self):
        def cue_gated_validate(*args, **kwargs):
            # The validator's cue gate, run on what validation sees.
            for ad in args[2]:
                if not is_cue_backed(ad):
                    ad['held_for_review'] = True
            return list(args[2]), list(args[3])

        run = drive_verification_pass([(1409.4, 1432.0)], segment_actions=ACTIONS,
                                      cue_gate_enabled=True, validate=cue_gated_validate,
                                      audio_analysis_result=JINGLES)
        assert run.output[1] == []
        assert _spans(run.output[3]) == [(1409.4, 1432.0)]
        assert 'pass2_cue_snap' not in run.output[3][0]

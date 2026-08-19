"""An incomplete verification pass (no_segments, transcription_failed,
detection_failed) must not report a clean scan."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('verify_status_test_')

import ad_detector
from main_app import processing
from verification_pass import VerificationPass


def _ctx():
    return SimpleNamespace(
        slug='verify-status-feed', episode_id='ep1', podcast_id=1,
        podcast_name='Test Podcast', episode_title='Episode 1',
        episode_description=None, podcast_description=None,
    )


def _run(verification_result):
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        db.get_setting_float.return_value = 0.6

        verifier_cls = stack.enter_context(
            patch('verification_pass.VerificationPass'))
        verifier_cls.return_value.verify.return_value = verification_result

        result = processing._run_verification_pass(
            _ctx(), '/tmp/verify-status-cut.mp3', [], False, 0.8,
            MagicMock(), None,
        )
    return result, storage


class TestAbortedVerificationStatus:
    def test_transcription_failed_status_is_not_reported_clean(self, caplog):
        result, storage = _run({
            'ads': [], 'ads_processed': [], 'segments': [],
            'status': 'transcription_failed',
        })

        verification_ok = result[6]
        assert verification_ok is False
        assert 'Verification: clean' not in caplog.text
        storage.save_ads_json.assert_called_once()

    def test_no_segments_status_is_not_reported_clean(self, caplog):
        result, storage = _run({
            'ads': [], 'ads_processed': [], 'segments': [],
            'status': 'no_segments',
        })

        verification_ok = result[6]
        assert verification_ok is False
        assert 'Verification: clean' not in caplog.text
        storage.save_ads_json.assert_called_once()

    def test_genuine_clean_pass_without_status_key_still_reports_clean(self, caplog):
        caplog.set_level('INFO')
        result, storage = _run({
            'ads': [], 'ads_processed': [], 'segments': [],
        })

        verification_ok = result[6]
        assert verification_ok is True
        assert 'Verification: clean' in caplog.text
        storage.save_ads_json.assert_called_once()


def _verify_with_detection(detection_result):
    """Run VerificationPass.verify against a stubbed detector response."""
    detector = MagicMock()
    detector.run_verification_detection.return_value = detection_result
    analyzer = MagicMock()
    analysis = MagicMock()
    analysis.signals = []
    analysis.get_signals_by_type.return_value = []
    analyzer.analyze.return_value = analysis
    verifier = VerificationPass(ad_detector=detector, transcriber=MagicMock(),
                                audio_analyzer=analyzer)
    return verifier.verify(
        processed_audio_path='/nonexistent.mp3', podcast_name='Test Podcast',
        episode_title='Episode 1', slug='verify-status-feed', episode_id='ep1',
        original_segments=[{'start': 0.0, 'end': 10.0, 'text': 'hello'}],
    )


class TestFailedDetectionVerification:
    def test_partial_window_failure_envelope_is_not_clean(self):
        envelope = ad_detector._windows_failed_response(
            'verification', 2, 6, RuntimeError('boom'), 'model-x')
        result = _verify_with_detection(envelope)

        assert result['status'] == 'detection_failed'
        assert result['ads'] == []
        assert result['ads_processed'] == []
        assert '2/6 verification windows failed' in result['error']

    def test_all_windows_failed_envelope_is_not_clean(self):
        envelope = ad_detector._windows_failed_response(
            'verification', 6, 6, RuntimeError('boom'), 'model-x')
        result = _verify_with_detection(envelope)

        assert result['status'] == 'detection_failed'
        assert 'All 6 verification windows failed' in result['error']

    def test_detection_failed_status_is_not_reported_clean(self, caplog):
        envelope = ad_detector._windows_failed_response(
            'verification', 2, 6, RuntimeError('boom'), 'model-x')
        result, storage = _run(_verify_with_detection(envelope))

        count, v_ads_for_ui, v_cuts, v_held = result[:4]
        assert result[6] is False
        assert 'Verification: clean' not in caplog.text
        # Pass-1 output untouched: nothing cut, held, or handed to the UI.
        assert (count, v_ads_for_ui, v_cuts, v_held) == (0, [], [], [])
        assert result[4] == '/tmp/verify-status-cut.mp3'
        storage.save_ads_json.assert_called_once()

    def test_all_windows_failed_flows_through_as_incomplete(self, caplog):
        envelope = ad_detector._windows_failed_response(
            'verification', 6, 6, RuntimeError('boom'), 'model-x')
        result, storage = _run(_verify_with_detection(envelope))

        assert result[6] is False
        assert 'Verification: clean' not in caplog.text
        assert 'Verification incomplete (detection_failed' in caplog.text
        storage.save_ads_json.assert_called_once()

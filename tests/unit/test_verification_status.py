"""An incomplete verification pass (no_segments/transcription_failed) must not report a clean scan."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('verify_status_test_')

from main_app import processing


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

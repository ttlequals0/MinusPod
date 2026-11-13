"""Detection and verification record addressing samples through one helper."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('addressing_sample_test_')

from ad_detector import AdDetector, AddressingStats


def _detector():
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    return detector


def test_judged_windows_are_logged_with_the_stage():
    detector = _detector()
    stats = AddressingStats(windows_judged=3, windows_compliant=2, ads_proposed=5,
                            ads_kept=4, dropped_invalid_ref=1, dropped_out_of_window=0,
                            dropped_too_long=0)
    detector._record_addressing_sample('example-podcast', 'a1b2c3d4e5f6', 'verification',
                                       'auto', 'segment_ids', stats)
    detector.db.record_addressing_log.assert_called_once_with(
        'example-podcast', 'a1b2c3d4e5f6', 'verification', 'auto', 'segment_ids', 3, 2,
        ads_proposed=5, ads_kept=4, ads_dropped_invalid_ref=1,
        ads_dropped_out_of_window=0, ads_dropped_too_long=0)


def test_nothing_is_logged_when_no_window_was_judged():
    detector = _detector()
    detector._record_addressing_sample('example-podcast', 'a1b2c3d4e5f6', 'detection',
                                       'auto', 'segment_ids', AddressingStats())
    detector.db.record_addressing_log.assert_not_called()


def test_a_failing_write_never_fails_the_pass():
    detector = _detector()
    detector.db.record_addressing_log.side_effect = RuntimeError('db down')
    detector._record_addressing_sample('example-podcast', 'a1b2c3d4e5f6', 'detection',
                                       'auto', 'timestamps', AddressingStats(windows_judged=1))

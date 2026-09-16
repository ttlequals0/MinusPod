"""Per-pass window coverage names what the lost windows were lost to."""
import unittest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('run_window_stats_')
from main_app.processing import _window_stats  # noqa: E402
from utils.llm_call import LOSS_RATE_LIMIT, LOSS_SERVER_ERROR  # noqa: E402


class TestWindowStats(unittest.TestCase):
    def test_coverage_only_when_the_pass_reported_no_classes(self):
        self.assertEqual(_window_stats(10, 2), {'total': 10, 'failed': 2})

    def test_a_clean_pass_carries_no_failure_classes(self):
        self.assertEqual(_window_stats(10, 0, {}), {'total': 10, 'failed': 0})

    def test_the_passs_own_tally_is_reported(self):
        self.assertEqual(
            _window_stats(20, 3, {LOSS_SERVER_ERROR: 2, LOSS_RATE_LIMIT: 1}),
            {'total': 20, 'failed': 3,
             'failureClasses': {LOSS_SERVER_ERROR: 2, LOSS_RATE_LIMIT: 1}})


if __name__ == '__main__':
    unittest.main()

"""incompleteCoverage carries the per-class window loss tally."""
from tests.app_bootstrap import bootstrap

bootstrap('incomplete_coverage_test_')

from episode_runs import incomplete_coverage


def _run(stats):
    return {'status': 'completed', 'stats': stats}


def test_failure_classes_pass_through():
    runs = [_run({'windows': {'total': 24, 'failed': 3,
                              'failureClasses': {'rate_limit': 2, 'server_error': 1}}})]
    assert incomplete_coverage(runs) == {
        'detection': {'failed': 3, 'total': 24,
                      'failureClasses': {'rate_limit': 2, 'server_error': 1}},
    }


def test_missing_classes_are_omitted_and_clean_run_is_none():
    runs = [_run({'windows': {'total': 24, 'failed': 1},
                  'verificationWindows': {'total': 20, 'failed': 0}})]
    assert incomplete_coverage(runs) == {'detection': {'failed': 1, 'total': 24}}
    assert incomplete_coverage([_run({'windows': {'total': 24, 'failed': 0}})]) is None

"""The suppressed-upstream-items line is INFO and logged only when the count changes."""
import logging

import pytest

import rss_parser


@pytest.fixture(autouse=True)
def _clean():
    rss_parser._suppressed_last_count.clear()
    yield
    rss_parser._suppressed_last_count.clear()


def _msgs(caplog):
    return [r for r in caplog.records if 'Suppressed' in r.getMessage()]


def test_logs_once_per_unchanged_count(caplog):
    with caplog.at_level(logging.INFO, logger=rss_parser.logger.name):
        rss_parser._log_suppressed_upstream('example-podcast', 3)
        rss_parser._log_suppressed_upstream('example-podcast', 3)
        rss_parser._log_suppressed_upstream('example-podcast', 5)
    msgs = _msgs(caplog)
    assert len(msgs) == 2
    assert all(r.levelno == logging.INFO for r in msgs)
    assert '[example-podcast] Suppressed 3 upstream item(s)' in msgs[0].getMessage()


def test_feeds_are_tracked_separately(caplog):
    with caplog.at_level(logging.INFO, logger=rss_parser.logger.name):
        rss_parser._log_suppressed_upstream('feed-one', 2)
        rss_parser._log_suppressed_upstream('feed-two', 2)
    assert len(_msgs(caplog)) == 2


def test_count_returning_after_zero_is_logged_again(caplog):
    with caplog.at_level(logging.INFO, logger=rss_parser.logger.name):
        rss_parser._log_suppressed_upstream('example-podcast', 3)
        rss_parser._log_suppressed_upstream('example-podcast', 0)
        rss_parser._log_suppressed_upstream('example-podcast', 3)
    assert len(_msgs(caplog)) == 2


def test_state_is_bounded(caplog):
    cap = rss_parser._SUPPRESSED_LAST_COUNT_MAX
    with caplog.at_level(logging.INFO, logger=rss_parser.logger.name):
        for i in range(cap * 2):
            rss_parser._log_suppressed_upstream(f'feed-{i}', 1)
    assert len(rss_parser._suppressed_last_count) <= cap

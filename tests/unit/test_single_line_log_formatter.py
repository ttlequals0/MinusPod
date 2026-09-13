"""A multi-line record splits into several Docker log lines, and the
continuation lines carry no level prefix, so a scraper re-sniffs their level
from the text and files a logged prompt body as CRITICAL.
"""
import logging

from tests.app_bootstrap import bootstrap

bootstrap('single_line_log_test_', passphrase='single-line-log-test-pass')

from main_app import SingleLineFormatter  # noqa: E402

FORMAT = '[%(levelname)s] [%(name)s] %(message)s'


def _record(msg, args=()):
    return logging.LogRecord('podcast.llm_io', logging.DEBUG, __file__, 1,
                             msg, args, None)


def test_multiline_message_becomes_one_line():
    out = SingleLineFormatter(FORMAT).format(
        _record('system prompt (12 chars):\nCRITICAL: every ad\nmust be real'))
    assert '\n' not in out
    assert '\\nCRITICAL: every ad\\nmust be real' in out


def test_single_line_message_is_untouched():
    out = SingleLineFormatter(FORMAT).format(_record('plain message'))
    assert out == '[DEBUG] [podcast.llm_io] plain message'


def test_carriage_returns_are_collapsed_too():
    out = SingleLineFormatter(FORMAT).format(_record('a\r\nb\rc'))
    assert '\r' not in out and '\n' not in out
    assert out.endswith('a\\nb\\nc')


def test_args_are_still_interpolated():
    out = SingleLineFormatter(FORMAT).format(_record('%s and\n%s', ('one', 'two')))
    assert out.endswith('one and\\ntwo')


def test_the_shared_record_is_not_mutated():
    """The per-episode run log formats the same record and wants real
    newlines, so formatting for the console must not rewrite it."""
    record = _record('head\ntail')
    SingleLineFormatter(FORMAT).format(record)
    assert record.getMessage() == 'head\ntail'
    assert logging.Formatter('%(message)s').format(record) == 'head\ntail'


def test_traceback_keeps_its_own_line_breaks():
    try:
        raise ValueError('boom')
    except ValueError:
        import sys
        record = logging.LogRecord('podcast', logging.ERROR, __file__, 1,
                                   'failed\nhere', (), sys.exc_info())
    out = SingleLineFormatter(FORMAT).format(record)
    assert 'failed\\nhere' in out
    assert 'Traceback (most recent call last):\n' in out

import json
import logging

from utils.log_format import (
    JSONFormatter, SingleLineFormatter, QueryStringRedactingFilter, make_formatter)


def _record(msg):
    return logging.LogRecord('podcast.audio', logging.INFO, __file__, 1, msg, (), None)


def test_make_formatter_selects_by_arg():
    assert isinstance(make_formatter('json'), JSONFormatter)
    assert isinstance(make_formatter('text'), SingleLineFormatter)


def test_make_formatter_reads_env_when_unset(monkeypatch):
    monkeypatch.setenv('LOG_FORMAT', 'JSON')
    assert isinstance(make_formatter(), JSONFormatter)
    monkeypatch.delenv('LOG_FORMAT')
    assert isinstance(make_formatter(), SingleLineFormatter)


def test_filter_scrubs_query_string_in_message():
    rec = _record("fetch failed https://cdn.example.com/a.mp3?key=secret123")
    assert QueryStringRedactingFilter().filter(rec) is True
    assert 'secret123' not in rec.getMessage()
    assert '?<redacted>' in rec.getMessage()


def test_filter_leaves_plain_lines_untouched():
    rec = _record("processing episode done")
    QueryStringRedactingFilter().filter(rec)
    assert rec.getMessage() == "processing episode done"


def test_filter_never_raises_on_mismatched_args():
    rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'value %d', ('x',), None)
    assert QueryStringRedactingFilter().filter(rec) is True


def _exception_record():
    try:
        raise RuntimeError(
            "403 Client Error: Forbidden for url: https://cdn.example.com/a.mp3?key=secret123")
    except RuntimeError:
        import sys
        return logging.LogRecord('x', logging.ERROR, __file__, 1, 'download failed', (),
                                 sys.exc_info())


def test_text_formatter_scrubs_traceback():
    out = make_formatter('text').format(_exception_record())
    assert 'secret123' not in out
    assert '?<redacted>' in out


def test_json_formatter_scrubs_traceback_and_stays_valid():
    out = make_formatter('json').format(_exception_record())
    assert 'secret123' not in out
    assert '?<redacted>' in out
    assert json.loads(out)['level'] == 'ERROR'

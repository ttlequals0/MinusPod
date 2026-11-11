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

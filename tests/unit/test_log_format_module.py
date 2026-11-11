import json
import logging

import pytest

from utils.http import redact_log_text
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


def test_scrubbing_formatter_keeps_newlines_and_scrubs_traceback():
    from utils.log_format import ScrubbingFormatter
    out = ScrubbingFormatter('%(message)s').format(_exception_record())
    assert 'secret123' not in out
    assert '\n' in out


FEED_KEY = "a" * 64


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_app_formatters_redact_bare_path_and_feed_key(fmt):
    rec = _record(f"GET /example-podcast?key=secret456 feed {FEED_KEY}")
    out = make_formatter(fmt).format(rec)
    assert "secret456" not in out
    assert FEED_KEY not in out
    assert "[redacted-feed-key]" in out
    assert "/example-podcast?<redacted>" in out


def test_filter_sets_redacted_exc_text_and_traceback_renders_once():
    rec = _exception_record()
    assert QueryStringRedactingFilter().filter(rec) is True
    assert "secret123" not in rec.exc_text
    out = logging.Formatter("%(message)s").format(rec)
    assert out.count("Traceback (most recent call last)") == 1
    assert "secret123" not in out


def test_filter_redacts_stack_info():
    rec = _record("x")
    rec.stack_info = "frame https://h/x?token=secret123"
    QueryStringRedactingFilter().filter(rec)
    assert "secret123" not in rec.stack_info


def test_redact_log_text_non_string_and_clean_text():
    assert redact_log_text(12345) == "12345"
    assert redact_log_text("nothing to redact here") == "nothing to redact here"


def test_filter_redacts_a_raw_cached_exc_text():
    rec = _exception_record()
    rec.exc_text = "Traceback (most recent call last):\nRuntimeError: https://h/x?token=secret123"
    QueryStringRedactingFilter().filter(rec)
    assert "secret123" not in rec.exc_text
    out = logging.Formatter("%(message)s").format(rec)
    assert "secret123" not in out
    assert out.count("Traceback (most recent call last)") == 1


def test_redact_log_text_survives_a_broken_stderr(monkeypatch):
    import sys
    import utils.http as http

    class BrokenStderr:
        def write(self, *_):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    def boom(_):
        raise RuntimeError("forced")

    monkeypatch.setattr(http, "_redaction_failure_reported", False)
    monkeypatch.setattr(http, "scrub_query_strings", boom)
    monkeypatch.setattr(sys, "stderr", BrokenStderr())
    assert http.redact_log_text("anything") == "[log text withheld: redaction failed]"


def test_filter_redacts_exc_text_without_exc_info():
    rec = _record("download failed")
    rec.exc_text = "Traceback (most recent call last):\nRuntimeError: https://u:pw@h/x?token=secret123"
    QueryStringRedactingFilter().filter(rec)
    assert rec.exc_text == "Traceback (most recent call last):\nRuntimeError: https://<redacted>@h/x?<redacted>"
    out = logging.Formatter("%(message)s").format(rec)
    assert "secret123" not in out and "pw" not in out


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_formatters_never_emit_exc_text_or_stack_info_raw(fmt):
    rec = _record("x")
    rec.exc_text = "RuntimeError: https://h/x?token=secret123"
    rec.stack_info = "Stack (most recent call last):\n  /f?key=secret456"
    out = make_formatter(fmt).format(rec)
    assert "secret123" not in out and "secret456" not in out


def test_text_formatter_keeps_its_layout():
    rec = _record("GET HTTPS://u:p@h/x?k=v done")
    out = make_formatter('text').format(rec)
    assert out.endswith("] [INFO] [podcast.audio] GET HTTPS://<redacted>@h/x?<redacted> done")
    assert out.startswith("[")

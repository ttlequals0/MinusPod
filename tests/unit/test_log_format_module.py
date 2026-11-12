import io
import json
import logging
import sys
import threading

import gunicorn.config
import pytest

import utils.http as http
from run_log import _MESSAGE_FORMATTER
from tests.unit.test_gunicorn_conf import _load_conf
from utils.http import MAX_REDACT_CHARS, redact_log_text
from utils.log_format import (
    JSONFormatter, ScrubbingFormatter, SingleLineFormatter, QueryStringRedactingFilter, guard_handler_errors,
    install_exception_hooks, make_formatter)


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


_CONTINUED = 'url /f?k=a\nb&c=SECRETNL end'


def test_text_and_json_redact_a_continued_query_the_same_way():
    text = make_formatter('text').format(_record(_CONTINUED))
    message = json.loads(make_formatter('json').format(_record(_CONTINUED)))['message']
    assert 'SECRETNL' not in text and 'SECRETNL' not in message
    assert text.endswith('] ' + message)


def test_json_keeps_line_breaks_when_redaction_does_not_cross_them():
    out = json.loads(make_formatter('json').format(_record('a https://h/x?k=v \nnext line')))
    assert out['message'] == 'a https://h/x?<redacted> \nnext line'


def test_filter_and_run_log_formatter_redact_a_continued_query():
    rec = _record(_CONTINUED)
    QueryStringRedactingFilter().filter(rec)
    assert 'SECRETNL' not in logging.Formatter('%(message)s').format(rec)
    assert 'SECRETNL' not in ScrubbingFormatter('%(message)s').format(_record(_CONTINUED))


_KEY = 'ab' * 32
_KEPT = f'Downloading audio from: https://u:PASS@cdn.example.com/{_KEY}/a.mp3?token=SIGNED'


def _marked(msg, mark):
    rec = _record(msg)
    if mark is not None:
        rec.keep_url_query = mark
    return rec


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_marked_record_keeps_its_query_but_not_userinfo_or_key(fmt):
    out = make_formatter(fmt).format(_marked(_KEPT, True))
    assert 'token=SIGNED' in out
    assert 'PASS' not in out and _KEY not in out


@pytest.mark.parametrize("mark", [None, False, 'true', 1])
@pytest.mark.parametrize("fmt", ["text", "json"])
def test_only_a_true_mark_keeps_the_query(fmt, mark):
    out = make_formatter(fmt).format(_marked(_KEPT, mark))
    assert 'SIGNED' not in out and 'PASS' not in out and _KEY not in out


def test_filter_honors_the_mark_for_query_only():
    rec = _marked(_KEPT, True)
    QueryStringRedactingFilter().filter(rec)
    out = logging.Formatter('%(message)s').format(rec)
    assert 'token=SIGNED' in out and 'PASS' not in out and _KEY not in out
    plain = _marked(_KEPT, None)
    QueryStringRedactingFilter().filter(plain)
    assert 'SIGNED' not in plain.getMessage()


def test_run_log_formatter_honors_the_mark():
    assert 'token=SIGNED' in _MESSAGE_FORMATTER.format(_marked(_KEPT, True))
    assert 'SIGNED' not in _MESSAGE_FORMATTER.format(_marked(_KEPT, None))


def test_message_text_cannot_set_the_mark():
    out = make_formatter('json').format(_record(_KEPT + ' keep_url_query=True extra={"keep_url_query": True}'))
    assert 'SIGNED' not in out


def _raw_handle_error(self, record):
    print(f"Arguments: {record.args}", file=sys.stderr)


def _bad_format_stderr(monkeypatch, capsys, handler):
    monkeypatch.setattr(logging.Handler, 'handleError', _raw_handle_error)
    monkeypatch.setattr(logging, 'raiseExceptions', True)
    guard_handler_errors()
    log = logging.getLogger('podcast.handler_error_test')
    monkeypatch.setattr(log, 'propagate', False)
    log.addHandler(handler)
    try:
        log.warning('fetch %s %s', 'https://h/x?k=SECRET')  # noqa: PLE1206 the format error is the point
    finally:
        log.removeHandler(handler)
    return capsys.readouterr().err


def test_a_format_error_never_prints_the_raw_args(monkeypatch, capsys):
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(make_formatter('text'))
    handler.addFilter(QueryStringRedactingFilter())
    err = _bad_format_stderr(monkeypatch, capsys, handler)
    assert 'SECRET' not in err and 'Arguments' not in err
    assert 'TypeError' in err and 'podcast.handler_error_test' in err


def test_a_format_error_in_a_gunicorn_handler_never_prints_the_raw_args(monkeypatch, capsys):
    mod = _load_conf(monkeypatch, 1, None)
    cfg = gunicorn.config.Config()
    cfg.set('errorlog', '-')
    rl = mod.RedactingLogger(cfg)
    handler = next(h for h in rl.error_log.handlers if getattr(h, '_gunicorn', False))
    handler.setStream(io.StringIO())
    monkeypatch.setattr(logging.Handler, 'handleError', _raw_handle_error)
    mod.RedactingLogger(cfg)
    rl.error_log.warning('fetch %s %s', 'https://h/x?k=SECRET')
    err = capsys.readouterr().err
    assert 'SECRET' not in err and 'Arguments' not in err and 'TypeError' in err


def _capture_uncaught(monkeypatch):
    monkeypatch.setattr(threading, 'excepthook', threading.excepthook)
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    install_exception_hooks()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(make_formatter('text'))
    handler.addFilter(QueryStringRedactingFilter())
    log = logging.getLogger('podcast.uncaught')
    log.addHandler(handler)
    monkeypatch.setattr(log, 'propagate', False)
    return stream, lambda: log.removeHandler(handler)


def test_an_uncaught_thread_exception_is_logged_redacted(monkeypatch, capsys):
    stream, done = _capture_uncaught(monkeypatch)

    def boom():
        raise RuntimeError("403 for url: https://cdn.example.com/x.mp3?token=SECRET")

    worker = threading.Thread(target=boom, name='fetcher')
    worker.start()
    worker.join()
    done()
    out = stream.getvalue()
    assert 'SECRET' not in out and 'SECRET' not in capsys.readouterr().err
    assert 'fetcher' in out and 'RuntimeError' in out and '?<redacted>' in out


def test_an_uncaught_main_thread_exception_is_logged_redacted(monkeypatch, capsys):
    stream, done = _capture_uncaught(monkeypatch)
    try:
        raise RuntimeError("https://h/x?token=SECRET")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    done()
    assert 'SECRET' not in stream.getvalue() and 'SECRET' not in capsys.readouterr().err
    assert '?<redacted>' in stream.getvalue()


def _raised(exc):
    try:
        raise exc
    except Exception:
        return sys.exc_info()


def _logged_record(msg, exc, mark):
    rec = logging.LogRecord('podcast.audio', logging.INFO, __file__, 1, msg, (), _raised(exc))
    rec.keep_url_query = mark
    return rec


def _three_formats(rec, with_filter):
    outs = []
    for fmt in (make_formatter('text'), make_formatter('json'), _MESSAGE_FORMATTER):
        copy_rec = logging.makeLogRecord(rec.__dict__)
        if with_filter:
            QueryStringRedactingFilter().filter(copy_rec)
        outs.append(fmt.format(copy_rec))
    return outs


@pytest.mark.parametrize('with_filter', [False, True])
def test_a_marked_record_never_keeps_the_query_in_its_traceback(with_filter):
    rec = _logged_record('Downloading https://h/x?tok=VISIBLE',
                         RuntimeError('fail https://h/y?sig=SECRET2'), True)
    for out in _three_formats(rec, with_filter):
        assert 'SECRET2' not in out
        assert 'tok=VISIBLE' in out


@pytest.mark.parametrize('with_filter', [False, True])
def test_a_marked_record_never_keeps_the_query_in_its_stack(with_filter):
    rec = _record('Downloading https://h/x?tok=VISIBLE')
    rec.keep_url_query = True
    rec.stack_info = 'Stack (most recent call last):\n  frame https://h/y?sig=SECRET3'
    for out in _three_formats(rec, with_filter):
        assert 'SECRET3' not in out
        assert 'tok=VISIBLE' in out


@pytest.mark.parametrize('with_filter', [False, True])
def test_a_continued_query_in_exception_text_is_redacted(with_filter):
    rec = _logged_record('download failed', ValueError('https://h/x?a=1\nb&c=SECRET1'), None)
    for out in _three_formats(rec, with_filter):
        assert 'SECRET1' not in out


def test_json_traceback_keeps_its_line_breaks():
    rec = _logged_record('download failed', ValueError('https://h/x?a=1 end'), None)
    exception = json.loads(make_formatter('json').format(rec))['exception']
    assert exception.startswith('Traceback (most recent call last):\n')
    assert exception.endswith('ValueError: https://h/x?<redacted> end')


def test_a_redacted_break_is_flattened_and_the_rest_stay_real():
    assert redact_log_text('one\ntwo https://h/x?a=1\nb&c=S end\nthree') == (
        'one\ntwo https://h/x?<redacted> end\nthree')


def test_a_break_that_ends_a_redacted_query_is_kept():
    assert redact_log_text('get https://h/x?a=1\n  File "x"\r\nnext /f?k=v)\r\n') == (
        'get https://h/x?<redacted>\n  File "x"\r\nnext /f?<redacted>)\r\n')


_BIG = 60000


def _oversized_record():
    rec = _logged_record('w ' * (_BIG // 2), ValueError('e ' * (_BIG // 2)), None)
    rec.stack_info = 'Stack (most recent call last):\n' + 's ' * (_BIG // 2)
    return rec


@pytest.mark.parametrize('with_filter', [False, True])
def test_message_traceback_and_stack_share_one_cap(with_filter):
    text, json_out, run_log = _three_formats(_oversized_record(), with_filter)
    assert len(text) <= MAX_REDACT_CHARS and len(run_log) <= MAX_REDACT_CHARS
    assert text.rstrip().endswith('s s') and run_log.rstrip().endswith('s s') and 'chars elided' in text
    data = json.loads(json_out)
    assert len(data['message']) + len(data['exception']) <= MAX_REDACT_CHARS
    assert data['exception'].rstrip().endswith('e e')


def test_json_keeps_a_short_exception_whole_beside_a_huge_message():
    rec = _logged_record('w ' * _BIG, ValueError('short'), None)
    data = json.loads(make_formatter('json').format(rec))
    assert data['exception'] == redact_log_text(logging.Formatter().formatException(rec.exc_info))
    assert len(data['message']) + len(data['exception']) <= MAX_REDACT_CHARS


@pytest.mark.parametrize('message', ['done\n', 'done'])
def test_run_log_layout_matches_the_stdlib_formatter(message):
    rec = _logged_record(message, ValueError('plain'), None)
    rec.stack_info = 'Stack (most recent call last):\n  frame'
    expected = logging.Formatter('%(message)s').format(logging.makeLogRecord(rec.__dict__))
    assert ScrubbingFormatter('%(message)s').format(rec) == expected


def test_a_bad_format_string_leaves_the_redaction_notice_unused(monkeypatch, capsys):
    monkeypatch.setattr(http, '_redaction_failure_reported', False)
    rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'fetch %s %s', ('https://h/x?k=SECRET',), None)
    assert QueryStringRedactingFilter().filter(rec) is True
    assert 'log redaction failed' not in capsys.readouterr().err

    def boom(*_args, **_kwargs):
        raise RuntimeError('forced')

    monkeypatch.setattr('utils.log_format.redact_log_text', boom)
    assert QueryStringRedactingFilter().filter(_record('later')) is True
    assert 'log redaction failed: RuntimeError' in capsys.readouterr().err


_REDACTION_LINES = [
    'GET https://cdn.example.com/a.mp3?token=SECRET123&x=1 failed',
    'fetch https://user:pw@example.com/feed.xml?k=abc done',
    'key a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6 leaked',
    'plain line with nothing to hide',
    'multi\nline https://h.example/x?sig=ZZZ\nend',
    'x' * 40000,
]


@pytest.mark.parametrize('line', _REDACTION_LINES)
@pytest.mark.parametrize('fmt', [
    ScrubbingFormatter('%(message)s'),
    SingleLineFormatter('[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'),
    JSONFormatter(),
])
def test_marked_record_formats_like_an_unmarked_one(fmt, line):
    marked = _record(line)
    assert QueryStringRedactingFilter().filter(marked) is True
    assert getattr(marked, '_text_redacted', False) is True
    unmarked = _record(line)
    a, b = fmt.format(marked), fmt.format(unmarked)
    if isinstance(fmt, JSONFormatter):
        a, b = json.loads(a), json.loads(b)
        a.pop('timestamp'), b.pop('timestamp')
    elif '%(asctime)s' in fmt._fmt:
        a, b = a[a.index(']') + 1:], b[b.index(']') + 1:]
    assert a == b


def test_marked_record_message_is_not_redacted_again(monkeypatch):
    rec = _record('GET https://cdn.example.com/a.mp3?token=SECRET123 failed')
    QueryStringRedactingFilter().filter(rec)
    seen = []
    real = http.redact_log_text

    def spy(text, keep_query=False):
        seen.append(text)
        return real(text, keep_query)

    monkeypatch.setattr('utils.log_format.redact_log_text', spy)
    out = SingleLineFormatter('[%(levelname)s] [%(name)s] %(message)s').format(rec)
    assert 'SECRET123' not in out
    assert all('cdn.example.com' not in text for text in seen)

"""when_ready warns when multi-worker + in-memory rate-limit storage."""
import importlib.util
import io
import json
import logging
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import gunicorn.config
import pytest
import requests
from gunicorn.glogging import Logger

from utils import log_format
from utils.log_format import JSONFormatter, QueryStringRedactingFilter, SingleLineFormatter


def _load_conf(monkeypatch, workers, storage):
    monkeypatch.setenv("GUNICORN_WORKERS", str(workers))
    if storage is None:
        monkeypatch.delenv("RATE_LIMIT_STORAGE_URI", raising=False)
    else:
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI", storage)
    path = Path(__file__).resolve().parents[2] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("workers,storage,expect_warn", [
    (2, None, True),
    (2, "memory://", True),
    (1, None, False),
    (2, "redis://localhost:6379", False),
])
def test_when_ready_logs_rate_limit_note(monkeypatch, workers, storage, expect_warn):
    mod = _load_conf(monkeypatch, workers, storage)
    server = MagicMock()
    mod.when_ready(server)
    assert server.log.info.called is expect_warn
    assert not server.log.warning.called


def _error_formatters(monkeypatch, log_format):
    if log_format is None:
        monkeypatch.delenv("LOG_FORMAT", raising=False)
    else:
        monkeypatch.setenv("LOG_FORMAT", log_format)
    mod = _load_conf(monkeypatch, 1, None)
    cfg = gunicorn.config.Config()
    cfg.set("errorlog", "-")
    cfg.set("accesslog", "-")
    rl = mod.RedactingLogger(cfg)
    assert isinstance(rl, Logger)
    # Only gunicorn's own handlers; pytest attaches capture handlers to the same loggers.
    return ([h.formatter for h in rl.error_log.handlers if getattr(h, "_gunicorn", False)],
            [h.formatter for h in rl.access_log.handlers if getattr(h, "_gunicorn", False)])


def test_gunicorn_logs_use_json_formatter_under_json_format(monkeypatch):
    err, acc = _error_formatters(monkeypatch, "json")
    assert any(isinstance(f, JSONFormatter) for f in err)
    assert any(isinstance(f, JSONFormatter) for f in acc)


def test_gunicorn_access_log_line_is_json_under_json_format(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    mod = _load_conf(monkeypatch, 1, None)
    cfg = gunicorn.config.Config()
    cfg.set("accesslog", "-")
    rl = mod.RedactingLogger(cfg)
    stream = io.StringIO()
    for h in rl.access_log.handlers:
        if getattr(h, "_gunicorn", False):
            h.setStream(stream)
    rl.access_log.info("GET /health 200")
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "level" in line


@pytest.mark.parametrize("log_format", ["text", None])
def test_gunicorn_logs_keep_own_formatter_under_text(monkeypatch, log_format):
    err, acc = _error_formatters(monkeypatch, log_format)
    assert err and acc
    for f in err + acc:
        assert not isinstance(f, (JSONFormatter, SingleLineFormatter))


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_error_log_redacts_request_path_query(monkeypatch, log_format):
    monkeypatch.setenv("LOG_FORMAT", log_format)
    mod = _load_conf(monkeypatch, 1, None)
    cfg = gunicorn.config.Config()
    cfg.set("errorlog", "-")
    rl = mod.RedactingLogger(cfg)
    stream = io.StringIO()
    for h in rl.error_log.handlers:
        if getattr(h, "_gunicorn", False):
            h.setStream(stream)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        rl.error_log.exception("Error handling request %s %s", "GET", "/example-podcast?key=secret123")
    out = stream.getvalue()
    assert "secret123" not in out
    assert "/example-podcast" in out
    if log_format == "json":
        assert json.loads(out.strip().splitlines()[-1])["level"] == "ERROR"


def test_request_path_filter_covers_invalid_request_line():
    rec = logging.LogRecord("gunicorn.error", logging.WARNING, __file__, 1,
                            "Invalid request from ip=1.2.3.4: Invalid HTTP request line: 'GET /f?key=secret123 HTTP/1.1'",
                            (), None)
    assert QueryStringRedactingFilter().filter(rec) is True
    assert "secret123" not in rec.getMessage()
    assert "GET /f?<redacted> HTTP/1.1" in rec.getMessage()


def _captured_logger(monkeypatch, log_format, which):
    monkeypatch.setenv("LOG_FORMAT", log_format)
    mod = _load_conf(monkeypatch, 1, None)
    cfg = gunicorn.config.Config()
    cfg.set("errorlog", "-")
    cfg.set("accesslog", "-")
    rl = mod.RedactingLogger(cfg)
    stream = io.StringIO()
    log = getattr(rl, which)
    for h in log.handlers:
        if getattr(h, "_gunicorn", False):
            h.setStream(stream)
    return log, stream


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_error_log_redacts_message_and_traceback(monkeypatch, log_format):
    log, stream = _captured_logger(monkeypatch, log_format, "error_log")
    try:
        raise requests.HTTPError(
            "403 Client Error: Forbidden for url: https://up.example.com/x?token=secret123")
    except requests.HTTPError:
        log.exception("Error handling request %s", "/example-podcast?key=secret456")
    out = stream.getvalue()
    assert "secret123" not in out and "secret456" not in out
    assert "/example-podcast" in out
    assert "Traceback" in out
    if log_format == "json":
        assert json.loads(out.strip().splitlines()[-1])["level"] == "ERROR"


def test_text_access_log_redacts_referer_query(monkeypatch):
    log, stream = _captured_logger(monkeypatch, "text", "access_log")
    log.info('1.2.3.4 - - [t] "GET /x HTTP/1.1" 200 5 "https://ref.example.com/p?session=secret789" "ua"')
    assert "secret789" not in stream.getvalue()
    assert "ref.example.com/p" in stream.getvalue()


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_error_log_skips_only_the_query_pass_for_a_marked_record(monkeypatch, log_format):
    key = "ab" * 32
    log, stream = _captured_logger(monkeypatch, log_format, "error_log")
    log.error("fetch https://u:PASS@h/%s/a.mp3?token=SIGNED", key, extra={"keep_url_query": True})
    log.error("fetch https://h/a.mp3?token=PLAIN")
    out = stream.getvalue()
    assert "token=SIGNED" in out
    assert "PASS" not in out and key not in out and "PLAIN" not in out


def test_post_fork_routes_uncaught_exceptions_through_logging(monkeypatch):
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    mod = _load_conf(monkeypatch, 1, None)
    mod.post_fork(MagicMock(), MagicMock())
    assert threading.excepthook is log_format._log_uncaught_thread
    assert sys.excepthook is log_format._log_uncaught

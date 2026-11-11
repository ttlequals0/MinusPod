"""when_ready warns when multi-worker + in-memory rate-limit storage."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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
def test_when_ready_warning(monkeypatch, workers, storage, expect_warn):
    mod = _load_conf(monkeypatch, workers, storage)
    server = MagicMock()
    mod.when_ready(server)
    assert server.log.warning.called is expect_warn


def _error_formatters(monkeypatch, log_format):
    import gunicorn.config
    from gunicorn.glogging import Logger
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
    return ([h.formatter for h in rl.error_log.handlers],
            [h.formatter for h in rl.access_log.handlers])


def test_gunicorn_logs_use_json_formatter_under_json_format(monkeypatch):
    from utils.log_format import JSONFormatter
    err, acc = _error_formatters(monkeypatch, "json")
    assert any(isinstance(f, JSONFormatter) for f in err)
    assert any(isinstance(f, JSONFormatter) for f in acc)


@pytest.mark.parametrize("log_format", ["text", None])
def test_gunicorn_logs_keep_own_formatter_under_text(monkeypatch, log_format):
    from utils.log_format import JSONFormatter, SingleLineFormatter
    err, acc = _error_formatters(monkeypatch, log_format)
    for f in err + acc:
        assert not isinstance(f, (JSONFormatter, SingleLineFormatter))

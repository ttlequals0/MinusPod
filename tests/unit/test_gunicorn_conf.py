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

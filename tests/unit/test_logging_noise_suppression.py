"""Third-party transport loggers stay quiet at LOG_LEVEL=DEBUG.

Their DEBUG output is per-request connection chatter (connect_tcp.started,
close.complete) that buries real signal in Loki and, since 2.89.2, fills a
quarter of every episode run log. The suppression list matches logger names
exactly, so an SDK that renames its transport escapes it: openai 3.x moved
to httpx2/httpcore2 and went unsuppressed until 2.89.4. This test fails when
an installed transport is missing from the list.
"""

import importlib
import logging
import os
import tempfile

import pytest

# main_app builds a Storage at import time; point it somewhere writable
# before the import so this test does not depend on the deploy layout.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='logtest_'))

# Transports whose records must never reach DEBUG in production logs. Each
# entry is the top-level logger name the package actually logs under.
SUPPRESSED = (
    'openai', 'anthropic',
    'httpx', 'httpcore',
    'httpx2', 'httpcore2',
    'urllib3', 'asyncio', 'charset_normalizer', 'requests',
)


@pytest.fixture
def configured_logging(monkeypatch):
    """Re-run setup_logging() at DEBUG, then restore the previous levels."""
    import main_app

    saved = {name: logging.getLogger(name).level for name in SUPPRESSED}
    saved_root = logging.getLogger().level
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    monkeypatch.setattr(main_app, '_logging_configured', False)
    main_app.setup_logging()
    try:
        yield
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
        logging.getLogger().setLevel(saved_root)
        main_app._logging_configured = True


@pytest.mark.parametrize('name', SUPPRESSED)
def test_transport_logger_is_capped_at_warning(configured_logging, name):
    assert logging.getLogger(name).level == logging.WARNING, (
        f"{name} would emit DEBUG request dumps into Loki and run logs"
    )


def test_application_loggers_still_follow_log_level(configured_logging):
    assert logging.getLogger('podcast.audio').level == logging.DEBUG
    assert logging.getLogger('podcast.llm_io').level == logging.DEBUG


def test_every_installed_transport_is_listed():
    """An installed transport missing from SUPPRESSED is the 2.89.4 bug."""
    for name in ('httpx', 'httpcore', 'httpx2', 'httpcore2'):
        if importlib.util.find_spec(name) is None:
            continue
        assert name in SUPPRESSED, (
            f"{name} is installed but not suppressed; add it to the noisy "
            f"logger list in main_app.setup_logging()"
        )

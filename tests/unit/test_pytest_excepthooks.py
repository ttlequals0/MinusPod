"""Importing the app must leave pytest's uncaught-exception hooks in place for the session."""
import sys
import threading

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('pytest_excepthooks_test_')
import main_app  # noqa: F401 the import that installs the app hooks is the point
from utils import log_format


def test_app_import_at_collection_keeps_pytests_hooks():
    assert threading.excepthook is not log_format._log_uncaught_thread
    assert sys.excepthook is not log_format._log_uncaught


def test_app_client_keeps_pytests_hooks(app_client):
    assert threading.excepthook is not log_format._log_uncaught_thread
    assert sys.excepthook is not log_format._log_uncaught

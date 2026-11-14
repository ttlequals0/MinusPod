"""Importing the app must leave pytest's uncaught-exception hooks in place for the session."""
import functools
import sys
import threading

from _pytest import threadexception

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('pytest_excepthooks_test_')
import main_app  # noqa: F401 the import that installs the app hooks is the point


def _assert_pytests_hooks():
    hook = threading.excepthook
    assert isinstance(hook, functools.partial) and hook.func is threadexception.thread_exception_hook, hook
    # pytest installs no sys.excepthook, so the interpreter default must still be active.
    assert sys.excepthook is sys.__excepthook__


def test_app_import_at_collection_keeps_pytests_hooks():
    _assert_pytests_hooks()


def test_app_client_keeps_pytests_hooks(app_client):
    _assert_pytests_hooks()

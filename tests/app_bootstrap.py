"""Shared module-level app bootstrap for tests.

Many test modules import main_app (or other src modules that construct the
Database/Storage singletons at import time). Those singletons default to
/app/data, so each module must mint a temp data dir and rebind the defaults
BEFORE the first src import. Call bootstrap() at the very top of the test
module, before importing main_app or anything that instantiates Database()
or Storage():

    from tests.app_bootstrap import bootstrap

    _test_data_dir = bootstrap('my_test_')

Notes:
- Mutating __defaults__ on the shared classes leaks across the pytest
  session by design: every module using this pattern re-points the defaults
  at its own temp dir, and none restore. Database.__new__ ignores its
  data_dir argument, so patching __new__.__defaults__ is always harmless.
- reset_storage=True additionally clears the Storage singleton so the next
  Storage() call constructs one rooted at the new data dir. Off by default
  because modules that never re-instantiate Storage should keep whatever
  instance an earlier module's main_app import created.
"""
import atexit
import os
import shutil
import sys
import tempfile

_SRC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'src')
)


def bootstrap(prefix, secret_key='test-secret', passphrase=None,
              reset_storage=False):
    """Create a temp data dir and point the app singletons at it.

    Returns the created data dir path. Environment (SECRET_KEY, DATA_DIR,
    optionally MINUSPOD_MASTER_PASSPHRASE) is set before any src import so
    modules that read env at import time see the test values.
    """
    data_dir = tempfile.mkdtemp(prefix=prefix)
    os.environ.setdefault('SECRET_KEY', secret_key)
    os.environ['DATA_DIR'] = data_dir
    if passphrase is not None:
        os.environ['MINUSPOD_MASTER_PASSPHRASE'] = passphrase
    if _SRC_DIR not in sys.path:
        sys.path.insert(0, _SRC_DIR)

    # Imported here rather than at module top: the env vars above must be in
    # place before src modules load, and importing this helper must stay
    # side-effect free.
    import database
    import storage as storage_mod

    database.Database._instance = None
    database.Database.__init__.__defaults__ = (data_dir,)
    database.Database.__new__.__defaults__ = (data_dir,)
    if reset_storage:
        storage_mod.Storage._instance = None
    storage_mod.Storage.__init__.__defaults__ = (data_dir,)

    atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
    return data_dir

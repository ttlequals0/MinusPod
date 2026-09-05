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
- model_env sets OPENAI_MODEL so a seeded DB gets a model, mirroring the
  SECRET_KEY setdefault (first bootstrap() call wins). Pass model_env=None
  to opt out; the env var may persist from an earlier module regardless.
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
              reset_storage=False, model_env='test-model'):
    """Create a temp data dir and point the app singletons at it.

    Returns the created data dir path. Environment (SECRET_KEY, DATA_DIR,
    OPENAI_MODEL, optionally MINUSPOD_MASTER_PASSPHRASE) is set before any
    src import so modules that read env at import time see the test values.
    """
    data_dir = tempfile.mkdtemp(prefix=prefix)
    os.environ.setdefault('SECRET_KEY', secret_key)
    os.environ['DATA_DIR'] = data_dir
    if passphrase is not None:
        os.environ['MINUSPOD_MASTER_PASSPHRASE'] = passphrase
    if model_env is not None:
        os.environ.setdefault('OPENAI_MODEL', model_env)
    if _SRC_DIR not in sys.path:
        sys.path.insert(0, _SRC_DIR)

    # Imported here rather than at module top: the env vars above must be in
    # place before src modules load, and importing this helper must stay
    # side-effect free.
    import database
    import storage as storage_mod

    database.Database._instance = None
    if reset_storage:
        storage_mod.Storage._instance = None

    atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
    return data_dir


def ensure_model_configured(db, model='test-model'):
    """Ensure claude_model is set so model resolvers do not raise.

    No-op if already seeded from OPENAI_MODEL; call after DB construction
    for a path (e.g. temp_db) that may not have OPENAI_MODEL in its env.
    """
    if not db.get_setting('claude_model'):
        db.set_setting('claude_model', model, is_default=True)
    return model

"""Shared module-level app bootstrap for tests.

The Database/Storage singletons are built at import time and default to
/app/data, so each test module must mint a temp data dir and rebind the defaults
before the first src import. Call bootstrap() at the very top of the module,
before importing main_app or anything that instantiates Database() or Storage():

    from tests.app_bootstrap import bootstrap

    _test_data_dir = bootstrap('my_test_')

Notes:
- Once main_app is imported, later module collection reuses its data directory.
  Resetting the Database singleton while imported modules retain the original
  database object splits one test across two database files.
- reset_storage=True also clears the Storage singleton so the next Storage() is
  rooted at the new data dir. Off by default so modules that never re-instantiate
  Storage keep the instance an earlier main_app import created.
- model_env sets OPENAI_MODEL so a seeded DB gets a model (first bootstrap() call
  wins, like SECRET_KEY). Pass model_env=None to opt out; the env var may persist
  from an earlier module regardless.
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
    """Create a temp data dir and point the app singletons at it; returns the path.

    Env (SECRET_KEY, DATA_DIR, OPENAI_MODEL, optionally MINUSPOD_MASTER_PASSPHRASE)
    is set before any src import so import-time env reads see the test values.
    """
    os.environ.setdefault('SECRET_KEY', secret_key)
    if passphrase is not None:
        os.environ['MINUSPOD_MASTER_PASSPHRASE'] = passphrase
    if model_env is not None:
        os.environ.setdefault('OPENAI_MODEL', model_env)
    if _SRC_DIR not in sys.path:
        sys.path.insert(0, _SRC_DIR)

    loaded_app = sys.modules.get('main_app')
    loaded_db = getattr(loaded_app, 'db', None) if loaded_app else None
    if loaded_db is not None:
        data_dir = str(loaded_db.data_dir)
        os.environ['DATA_DIR'] = data_dir
        return data_dir

    data_dir = tempfile.mkdtemp(prefix=prefix)
    os.environ['DATA_DIR'] = data_dir

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

    No-op if already seeded from OPENAI_MODEL; for paths (e.g. temp_db) without it in env."""
    if not db.get_setting('claude_model'):
        db.set_setting('claude_model', model, is_default=True)
    return model


def authenticate_test_client(client):
    """Authenticate a Flask test client at the current session generation."""
    from api import get_database
    from api.auth_state import SESSION_GENERATION_KEY, current_generation

    db = get_database()
    with client.session_transaction() as session:
        session['authenticated'] = True
        session[SESSION_GENERATION_KEY] = current_generation(db)
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}

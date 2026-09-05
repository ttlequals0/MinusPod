"""The data directory is resolved at call time, so test import order cannot pin it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
from utils.paths import DEFAULT_DATA_DIR, resolve_data_dir  # noqa: E402
import status_service  # noqa: E402


def test_resolve_reads_the_environment_on_every_call(monkeypatch, tmp_path):
    for var in ('DATA_DIR', 'DATA_PATH', 'MINUSPOD_DATA_DIR'):
        monkeypatch.delenv(var, raising=False)
    assert str(resolve_data_dir()) == DEFAULT_DATA_DIR

    monkeypatch.setenv('MINUSPOD_DATA_DIR', str(tmp_path / 'one'))
    assert resolve_data_dir() == tmp_path / 'one'
    # DATA_DIR wins, matching every other resolver in the codebase.
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'two'))
    assert resolve_data_dir() == tmp_path / 'two'


def test_a_database_built_with_no_data_dir_uses_the_configured_one(monkeypatch,
                                                                   tmp_path):
    """A bare Database() after the singleton is reset (what every test using
    the temp_db fixture leaves behind) must not fall back to the packaged
    default and try to create it."""
    monkeypatch.delenv('DATA_DIR', raising=False)
    monkeypatch.delenv('DATA_PATH', raising=False)
    monkeypatch.setenv('MINUSPOD_DATA_DIR', str(tmp_path / 'configured'))

    # Built through object.__new__ so the shared singleton is neither read
    # nor replaced, and with an explicit None because app_bootstrap rewrites
    # Database.__init__.__defaults__ for every module that calls it.
    db = object.__new__(Database)
    db._initialized = False
    db.__init__(None)

    assert db.data_dir == tmp_path / 'configured'
    assert db.data_dir.is_dir()


def test_status_file_path_matches_production_with_no_env_set(monkeypatch):
    for var in ('DATA_DIR', 'DATA_PATH', 'MINUSPOD_DATA_DIR'):
        monkeypatch.delenv(var, raising=False)
    assert status_service._status_file_path() == \
        os.path.join(DEFAULT_DATA_DIR, 'processing_status.json')


def test_status_file_path_honours_data_dir_set_after_import(monkeypatch, tmp_path):
    """The module is already imported; setting the env var afterwards must
    still be picked up, proving the path is not frozen at import time."""
    monkeypatch.delenv('DATA_DIR', raising=False)
    monkeypatch.delenv('DATA_PATH', raising=False)
    monkeypatch.setenv('MINUSPOD_DATA_DIR', str(tmp_path))
    assert status_service._status_file_path() == \
        str(tmp_path / 'processing_status.json')

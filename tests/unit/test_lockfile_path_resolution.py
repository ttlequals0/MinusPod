"""The flask-key lockfile path is resolved at call time, not frozen at import."""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('lockfile_path_test_')

from utils.paths import DEFAULT_DATA_DIR  # noqa: E402
import main_app  # noqa: E402


def test_matches_production_with_no_env_set(monkeypatch):
    for var in ('DATA_DIR', 'DATA_PATH', 'MINUSPOD_DATA_DIR'):
        monkeypatch.delenv(var, raising=False)
    assert main_app._secret_key_lockfile_path().as_posix() == \
        f'{DEFAULT_DATA_DIR}/.secret_key.lock'


def test_honours_data_dir_set_after_import(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    assert main_app._secret_key_lockfile_path() == tmp_path / '.secret_key.lock'

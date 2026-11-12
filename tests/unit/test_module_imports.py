"""Every top-level module under src/ imports on its own, so no import cycle depends on load order."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
_MODULES = sorted(
    {name[:-3] for name in os.listdir(_SRC) if name.endswith('.py')}
    | {name for name in os.listdir(_SRC) if os.path.isfile(os.path.join(_SRC, name, '__init__.py'))})


def _import_alone(module, data_dir):
    env = {**os.environ, 'PYTHONPATH': _SRC, 'DATA_DIR': data_dir, 'SECRET_KEY': 'test-secret'}
    result = subprocess.run([sys.executable, '-c', f'import {module}'], env=env, cwd=data_dir,
                            capture_output=True, text=True, timeout=300)
    return module, result.returncode, result.stderr.strip().splitlines()[-1:]


def test_every_top_level_module_imports_in_a_fresh_interpreter(tmp_path):
    assert {'secrets_crypto', 'db_backup_service', 'config_transfer', 'main_app', 'utils'} <= set(_MODULES)
    dirs = []
    for module in _MODULES:
        dirs.append(str(tmp_path / module))
        os.mkdir(dirs[-1])
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(pool.map(_import_alone, _MODULES, dirs))
    failed = [(module, err) for module, code, err in results if code != 0]
    assert not failed, failed

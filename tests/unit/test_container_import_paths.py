"""Regression gate for module-level `from version import ...` in gunicorn boot modules.

gunicorn only puts /app/src on sys.path, so the repo root (version.py) is not importable at boot;
pytest's wider path hides this, so a subprocess reproduces the narrower one."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"

# Modules gunicorn imports at boot: `database` (gunicorn.conf.py's
# on_starting/post_fork hooks) and `main_app`, the WSGI app object named
# by entrypoint.sh's `gunicorn -c /app/gunicorn.conf.py main_app:app`.
_BOOT_MODULES = ("database", "main_app")


def _run_in_container_like_sandbox(code: str) -> subprocess.CompletedProcess:
    """Run `code` with only src/ on PYTHONPATH and a neutral cwd so sys.path[0] cannot reintroduce the repo root."""
    data_dir = tempfile.mkdtemp(prefix="container_import_gate_data_")
    neutral_cwd = tempfile.mkdtemp(prefix="container_import_gate_cwd_")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_SRC_DIR),
        # Storage() and Database() default to /app/data outside the
        # container; point every env var those read at a writable temp dir.
        "DATA_DIR": data_dir,
        "DATA_PATH": data_dir,
        "MINUSPOD_DATA_DIR": data_dir,
        "SECRET_KEY": "container-import-gate-test",
    }
    # Passed through, not injected: leave PYTHONHOME as the parent process
    # has it (usually unset) rather than forcing a value either way.
    for passthrough in ("PYTHONHOME", "VIRTUAL_ENV", "HOME", "LANG", "LC_ALL"):
        if passthrough in os.environ:
            env[passthrough] = os.environ[passthrough]
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=neutral_cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_boot_modules_import_with_only_src_on_syspath():
    """`database` and `main_app` must import with only src on sys.path (gunicorn's boot path,
    narrower than pytest's)."""
    repo_root_str = str(_REPO_ROOT)
    code = (
        "import sys\n"
        f"assert {repo_root_str!r} not in sys.path, sys.path\n"
        "import " + ", ".join(_BOOT_MODULES) + "\n"
        "from utils.app_version import APP_VERSION\n"
        "assert APP_VERSION != 'unknown', APP_VERSION\n"
        "print('OK:' + APP_VERSION)\n"
    )
    result = _run_in_container_like_sandbox(code)
    assert result.returncode == 0, (
        "boot-path import failed with only src/ on sys.path (repo root "
        f"absent, neutral cwd):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # main_app's own logging handler writes to stdout, so the sentinel line
    # is interleaved with app boot logs (background threads log after it
    # too): search rather than assume it's the last line.
    match = re.search(r"^OK:(?P<version>\S+)$", result.stdout, re.MULTILINE)
    assert match, f"sentinel line not found in stdout:\n{result.stdout}"
    assert match.group("version") != "unknown", match.group("version")

"""Regression gate for the module-level `from version import ...` incident.

gunicorn's boot path only puts /app/src on sys.path (gunicorn.conf.py's
on_starting/post_fork hooks); the repo root (where version.py lives) is not
importable there, so a module-level `from version import __version__` in
anything gunicorn imports at boot raises ModuleNotFoundError and
crash-loops every worker. The pytest suite runs with PYTHONPATH=src from
the repo root, so the root is on sys.path there and this bug class was
invisible to it. This test reproduces the container's narrower boot
sys.path in a subprocess so the same bug class fails locally.
"""
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
    """Run `code` with sys.path mimicking the container boot: only src/ is
    on PYTHONPATH, the repo root is explicitly absent, and cwd is a neutral
    temp dir (not the repo root) so `python -c`'s implicit sys.path[0] entry
    (the cwd) can't silently reintroduce it.
    """
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
    """`database` and `main_app` must import cleanly when only the src dir
    is on sys.path: gunicorn's actual boot-time sys.path, not the wider
    one pytest runs under (PYTHONPATH=src from the repo root also puts the
    root itself on sys.path via cwd).
    """
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

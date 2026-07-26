"""Path-independent accessor for the running application version.

``version.py`` lives at the project root, which isn't always on ``sys.path``
(gunicorn's boot path only inserts ``/app/src``), so a bare
``from version import __version__`` can crash-loop workers with
``ModuleNotFoundError``. This module reads it by explicit file path instead.
Import ``APP_VERSION`` or call ``get_app_version()``.
"""
import importlib.util
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# src/utils/app_version.py -> parents[0]=src/utils, [1]=src, [2]=project root.
_VERSION_FILE = Path(__file__).resolve().parents[2] / "version.py"


def _read_version_file() -> Optional[str]:
    """Load version.py's __version__ by explicit path, without touching sys.path."""
    try:
        spec = importlib.util.spec_from_file_location("_minuspod_version", _VERSION_FILE)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "__version__", None)
    except Exception:
        logger.warning("Could not read version.py at %s", _VERSION_FILE, exc_info=True)
        return None


def get_app_version() -> str:
    """Return the running app version.

    Resolution order: MINUSPOD_VERSION env var (if an operator set one),
    then version.py read by explicit path, then 'unknown'.
    """
    env_version = os.environ.get("MINUSPOD_VERSION")
    if env_version:
        return env_version
    return _read_version_file() or "unknown"


APP_VERSION: str = get_app_version()

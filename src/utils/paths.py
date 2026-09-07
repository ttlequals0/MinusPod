"""Filesystem locations resolved from the environment."""
import os
from pathlib import Path

DEFAULT_DATA_DIR = '/app/data'


def resolve_data_dir() -> Path:
    """The data directory, read fresh on every call so a relocated MINUSPOD_DATA_DIR
    is honoured regardless of import order."""
    return Path(
        os.environ.get('DATA_DIR')
        or os.environ.get('DATA_PATH')
        or os.environ.get('MINUSPOD_DATA_DIR')
        or DEFAULT_DATA_DIR
    )


# Repo layout: <root>/src/utils/paths.py and <root>/static/ui/logo.png; the
# container mirrors it under /app.
LOGO_PATH = Path(__file__).resolve().parents[2] / 'static' / 'ui' / 'logo.png'

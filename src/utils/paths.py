"""Filesystem locations resolved from the environment."""
import os
from pathlib import Path

DEFAULT_DATA_DIR = '/app/data'


def resolve_data_dir() -> Path:
    """The data directory, read at call time.

    Reading the environment on every call rather than at import keeps a
    relocated MINUSPOD_DATA_DIR honoured no matter which module imported
    first.
    """
    return Path(
        os.environ.get('DATA_DIR')
        or os.environ.get('DATA_PATH')
        or os.environ.get('MINUSPOD_DATA_DIR')
        or DEFAULT_DATA_DIR
    )

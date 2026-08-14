"""Atomic JSON writes for files shared between gunicorn workers."""
import json
import os
import tempfile
from typing import Union


def write_json_atomic(path: Union[str, os.PathLike], obj) -> bool:
    """Replace `path` with `obj` as JSON. Returns False if the write failed.

    The temp file is per-writer. A shared temp name lets one worker truncate
    another's partial write, after which both rename the wreckage into place.
    """
    path = os.fspath(path)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(path) or '.',
            prefix=f'.{os.path.basename(path)}-', suffix='.tmp',
        )
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f)
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        return False

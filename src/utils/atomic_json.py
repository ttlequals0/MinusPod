"""Atomic JSON writes for files shared between gunicorn workers."""
import json
import os
import tempfile
from typing import Union


# Read once at import: os.umask(0) to sample it would briefly let any
# concurrent file creation in this process land world-writable.
_UMASK = os.umask(0)
os.umask(_UMASK)
_FILE_MODE = 0o666 & ~_UMASK


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
        # mkstemp gives 0600; the previous fixed-path write was umask-derived,
        # and tightening it locks out a container running as another uid.
        os.fchmod(fd, _FILE_MODE)
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f)
        os.replace(temp_path, path)
        return True
    except Exception:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        return False

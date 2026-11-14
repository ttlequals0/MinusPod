"""Return freed heap memory to the OS (glibc malloc_trim)."""

import ctypes
import gc
import logging
import os

logger = logging.getLogger(__name__)

_MIB = 1024 ** 2


def _load_malloc_trim():
    """malloc_trim from the process libc, or None off glibc."""
    try:
        return ctypes.CDLL(None).malloc_trim
    except (OSError, AttributeError):
        return None


_malloc_trim = _load_malloc_trim()


def _rss_mib() -> float | None:
    """Resident set size in MiB from /proc/self/statm, else None."""
    try:
        with open('/proc/self/statm') as f:
            return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') / _MIB
    except (OSError, ValueError, IndexError):
        return None


def trim_idle_memory() -> bool:
    """Collect garbage and trim the malloc heap; True if a trim ran. Never raises."""
    if _malloc_trim is None:
        return False
    try:
        gc.collect()
        before = _rss_mib()
        _malloc_trim(0)
        after = _rss_mib()
        if before is not None and after is not None:
            logger.info("Idle memory trim: RSS %.0f MiB -> %.0f MiB", before, after)
        else:
            logger.info("Idle memory trim ran")
        return True
    except Exception as exc:
        logger.debug("Idle memory trim failed: %s", exc)
        return False

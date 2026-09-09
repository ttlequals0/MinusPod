"""Log when this process stops scheduling Python threads for several seconds.

A sleeping thread that wakes late means every thread was starved: a C call
holding the GIL, or the container out of CPU.
"""
import logging
import os
import sys
import threading
import time

logger = logging.getLogger('podcast.watchdog')

TICK_SECONDS = 1.0
STALL_SECONDS = 5.0


def _thread_positions(skip_ident):
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = []
    for ident, frame in sys._current_frames().items():
        if ident == skip_ident:
            continue
        lines.append(f"{names.get(ident, ident)} at "
                     f"{frame.f_code.co_filename.rsplit('/', 1)[-1]}:{frame.f_lineno} "
                     f"{frame.f_code.co_name}")
    return '; '.join(lines)


def _loop():
    me = threading.get_ident()
    # The stall itself cannot be sampled (no thread runs during it), so the
    # snapshot from the tick before it is what shows where the hog began.
    before = _thread_positions(me)
    while True:
        started = time.monotonic()
        time.sleep(TICK_SECONDS)
        late = time.monotonic() - started - TICK_SECONDS
        now = _thread_positions(me)
        if late >= STALL_SECONDS:
            logger.warning("pid %d thread scheduling stalled %.1fs. Before: %s. After: %s",
                           os.getpid(), late, before, now)
        before = now


def start():
    threading.Thread(target=_loop, name='stall-watchdog', daemon=True).start()
    logger.info("Stall watchdog started (pid %d, %.0fs threshold)",
                os.getpid(), STALL_SECONDS)

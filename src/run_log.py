"""Per-run pipeline log capture (issue #660).

A RunLogRecorder is attached to the root logger for the duration of one
processing run and streams the run's records to a temp JSONL file, which
the pipeline renames onto the run's processing_history row when the row
lands. Nothing in here may raise into the pipeline.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger('podcast.run_log')

DEFAULT_SIZE_CAP_BYTES = 20 * 1024 * 1024
TRUNCATION_MARKER = 'log truncated at size cap'

_MESSAGE_FORMATTER = logging.Formatter('%(message)s')
_TEMP_NAME_UNSAFE = re.compile(r'[^A-Za-z0-9._-]')

# One run is processed at a time per worker process, so the active recorder
# is a process-wide slot rather than a contextvar: pool workers must see it
# and a new thread starts with an empty context.
_active_lock = threading.Lock()
_active_recorder = None


def current_recorder():
    """The recorder capturing the run in flight, or None."""
    with _active_lock:
        return _active_recorder


def _set_current_recorder(recorder):
    global _active_recorder
    with _active_lock:
        _active_recorder = recorder


def _clear_current_recorder(recorder):
    global _active_recorder
    with _active_lock:
        if _active_recorder is recorder:
            _active_recorder = None


class RunLogRecorder(logging.Handler):
    """Logging handler that writes one run's records to a JSONL file."""

    def __init__(self, slug, episode_id, min_level, temp_dir,
                 size_cap_bytes=DEFAULT_SIZE_CAP_BYTES):
        super().__init__(level=min_level)
        self.setFormatter(_MESSAGE_FORMATTER)
        self.slug = slug
        self.episode_id = episode_id
        self.tag = f"[{slug}:{episode_id}]"
        self.size_cap_bytes = size_cap_bytes
        self.disabled = False
        self.truncated = False
        self.bytes_written = 0
        self.temp_path = Path(temp_dir) / self._temp_name()
        self._stream = None
        self._finalized = False
        self._threads = set()
        self._threads_lock = threading.Lock()

    def _temp_name(self):
        stamp = int(time.time() * 1000)
        safe_slug = _TEMP_NAME_UNSAFE.sub('_', str(self.slug))[:64]
        safe_episode = _TEMP_NAME_UNSAFE.sub('_', str(self.episode_id))[:64]
        return f"run-{safe_slug}-{safe_episode}-{stamp}.jsonl.tmp"

    def attach(self):
        """Start capturing on the root logger. Idempotent."""
        try:
            root = logging.getLogger()
            if self not in root.handlers:
                root.addHandler(self)
            _set_current_recorder(self)
        except Exception as err:
            self._disable(err)

    def detach(self):
        """Stop capturing. Idempotent."""
        try:
            root = logging.getLogger()
            if self in root.handlers:
                root.removeHandler(self)
        except Exception as err:
            logger.warning(f"run log detach failed: {err}")
        _clear_current_recorder(self)

    def register_thread(self):
        """Mark the calling thread as part of this run."""
        try:
            with self._threads_lock:
                self._threads.add(threading.get_ident())
        except Exception as err:
            logger.warning(f"run log thread registration failed: {err}")

    def emit(self, record):
        if self.disabled or self.truncated or self._finalized:
            return
        try:
            if record.levelno < self.level:
                return
            message = self.format(record)
            if not self._belongs(record, message):
                return
            self._write(record.levelname, record.name, message,
                        created=record.created)
        except Exception as err:
            self._disable(err)

    def _belongs(self, record, message):
        if self.tag in message:
            return True
        with self._threads_lock:
            return record.thread in self._threads

    def _write(self, level, logger_name, message, created=None):
        payload = json.dumps({
            'ts': _iso_ts(created),
            'level': level,
            'logger': logger_name,
            'msg': message,
        }, ensure_ascii=False)
        line = payload + '\n'
        encoded_len = len(line.encode('utf-8'))
        if self.bytes_written + encoded_len > self.size_cap_bytes:
            self.truncated = True
            self._write_marker()
            return
        self._stream_write(line, encoded_len)

    def _write_marker(self):
        marker = json.dumps({
            'ts': _iso_ts(None),
            'level': 'WARNING',
            'logger': logger.name,
            'msg': TRUNCATION_MARKER,
        }) + '\n'
        self._stream_write(marker, len(marker.encode('utf-8')))

    def _stream_write(self, line, encoded_len):
        if self._stream is None:
            self.temp_path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = open(self.temp_path, 'a', encoding='utf-8',
                                buffering=1)
        self._stream.write(line)
        self.bytes_written += encoded_len

    def _disable(self, err):
        """Turn the recorder off for the rest of the run, once, quietly."""
        if self.disabled:
            return
        self.disabled = True
        try:
            logger.warning(f"run log capture disabled for {self.tag}: {err}")
        except Exception:
            pass
        self._close()

    def _close(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

    def finalize(self, final_path):
        """Move the temp file to ``final_path``; returns bytes/truncated or None."""
        self._finalized = True
        self._close()
        if self.disabled or self.bytes_written == 0:
            self._remove_temp()
            return None
        try:
            final_path = Path(final_path)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.temp_path, final_path)
            return {'bytes': self.bytes_written, 'truncated': self.truncated}
        except Exception as err:
            logger.warning(f"run log finalize failed for {self.tag}: {err}")
            self._remove_temp()
            return None

    def discard(self):
        """Drop the temp file; a finalized recorder keeps its final file."""
        self._close()
        if not self._finalized:
            self._remove_temp()

    def _remove_temp(self):
        try:
            self.temp_path.unlink(missing_ok=True)
        except Exception as err:
            logger.warning(f"run log temp cleanup failed for {self.tag}: {err}")


def _iso_ts(created):
    when = (datetime.fromtimestamp(created, timezone.utc) if created
            else datetime.now(timezone.utc))
    return when.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

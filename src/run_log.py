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
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger('podcast.run_log')

DEFAULT_SIZE_CAP_BYTES = 20 * 1024 * 1024
TRUNCATION_MARKER = 'log truncated at size cap'
STOPPED_MARKER = 'log capture stopped early after a write error'
EPISODE_LOG_PREFIX = 'logs/episodes/'
# A .jsonl.tmp younger than this may belong to a run still writing it.
TEMP_MIN_AGE_SECONDS = 6 * 3600

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


def register_worker_thread():
    """Mark the calling pool worker as part of the run in flight, if any."""
    recorder = current_recorder()
    if recorder is not None:
        recorder.register_thread()


def unregister_worker_thread():
    """Drop the calling thread's registration once its task is done."""
    recorder = current_recorder()
    if recorder is not None:
        recorder.unregister_thread()


def run_in_worker_thread(fn, *args, **kwargs):
    """Pool-task wrapper that registers the worker thread for the run log.

    Registration is dropped in the finally: thread idents are recycled, and an
    unregistered ident must not hand a later thread this run's capture.
    """
    register_worker_thread()
    try:
        return fn(*args, **kwargs)
    finally:
        unregister_worker_thread()


def run_log_temp_dir(data_dir):
    """Where in-flight run logs are written before they are finalized."""
    return Path(data_dir) / 'logs' / 'tmp'


def episode_log_root(data_dir):
    """Root of the per-episode run log tree."""
    return Path(data_dir) / 'logs' / 'episodes'


def run_log_relative_path(slug, episode_id, history_id) -> str:
    """Stored pointer: the run log path relative to the data dir."""
    return f"logs/episodes/{slug}/{episode_id}/run-{history_id}.jsonl"


def run_log_path(data_dir, slug, episode_id, history_id):
    """Absolute run log path, refusing slugs that escape the log root."""
    # Lazy: storage pulls the database stack, and this module stays importable
    # on its own.
    from storage import _safe_join_under
    root = episode_log_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return _safe_join_under(root, str(slug), str(episode_id),
                            f"run-{history_id}.jsonl")


def resolve_stored_log_path(data_dir, relative_path):
    """Absolute path for a stored pointer, refusing anything outside the log tree.

    Contained under the episode log root, not the data dir: a poisoned pointer
    must not be able to name the database or an audio file.
    """
    from storage import PathContainmentError, _safe_join_under
    relative = str(relative_path)
    if not relative.startswith(EPISODE_LOG_PREFIX):
        raise PathContainmentError(
            f"log pointer {relative!r} is not under {EPISODE_LOG_PREFIX}")
    return _safe_join_under(episode_log_root(data_dir),
                            *relative[len(EPISODE_LOG_PREFIX):].split('/'))


def delete_feed_logs(data_dir, slug):
    """Remove one feed's run logs; called when the feed itself is deleted."""
    try:
        directory = _safe_feed_log_dir(data_dir, slug)
    except Exception as err:
        logger.warning(f"[{slug}] refusing to delete run logs: {err}")
        return False
    if not directory.exists():
        return True
    try:
        shutil.rmtree(directory)
        return True
    except OSError as err:
        logger.warning(f"[{slug}] failed to delete run logs: {err}")
        return False


def _safe_feed_log_dir(data_dir, slug):
    from storage import _safe_join_under
    root = episode_log_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return _safe_join_under(root, str(slug))


def sweep_expired_logs(db, data_dir, retention_days):
    """Delete run logs past retention, clearing the pointers that named them.

    Returns (pruned, orphans): rows whose log was deleted, and files with no
    row (including temp files a killed run left behind). Retention 0 removes
    everything, matching the setting that turns storage off.
    """
    now = time.time()
    cutoff = now - max(0, int(retention_days or 0)) * 86400
    try:
        pointers = db.get_history_log_pointers()
    except Exception as err:
        # Without the map every file looks like an orphan, and deleting on
        # that guess would take live logs with it.
        logger.warning(
            f"run log sweep skipped: could not read log pointers: {err}")
        return 0, 0

    pruned = orphans = 0
    cleared = set()
    root = episode_log_root(data_dir)
    if root.exists():
        for path in sorted(root.rglob('run-*.jsonl')):
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                relative = path.relative_to(Path(data_dir)).as_posix()
                path.unlink()
                _prune_empty_dirs(path.parent, root)
            except Exception as err:
                logger.warning(f"run log sweep could not remove {path}: {err}")
                continue
            history_id = pointers.get(relative)
            if history_id is None:
                orphans += 1
                continue
            pruned += 1
            cleared.add(history_id)
            _clear_pointer(db, history_id)

    # Heal rows whose file went missing out of band (manual delete, restored
    # database, moved data dir) so the UI stops offering a log that is gone.
    for relative, history_id in pointers.items():
        if history_id in cleared:
            continue
        try:
            if resolve_stored_log_path(data_dir, relative).exists():
                continue
        except Exception:
            pass
        pruned += 1
        _clear_pointer(db, history_id)

    temp_dir = run_log_temp_dir(data_dir)
    if temp_dir.exists():
        # A live recorder owns a fresh temp file, and retention 0 would
        # otherwise unlink the log of the run in flight.
        temp_cutoff = min(cutoff, now - TEMP_MIN_AGE_SECONDS)
        for path in sorted(temp_dir.glob('*.jsonl.tmp')):
            try:
                if path.stat().st_mtime > temp_cutoff:
                    continue
                path.unlink()
                orphans += 1
            except Exception as err:
                logger.warning(f"run log sweep could not remove {path}: {err}")
    return pruned, orphans


def _clear_pointer(db, history_id):
    try:
        db.set_history_log_pointer(history_id, None)
    except Exception as err:
        logger.warning(f"run log sweep could not clear pointer {history_id}: {err}")


def _prune_empty_dirs(directory, root):
    """Remove now-empty episode and feed directories under the log root."""
    current = directory
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


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
        """Start capturing on the root logger. Idempotent.

        The attaching thread is the pipeline thread, so its untagged lines
        (ffmpeg, transcriber, storage) belong to this run.
        """
        try:
            root = logging.getLogger()
            if self not in root.handlers:
                root.addHandler(self)
            self.register_thread()
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

    def unregister_thread(self):
        """Forget the calling thread; a recycled ident is not this run."""
        try:
            with self._threads_lock:
                self._threads.discard(threading.get_ident())
        except Exception as err:
            logger.warning(f"run log thread deregistration failed: {err}")

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
        encoded = _encode_line({
            'ts': _iso_ts(created),
            'level': level,
            'logger': logger_name,
            'msg': message,
        })
        if self.bytes_written + len(encoded) > self.size_cap_bytes:
            self.truncated = True
            self._write_marker(TRUNCATION_MARKER)
            return
        self._stream_write(encoded)

    def _write_marker(self, message):
        self._stream_write(_encode_line({
            'ts': _iso_ts(None),
            'level': 'WARNING',
            'logger': logger.name,
            'msg': message,
        }))

    def _open_stream(self):
        self.temp_path.parent.mkdir(parents=True, exist_ok=True)
        # Binary and unbuffered: lines are already encoded once, and a killed
        # process keeps everything written so far.
        return open(self.temp_path, 'ab', buffering=0)

    def _stream_write(self, encoded):
        if self._stream is None:
            # A record racing finalize must not recreate the temp file the
            # rename just consumed.
            if self._finalized or self.disabled:
                return
            self._stream = self._open_stream()
        self._stream.write(encoded)
        self.bytes_written += len(encoded)

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
        """Move the temp file to ``final_path``; returns bytes/truncated or None.

        A recorder that disabled itself mid-run keeps the lines it did write,
        with a marker saying capture stopped there.
        """
        # The handler lock is what emit runs under, so no record can be
        # mid-write while the file is closed and renamed.
        self.acquire()
        try:
            if self._finalized:
                return None
            self._finalized = True
            if self.disabled and self.bytes_written:
                self._append_stopped_marker()
            self._close()
            if self.bytes_written == 0:
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
        finally:
            self.release()

    def _append_stopped_marker(self):
        """Best-effort note that capture died before the run did."""
        try:
            if self._stream is None:
                self._stream = self._open_stream()
            self._write_marker(STOPPED_MARKER)
        except Exception:
            pass

    def discard(self):
        """Drop the temp file; a finalized recorder keeps its final file."""
        self.disabled = True
        self._close()
        if not self._finalized:
            self._remove_temp()
        self._finalized = True

    def _remove_temp(self):
        try:
            self.temp_path.unlink(missing_ok=True)
        except Exception as err:
            logger.warning(f"run log temp cleanup failed for {self.tag}: {err}")


def _encode_line(payload):
    """One JSONL line as bytes; backslashreplace keeps a lone surrogate in a
    transcript from costing the run its whole log."""
    return (json.dumps(payload, ensure_ascii=False) + '\n').encode(
        'utf-8', 'backslashreplace')


def _iso_ts(created):
    when = (datetime.fromtimestamp(created, timezone.utc) if created
            else datetime.now(timezone.utc))
    return when.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

"""Bounded, payload-free operational diagnostics for self-hosted support."""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re

from utils.app_version import APP_VERSION

DIAGNOSTIC_DIR = 'logs/diagnostics'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 8
MAX_EXPORT_RECORDS = 10_000
MAX_EXPORT_BYTES = MAX_FILE_BYTES * MAX_FILES
RETENTION_DAYS = 7
_SKIP_LOGGERS = ('podcast.llm_io',)
_handler = None
_SRC_ROOT = Path(__file__).resolve().parent
_LEVELS = frozenset(('INFO', 'WARNING', 'ERROR', 'CRITICAL', 'OTHER'))
_CATEGORIES = frozenset(('api', 'audio', 'llm', 'feed', 'application', 'external'))
_VERSION_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$')
_SAFE_VERSION = APP_VERSION if _VERSION_RE.fullmatch(APP_VERSION) else 'unknown'
_SOURCE_ALLOWLIST = frozenset(
    path.relative_to(_SRC_ROOT).as_posix()
    for path in _SRC_ROOT.rglob('*.py')
    if not path.is_symlink() and path.is_file()
)


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _category(name: str) -> str:
    if name.startswith('podcast.api'):
        return 'api'
    if name.startswith(('podcast.audio', 'podcast.transcribe')):
        return 'audio'
    if name.startswith(('podcast.claude', 'podcast.llm')):
        return 'llm'
    if name.startswith(('podcast.feed', 'podcast.refresh')):
        return 'feed'
    if name.startswith('podcast.'):
        return 'application'
    return 'external'


@lru_cache(maxsize=512)
def _source_path(pathname: str) -> str:
    try:
        relative = Path(pathname).resolve().relative_to(_SRC_ROOT)
    except (OSError, TypeError, ValueError):
        return 'external'
    if relative.as_posix() not in _SOURCE_ALLOWLIST:
        return 'external'
    return relative.as_posix()


def _source(pathname: str, line: int) -> tuple[str, int]:
    return _source_path(pathname), max(0, min(int(line), 1_000_000))


def _level(levelno: int) -> str:
    if levelno >= logging.CRITICAL:
        return 'CRITICAL'
    if levelno >= logging.ERROR:
        return 'ERROR'
    if levelno >= logging.WARNING:
        return 'WARNING'
    if levelno >= logging.INFO:
        return 'INFO'
    return 'OTHER'


def _diagnostic_directory(data_dir):
    base = Path(data_dir).resolve()
    directory = base / DIAGNOSTIC_DIR
    current = directory
    while current != base:
        if current.exists() and current.is_symlink():
            raise OSError('diagnostic path contains a symlink')
        current = current.parent
    return directory


def _canonical_event(raw):
    if not isinstance(raw, dict):
        return None
    raw_level = raw.get('level')
    raw_category = raw.get('category')
    raw_version = raw.get('version')
    level = raw_level if isinstance(raw_level, str) and raw_level in _LEVELS else 'OTHER'
    category = raw_category if isinstance(raw_category, str) and raw_category in _CATEGORIES else 'external'
    version = raw_version if isinstance(raw_version, str) and _VERSION_RE.fullmatch(raw_version) else 'unknown'
    source = raw.get('source')
    if source != 'external':
        try:
            relative = Path(source)
            if (relative.is_absolute() or '..' in relative.parts
                    or relative.as_posix() not in _SOURCE_ALLOWLIST):
                source = 'external'
            else:
                source = relative.as_posix()
        except (TypeError, ValueError):
            source = 'external'
    try:
        event_time = datetime.fromisoformat(str(raw['ts']).replace('Z', '+00:00'))
        if event_time.tzinfo is None:
            raise ValueError
        timestamp = event_time.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        try:
            line = max(0, min(int(raw.get('line', 0)), 1_000_000))
        except (ValueError, TypeError, OverflowError):
            line = 0
    except (ValueError, KeyError, TypeError, OverflowError):
        return None
    return {
        'ts': timestamp, 'level': level, 'category': category,
        'source': source, 'line': line, 'version': version,
    }


class DiagnosticHandler(logging.Handler):
    """Write bounded metadata without formatted log messages."""

    def __init__(self, data_dir):
        super().__init__(level=logging.INFO)
        self.directory = _diagnostic_directory(data_dir)
        self.base_path = self.directory / f'diagnostic-{os.getpid()}'
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def emit(self, record):
        if record.name.startswith(_SKIP_LOGGERS):
            return
        try:
            source, line = _source(record.pathname, record.lineno)
            event = {
                'ts': _timestamp(record.created),
                'level': _level(record.levelno),
                'category': _category(record.name),
                'source': source,
                'line': line,
                'version': _SAFE_VERSION,
            }
            encoded = (json.dumps(event, separators=(',', ':')) + '\n').encode()
            with self._lock:
                self._ensure_directory()
                path = self.base_path.with_suffix('.jsonl')
                if path.exists() and path.stat().st_size + len(encoded) > MAX_FILE_BYTES:
                    self._rotate()
                self._append(self.base_path.with_suffix('.jsonl'), encoded)
                now = time.monotonic()
                if now - self._last_prune >= 60:
                    self._prune()
                    self._last_prune = now
        except Exception:
            return

    def _ensure_directory(self):
        if self.directory.exists() and self.directory.is_symlink():
            raise OSError('diagnostic directory is a symlink')
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _append(path, encoded):
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, 'ab', closefd=True) as stream:
            stream.write(encoded)

    def _rotate(self):
        for index in range(MAX_FILES - 1, 0, -1):
            source = self.base_path.with_suffix(f'.{index}.jsonl')
            target = self.base_path.with_suffix(f'.{index + 1}.jsonl')
            try:
                if index == MAX_FILES - 1:
                    source.unlink(missing_ok=True)
                elif source.exists() and not source.is_symlink():
                    os.replace(source, target)
            except OSError:
                continue
        current = self.base_path.with_suffix('.jsonl')
        try:
            if current.exists() and not current.is_symlink():
                os.replace(current, self.base_path.with_suffix('.1.jsonl'))
        except OSError:
            return

    def _prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        paths = []
        for path in self.directory.glob('diagnostic-*.jsonl'):
            try:
                if path.is_symlink():
                    continue
                mtime = path.stat().st_mtime
                if datetime.fromtimestamp(mtime, timezone.utc) < cutoff:
                    path.unlink()
                else:
                    paths.append((mtime, path))
            except OSError:
                continue
        for _, path in sorted(paths, reverse=True)[MAX_FILES:]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue


def install(data_dir):
    """Install one process-local diagnostic handler and return it."""
    global _handler
    if _handler is None:
        try:
            _handler = DiagnosticHandler(data_dir)
        except (OSError, ValueError):
            return None
        logging.getLogger().addHandler(_handler)
    return _handler


def _parse_time(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return parsed.astimezone(timezone.utc)


def export(data_dir, start: datetime, end: datetime) -> dict:
    """Return bounded diagnostic metadata for a UTC time range."""
    records = []
    bytes_read = 0
    retention_cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    try:
        directory = _diagnostic_directory(data_dir)
    except OSError:
        return {'events': [], 'truncated': False,
                'coverage': {'firstEvent': None, 'lastEvent': None, 'version': _SAFE_VERSION}}
    if not directory.is_dir():
        return {'events': [], 'truncated': False,
                'coverage': {'firstEvent': None, 'lastEvent': None, 'version': _SAFE_VERSION}}
    paths = []
    try:
        for path in directory.glob('diagnostic-*.jsonl'):
            try:
                if not path.is_symlink():
                    paths.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return {'events': [], 'truncated': False,
                'coverage': {'firstEvent': None, 'lastEvent': None, 'version': _SAFE_VERSION}}
    paths.sort(reverse=True)
    truncated = False
    for _, path in paths:
        try:
            with path.open(encoding='utf-8') as stream:
                while True:
                    line = stream.readline(1025)
                    if not line:
                        break
                    bytes_read += len(line)
                    if bytes_read > MAX_EXPORT_BYTES or len(line) > 1024:
                        truncated = True
                        break
                    try:
                        event = _canonical_event(json.loads(line))
                        if event is None:
                            continue
                        event_time = datetime.fromisoformat(event['ts'].replace('Z', '+00:00'))
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        continue
                    if retention_cutoff <= event_time <= end and start <= event_time:
                        records.append(event)
        except (OSError, UnicodeError):
            continue
        if truncated:
            break
    records.sort(key=lambda event: event['ts'])
    events = records[-MAX_EXPORT_RECORDS:]
    return {
        'events': events,
        'truncated': truncated or len(records) > MAX_EXPORT_RECORDS,
        'coverage': {
            'firstEvent': events[0]['ts'] if events else None,
            'lastEvent': events[-1]['ts'] if events else None,
            'version': _SAFE_VERSION,
        },
    }

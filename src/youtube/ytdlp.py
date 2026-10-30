"""yt-dlp binary lifecycle: baked floor, self-updating data-dir copy, subprocess wrapper."""
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

from utils.time import parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger(__name__)

BAKED_PATH = '/usr/local/bin/yt-dlp'
RUNTIME_NOT_EXECUTABLE = 'runtime yt-dlp copy is not executable; using the image copy'
UPDATE_INTERVAL = timedelta(hours=24)
_UPDATE_HINTS = ('please report this issue', 'unable to extract',
                 'requested format is not available', 'nsig extraction failed')
_booted = False
_ensured_path: Path | None = None
_baked_version: tuple[str, str | None] | None = None
_fallback_for: Path | None = None
_update_lock = threading.Lock()


class YtDlpNotInstalled(RuntimeError):
    pass


def runtime_dir(storage) -> Path:
    return Path(storage.data_dir) / 'youtube' / 'bin'


def runtime_path(storage) -> Path:
    return runtime_dir(storage) / 'yt-dlp'


def binary_version(path) -> str | None:
    try:
        out = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return text if out.returncode == 0 and text else None


def baked_version() -> str | None:
    """Version of the image copy, probed once per process (the image does not change under it)."""
    global _baked_version
    if _baked_version is None or _baked_version[0] != BAKED_PATH:
        _baked_version = (BAKED_PATH, binary_version(BAKED_PATH))
    return _baked_version[1]


def _version_tuple(text: str | None) -> tuple:
    try:
        return tuple(int(p) for p in (text or '').split('.'))
    except ValueError:
        return ()


def _install_baked(target: Path) -> None:
    """Copy the baked binary next to target, then rename over it (safe under concurrent workers)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix='.yt-dlp-')
    os.close(fd)
    try:
        shutil.copyfile(BAKED_PATH, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def ensure_runtime_binary(storage, db) -> Path:
    global _ensured_path, _fallback_for
    target = runtime_path(storage)
    if _fallback_for == target:
        return Path(BAKED_PATH)
    baked = baked_version()
    if not os.path.exists(target):
        if baked is None:
            raise YtDlpNotInstalled('yt-dlp is not installed in this image')
        _install_baked(target)
        _ensured_path = target
        return target
    target.chmod(0o755)
    current = binary_version(target)
    if current is None:
        db.set_setting('youtube_ytdlp_last_error', RUNTIME_NOT_EXECUTABLE, is_default=False)
        logger.warning(RUNTIME_NOT_EXECUTABLE)
        _fallback_for = target
        return Path(BAKED_PATH)
    if _version_tuple(baked) > _version_tuple(current):
        _install_baked(target)
    _ensured_path = target
    return target


def run(args: list[str], *, timeout: int, cwd=None, storage=None, db=None) -> subprocess.CompletedProcess:
    # Lazy import: main_app imports this package at startup.
    from main_app import db as app_db, storage as app_storage
    storage = storage or app_storage
    if _ensured_path is not None and _ensured_path == runtime_path(storage) and _ensured_path.exists():
        binary = _ensured_path
    else:
        binary = ensure_runtime_binary(storage, db or app_db)
    try:
        return subprocess.run([str(binary), *args], capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except FileNotFoundError as exc:
        raise YtDlpNotInstalled(str(exc)) from exc


def trimmed_error(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or '').splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith('ERROR:')]
    text = (errors or lines or ['yt-dlp failed'])[-1]
    return text[:500]


def needs_update(stderr: str) -> bool:
    low = (stderr or '').lower()
    return any(hint in low for hint in _UPDATE_HINTS)


def update_binary(storage, db, target: Path | None = None) -> dict:
    """Self-update the runtime copy; `target` is the already-ensured binary path."""
    if not _update_lock.acquire(blocking=False):
        return {'updated': False, 'version': db.get_setting('youtube_ytdlp_version'), 'error': 'update already running'}
    try:
        return _update_binary_locked(storage, db, target)
    finally:
        _update_lock.release()


def _update_binary_locked(storage, db, target) -> dict:
    now = utc_now_iso()
    db.set_setting('youtube_ytdlp_last_check_at', now, is_default=False)
    target = target or ensure_runtime_binary(storage, db)
    if str(target) == BAKED_PATH:
        return {'updated': False, 'version': baked_version(), 'error': RUNTIME_NOT_EXECUTABLE}
    before = binary_version(target)
    result = run(['-U'], timeout=300, storage=storage, db=db)
    if result.returncode != 0:
        error = trimmed_error(result.stderr or result.stdout)
        db.set_setting('youtube_ytdlp_last_error', error, is_default=False)
        return {'updated': False, 'version': before, 'error': error}
    after = binary_version(target)
    if after is None:
        error = 'version probe failed after update'
        db.set_setting('youtube_ytdlp_last_error', error, is_default=False)
        return {'updated': False, 'version': before, 'error': error}
    updated = after != before or 'updated yt-dlp' in (result.stdout or '').lower()
    db.set_setting('youtube_ytdlp_version', after, is_default=False)
    db.set_setting('youtube_ytdlp_last_error', '', is_default=False)
    if updated:
        db.set_setting('youtube_ytdlp_last_update_at', now, is_default=False)
        logger.info(f"yt-dlp updated {before} -> {after}")
    return {'updated': updated, 'version': after, 'error': None}


def update_tick(db):
    """Daily self-update, once at boot then every 24 h. Never raises."""
    global _booted
    try:
        if not db.get_setting_bool('youtube_enabled', False):
            return
        from main_app import storage  # lazy: avoids an import cycle
        if not db.get_setting_bool('youtube_ytdlp_auto_update', True):
            return
        last = parse_iso_utc(db.get_setting('youtube_ytdlp_last_check_at'))
        if _booted and last and utc_now() - last < UPDATE_INTERVAL:
            return
        _booted = True
        update_binary(storage, db, ensure_runtime_binary(storage, db))
    except Exception as exc:
        logger.warning(f"yt-dlp update failed: {exc}")
        try:
            db.set_setting('youtube_ytdlp_last_error', str(exc)[:500], is_default=False)
        except Exception:
            logger.warning("could not record yt-dlp error", exc_info=True)


def status(db) -> dict:
    return {'bakedVersion': baked_version(), 'version': db.get_setting('youtube_ytdlp_version')}

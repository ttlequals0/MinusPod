"""yt-dlp binary lifecycle: baked floor, self-updating data-dir copy, subprocess wrapper."""
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.time import parse_iso_utc, utc_now_iso

logger = logging.getLogger(__name__)

BAKED_PATH = '/usr/local/bin/yt-dlp'
RUNTIME_NOT_EXECUTABLE = 'runtime yt-dlp copy is not executable; using the image copy'
UPDATE_INTERVAL = timedelta(hours=24)
_UPDATE_HINTS = ('please report this issue', 'unable to extract',
                 'requested format is not available', 'nsig extraction failed')
_booted = False


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


def _version_tuple(text: str | None) -> tuple:
    try:
        return tuple(int(p) for p in (text or '').split('.'))
    except ValueError:
        return ()


def ensure_runtime_binary(storage, db) -> Path:
    target = runtime_path(storage)
    baked_version = binary_version(BAKED_PATH)
    if not os.path.exists(target):
        if baked_version is None:
            raise YtDlpNotInstalled('yt-dlp is not installed in this image')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BAKED_PATH, target)
        target.chmod(0o755)
        return target
    target.chmod(0o755)
    current = binary_version(target)
    if current is None:
        db.set_setting('youtube_ytdlp_last_error', RUNTIME_NOT_EXECUTABLE, is_default=False)
        logger.warning(RUNTIME_NOT_EXECUTABLE)
        return Path(BAKED_PATH)
    if _version_tuple(baked_version) > _version_tuple(current):
        shutil.copyfile(BAKED_PATH, target)
        target.chmod(0o755)
    return target


def run(args: list[str], *, timeout: int, cwd=None, storage=None, db=None) -> subprocess.CompletedProcess:
    # Lazy import: main_app imports this package at startup.
    from main_app import db as app_db, storage as app_storage
    binary = ensure_runtime_binary(storage or app_storage, db or app_db)
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


def update_binary(storage, db, *, force: bool = False) -> dict:
    now = utc_now_iso()
    db.set_setting('youtube_ytdlp_last_check_at', now, is_default=False)
    target = ensure_runtime_binary(storage, db)
    if str(target) == BAKED_PATH:
        return {'updated': False, 'version': binary_version(target), 'error': RUNTIME_NOT_EXECUTABLE}
    before = binary_version(target)
    result = run(['-U'], timeout=300, storage=storage, db=db)
    if result.returncode != 0:
        error = trimmed_error(result.stderr or result.stdout)
        db.set_setting('youtube_ytdlp_last_error', error, is_default=False)
        return {'updated': False, 'version': before, 'error': error}
    after = binary_version(target)
    updated = after != before or 'updated yt-dlp' in (result.stdout or '').lower()
    db.set_setting('youtube_ytdlp_version', after or before or '', is_default=False)
    db.set_setting('youtube_ytdlp_last_error', '', is_default=False)
    if updated:
        db.set_setting('youtube_ytdlp_last_update_at', now, is_default=False)
        logger.info(f"yt-dlp updated {before} -> {after}")
    return {'updated': updated, 'version': after, 'error': None}


def update_tick(db):
    """Daily self-update, once at boot then every 24 h. Never raises."""
    global _booted
    if not db.get_setting_bool('youtube_enabled', False):
        return
    from main_app import storage  # lazy: avoids an import cycle
    try:
        ensure_runtime_binary(storage, db)
    except YtDlpNotInstalled as exc:
        db.set_setting('youtube_ytdlp_last_error', str(exc), is_default=False)
        return
    if not db.get_setting_bool('youtube_ytdlp_auto_update', True):
        return
    last = parse_iso_utc(db.get_setting('youtube_ytdlp_last_check_at'))
    if _booted and last and datetime.now(timezone.utc) - last < UPDATE_INTERVAL:
        return
    _booted = True
    try:
        update_binary(storage, db)
    except Exception as exc:
        logger.warning(f"yt-dlp update failed: {exc}")
        db.set_setting('youtube_ytdlp_last_error', str(exc)[:500], is_default=False)


def status(storage, db) -> dict:
    target = runtime_path(storage)
    return {
        'runtimePath': str(target) if target.exists() else None,
        'bakedVersion': binary_version(BAKED_PATH),
        'version': db.get_setting('youtube_ytdlp_version') or (binary_version(target) if target.exists() else None),
    }

"""Operator-supplied Netscape cookies file for yt-dlp."""
import os
from datetime import datetime, timezone
from pathlib import Path


class InvalidCookiesError(ValueError):
    pass


def path(storage) -> Path:
    return Path(storage.data_dir) / 'youtube' / 'cookies.txt'


def _cookie_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith('#')]


def save(storage, text: str) -> dict:
    text = (text or '').replace('\r\n', '\n').strip() + '\n'
    rows = _cookie_lines(text)
    header_ok = text.lstrip().startswith('# Netscape HTTP Cookie File') or text.lstrip().startswith('# HTTP Cookie File')
    if not rows or not (header_ok or all(len(r.split('\t')) == 7 for r in rows)):
        raise InvalidCookiesError('Paste a Netscape-format cookies.txt (7 tab-separated fields per line)')
    target = path(storage)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(text)
    os.chmod(target, 0o600)
    return status(storage)


def clear(storage) -> None:
    try:
        os.unlink(path(storage))
    except FileNotFoundError:
        pass


def status(storage) -> dict:
    target = path(storage)
    if not target.exists():
        return {'set': False, 'lines': 0, 'updatedAt': None}
    updated = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc).isoformat()
    return {'set': True, 'lines': len(_cookie_lines(target.read_text(errors='replace'))), 'updatedAt': updated}

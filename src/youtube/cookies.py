"""Operator-supplied Netscape cookies file for yt-dlp."""
import os
from pathlib import Path

from utils.time import epoch_to_iso


class InvalidCookiesError(ValueError):
    pass


def path(storage) -> Path:
    return Path(storage.data_dir) / 'youtube' / 'cookies.txt'


def _cookie_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.strip() and (not ln.lstrip().startswith('#') or ln.startswith('#HttpOnly_'))]


def save(storage, text: str) -> dict:
    text = (text or '').replace('\r\n', '\n').strip('\r\n') + '\n'
    rows = _cookie_lines(text)
    header_ok = text.lstrip().startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File'))
    well_formed = [r for r in rows if len(r.split('\t')) == 7]
    valid = bool(well_formed) if header_ok else bool(rows) and len(well_formed) == len(rows)
    if not valid:
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
    updated = epoch_to_iso(target.stat().st_mtime)
    return {'set': True, 'lines': len(_cookie_lines(target.read_text(errors='replace'))), 'updatedAt': updated}

"""Backfill descriptions and exact publish times that flat yt-dlp listings lack."""
import json
import logging
import re
import subprocess
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from utils.time import epoch_to_iso, utc_now, utc_now_iso
from youtube import download, ytdlp
from youtube import settings as yt_settings
from youtube.urls import watch_url

logger = logging.getLogger(__name__)

METADATA_TIMEOUT_S = 180
BATCH = 10
IDLE_INTERVAL = timedelta(minutes=10)
_DATE_ONLY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_next_run = None


class MetadataError(Exception):
    def __init__(self, message: str, needs_update: bool = False):
        super().__init__(message)
        self.needs_update = needs_update


def video_id_of(row) -> str:
    vid = (row or {}).get('youtube_video_id')
    return vid or parse_qs(urlparse((row or {}).get('original_url') or '').query).get('v', [''])[0]


def fill_fields(episode_row, info) -> dict:
    """Upsert fields the info supplies and the row lacks."""
    fields = {}
    description = (info or {}).get('description')
    if description and not (episode_row or {}).get('description'):
        fields['description'] = description
    timestamp = (info or {}).get('timestamp')
    published = (episode_row or {}).get('published_at')
    if isinstance(timestamp, (int, float)) and (not published or _DATE_ONLY.match(str(published))):
        fields['published_at'] = epoch_to_iso(timestamp)
    return fields


def fetch_info(video_ids: list[str]) -> dict[str, dict]:
    """One yt-dlp call for all ids; returns info keyed by video id."""
    args = ['--dump-json', '--skip-download', '--no-playlist', '--no-warnings', '--no-progress']
    cookie_file = download.cookies_path()
    if cookie_file:
        args += ['--cookies', cookie_file]
    target = download._impersonate_target()
    if target:
        args += ['--impersonate', target]
    args += [watch_url(vid) for vid in video_ids]
    try:
        result = ytdlp.run(args, timeout=METADATA_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise MetadataError('yt-dlp timed out reading video metadata') from exc
    infos = {}
    for line in (result.stdout or '').splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if isinstance(info, dict) and info.get('id'):
            infos[info['id']] = info
    if result.returncode != 0 and not infos:
        raise MetadataError(ytdlp.trimmed_error(result.stderr), needs_update=ytdlp.needs_update(result.stderr))
    return infos


def backfill_tick(db):
    """Fill missing descriptions and publish times for a few episodes per pass, no download."""
    global _next_run
    if not yt_settings.enabled(db):
        return
    now = utc_now()
    if _next_run is not None and now < _next_run:
        return
    rows = db.get_youtube_episodes_missing_metadata(BATCH)
    if not rows:
        _next_run = now + IDLE_INTERVAL
        return
    wanted = [(row, video_id_of(row)) for row in rows]
    try:
        infos = fetch_info([vid for _, vid in wanted if vid])
    except MetadataError as exc:
        logger.warning(f"YouTube metadata backfill failed: {exc}")
        if exc.needs_update:
            from main_app import storage  # lazy: main_app instantiates Storage on import
            try:
                ytdlp.update_binary(storage, db)
            except Exception as update_exc:
                logger.warning(f"yt-dlp update failed: {update_exc}")
        _next_run = now + IDLE_INTERVAL
        return
    filled = 0
    for row, vid in wanted:
        fields = fill_fields(row, infos.get(vid, {}))
        if fields.get('description'):
            filled += 1
        db.upsert_episode(row['slug'], row['episode_id'], yt_metadata_at=utc_now_iso(), **fields)
    logger.info(f"YouTube metadata backfill: filled {filled} of {len(rows)} episode(s)")

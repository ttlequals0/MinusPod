"""Backfill descriptions and exact publish times that flat yt-dlp listings lack."""
import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from database.episodes import PodcastNotFoundError
from utils.time import epoch_to_iso, utc_now, utc_now_iso
from utils.youtube_fields import MIDNIGHT_ONLY, PLACEHOLDER_TITLES
from youtube import download, ytdlp
from youtube import settings as yt_settings
from youtube.urls import watch_url

logger = logging.getLogger(__name__)

METADATA_TIMEOUT_S = 180
BATCH = 10
IDLE_INTERVAL = timedelta(minutes=10)
_next_run = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='yt-metadata')
_future = None
MAX_MISSES = 3
_misses: dict[str, int] = {}


def video_id_of(row, url=None) -> str:
    vid = (row or {}).get('youtube_video_id')
    return vid or parse_qs(urlparse(url or (row or {}).get('original_url') or '').query).get('v', [''])[0]


def fill_fields(episode_row, info) -> dict:
    """Fields the info supplies and the row lacks."""
    fields = {}
    description = (info or {}).get('description')
    if description and not (episode_row or {}).get('description'):
        fields['description'] = description
    info = info or {}
    if info.get('title') and (episode_row or {}).get('title') in PLACEHOLDER_TITLES.values():
        fields['title'] = info['title']
    timestamp = info.get('timestamp')
    if not isinstance(timestamp, (int, float)):
        timestamp = info.get('release_timestamp')
    published = (episode_row or {}).get('published_at')
    if isinstance(timestamp, (int, float)) and (not published or MIDNIGHT_ONLY.match(str(published))):
        fields['published_at'] = epoch_to_iso(timestamp)
    return fields


def fetch_info(video_ids: list[str]) -> dict[str, dict]:
    """One yt-dlp call for all ids; returns info keyed by video id."""
    args = ['--dump-json', '--skip-download', '--no-playlist', '--no-warnings', '--no-progress']
    args += download.auth_args()
    args += [watch_url(vid) for vid in video_ids]
    try:
        result = ytdlp.run(args, timeout=METADATA_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise download.DownloadError('yt-dlp timed out reading video metadata') from exc
    infos = {}
    for line in (result.stdout or '').splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if isinstance(info, dict) and info.get('id'):
            infos[info['id']] = info
    if result.returncode != 0 and not infos:
        raise download.DownloadError(ytdlp.trimmed_error(result.stderr), needs_update=ytdlp.needs_update(result.stderr))
    return infos


def backfill_tick(db):
    """Start a background batch that fills missing descriptions and publish times, no download."""
    global _future
    if not yt_settings.enabled(db):
        return
    if (_future is not None and not _future.done()) or (_next_run is not None and utc_now() < _next_run):
        return
    _future = _executor.submit(_run_batch, db)


def _run_batch(db):
    global _next_run
    ok = False
    try:
        rows = db.get_youtube_episodes_missing_metadata(BATCH)
        ok = bool(rows)
        if rows:
            wanted = [(row, video_id_of(row)) for row in rows]
            try:
                infos = fetch_info([vid for _, vid in wanted if vid])
            except download.DownloadError as exc:
                ok = False
                logger.warning(f"YouTube metadata backfill failed: {exc}")
                if exc.needs_update:
                    ytdlp.update_after_failure(db, logger)
                for row, vid in wanted:
                    if vid and _give_up(vid):
                        _stamp(db, row)
        if ok:
            filled = 0
            for row, vid in wanted:
                if vid and vid not in infos and not _give_up(vid):
                    continue
                fields = fill_fields(row, infos.get(vid, {}))
                if fields.get('description'):
                    filled += 1
                _stamp(db, row, **fields)
            logger.info(f"YouTube metadata backfill: filled {filled} of {len(rows)} episode(s)")
    except Exception:
        ok = False
        logger.exception("YouTube metadata backfill failed")
    finally:
        db.clear_leaked_transaction(logger, 'youtube metadata backfill')
    _next_run = None if ok else utc_now() + IDLE_INTERVAL


def _stamp(db, row, **fields):
    try:
        db.upsert_episode(row['slug'], row['episode_id'], yt_metadata_at=utc_now_iso(), **fields)
    except PodcastNotFoundError:
        logger.debug(f"YouTube metadata backfill: skipping {row['slug']}/{row['episode_id']}, feed removed during backfill")


def _give_up(vid) -> bool:
    """Count a miss for a video yt-dlp returned nothing for; True once it reaches MAX_MISSES."""
    _misses[vid] = _misses.get(vid, 0) + 1
    if _misses[vid] < MAX_MISSES:
        return False
    logger.info(f"YouTube metadata backfill: stamping a video with no metadata after {_misses.pop(vid)} misses")
    return True

"""Download one YouTube video's audio (and captions) with yt-dlp."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from youtube import cookies, ytdlp
from youtube.captions import pick_file
from youtube.urls import watch_url

logger = logging.getLogger(__name__)
TIMEOUT_S = 1800
CAPTIONS_TIMEOUT_S = 300
_impersonate_cache: dict[str, str | None] = {}


class DownloadError(Exception):
    def __init__(self, message: str, needs_update: bool = False):
        super().__init__(message)
        self.needs_update = needs_update


@dataclass
class DownloadResult:
    audio_path: str
    info: dict
    captions_path: str | None


def resolve_bitrate() -> str:
    # Lazy import: main_app imports this package at startup.
    from main_app.pipeline.common import resolve_audio_encode_settings
    return resolve_audio_encode_settings()[0]


def cookies_path() -> str | None:
    # Lazy import: main_app imports this package at startup.
    from main_app import storage
    path = cookies.path(storage)
    return str(path) if path.exists() else None


def _impersonate_target() -> str | None:
    if 'target' not in _impersonate_cache:
        result = ytdlp.run(['--list-impersonate-targets'], timeout=60, extra_args=False, proxy=False)
        _impersonate_cache['target'] = 'chrome' if result.returncode == 0 and 'chrome' in result.stdout.lower() else None
    return _impersonate_cache['target']


def _sub_langs(language: str | None) -> str:
    if language and language != 'auto':
        lang = language.split('-')[0]
        return f'{lang},{lang}-orig,-live_chat'
    return '.*-orig,-live_chat'


def auth_args() -> list[str]:
    args = []
    cookie_file = cookies_path()
    if cookie_file:
        args += ['--cookies', cookie_file]
    target = _impersonate_target()
    if target:
        args += ['--impersonate', target]
    return args


def download(video_id: str, *, want_captions: bool, language: str) -> DownloadResult:
    """Fetch audio as mp3 into a fresh temp dir; the caller deletes that dir after moving the audio."""
    args = ['-x', '--audio-format', 'mp3', '--audio-quality', resolve_bitrate(), '--no-playlist',
            '--write-info-json', '--embed-chapters', '--no-write-playlist-metafiles', '--no-progress',
            '--no-warnings', '--sleep-requests', '1', '--sleep-interval', '2', '--max-sleep-interval', '8',
            '-o', '%(id)s.%(ext)s']
    if want_captions:
        args += ['--write-subs', '--write-auto-subs', '--sub-langs', _sub_langs(language), '--sub-format', 'json3']
    args += auth_args()
    args.append(watch_url(video_id))
    work_dir = tempfile.mkdtemp(prefix='minuspod-yt-')
    try:
        try:
            result = ytdlp.run(args, timeout=TIMEOUT_S, cwd=work_dir)
        except subprocess.TimeoutExpired as exc:
            raise DownloadError('yt-dlp timed out downloading the video') from exc
        if result.returncode != 0:
            raise DownloadError(ytdlp.trimmed_error(result.stderr), needs_update=ytdlp.needs_update(result.stderr))
        audio_path = os.path.join(work_dir, f'{video_id}.mp3')
        if not os.path.exists(audio_path):
            raise DownloadError('yt-dlp finished without producing an mp3')
        info = {}
        info_path = os.path.join(work_dir, f'{video_id}.info.json')
        if os.path.exists(info_path):
            try:
                with open(info_path) as fh:
                    info = json.load(fh)
            except (OSError, ValueError) as exc:
                raise DownloadError('yt-dlp wrote an unreadable info file') from exc
        captions_path = pick_file(work_dir, video_id, language) if want_captions else None
        return DownloadResult(audio_path=audio_path, info=info, captions_path=captions_path)
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def fetch_captions(video_id: str, *, language: str) -> str | None:
    """Fetch only the json3 captions; the caller deletes the returned file's directory."""
    args = ['--skip-download', '--write-subs', '--write-auto-subs', '--sub-langs', _sub_langs(language),
            '--sub-format', 'json3', '--no-playlist', '--no-warnings', '--no-progress',
            '-o', '%(id)s.%(ext)s'] + auth_args() + [watch_url(video_id)]
    work_dir = tempfile.mkdtemp(prefix='minuspod-yt-')
    try:
        try:
            result = ytdlp.run(args, timeout=CAPTIONS_TIMEOUT_S, cwd=work_dir)
        except subprocess.TimeoutExpired as exc:
            raise DownloadError('yt-dlp timed out fetching captions') from exc
        if result.returncode != 0:
            logger.debug(f"yt-dlp caption fetch for {video_id} exited {result.returncode}: "
                         f"{ytdlp.trimmed_error(result.stderr)}")
        path = pick_file(work_dir, video_id, language)
        if not path:
            shutil.rmtree(work_dir, ignore_errors=True)
        return path
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

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
_SUBTITLE_ERROR_PATTERNS = ('unable to download video subtitles', 'unable to download subtitles')

# DownloadError.kind values; None means the failure is not a recognized YouTube condition.
KIND_BLOCKED = 'blocked'
KIND_MEMBERS_ONLY = 'members_only'
# YouTube's own playability text, passed through by yt-dlp. Several phrasings, since YouTube rewords them.
_MEMBERS_ONLY_PATTERNS = ('members-only', "channel's members", 'join this channel')
_BLOCKED_PATTERNS = ('not a bot', 'captcha', 'http error 403')


class DownloadError(Exception):
    def __init__(self, message: str, needs_update: bool = False, kind: str | None = None):
        super().__init__(message)
        self.needs_update = needs_update
        self.kind = kind


def classify_error(stderr: str) -> str | None:
    """KIND_MEMBERS_ONLY or KIND_BLOCKED for a recognized yt-dlp failure, else None.

    Only ERROR lines count, and subtitle errors are left out: a failed caption track never decides
    what happened to the audio.
    """
    errors = [ln.strip().lower() for ln in (stderr or '').splitlines() if ln.strip().startswith('ERROR:')]
    errors = [ln for ln in errors if not any(pat in ln for pat in _SUBTITLE_ERROR_PATTERNS)]
    if any(pat in ln for ln in errors for pat in _MEMBERS_ONLY_PATTERNS):
        return KIND_MEMBERS_ONLY
    if any(pat in ln for ln in errors for pat in _BLOCKED_PATTERNS):
        return KIND_BLOCKED
    return None


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


def _is_subtitle_error(stderr: str) -> bool:
    """True when yt-dlp failed solely on subtitle download (e.g. caption HTTP 429)."""
    lines = [ln.strip() for ln in (stderr or '').splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith('ERROR:')]
    if not errors:
        return False
    return all(any(pat in ln.lower() for pat in _SUBTITLE_ERROR_PATTERNS) for ln in errors)


def _download_args(bitrate: str, auth: list[str], video_url: str, *,
                   want_captions: bool, language: str | None) -> list[str]:
    args = ['-x', '--audio-format', 'mp3', '--audio-quality', bitrate, '--no-playlist',
            '--write-info-json', '--embed-chapters', '--no-write-playlist-metafiles', '--no-progress',
            '--no-warnings', '--sleep-requests', '1', '--sleep-interval', '2', '--max-sleep-interval', '8',
            '-o', '%(id)s.%(ext)s']
    if want_captions:
        args += ['--write-subs', '--write-auto-subs', '--sub-langs', _sub_langs(language), '--sub-format', 'json3']
    args += auth
    args.append(video_url)
    return args


def _run_download(args: list[str], work_dir: str, lang: str | None = None) -> subprocess.CompletedProcess:
    try:
        return ytdlp.run(args, timeout=TIMEOUT_S, cwd=work_dir, lang=lang)
    except subprocess.TimeoutExpired as exc:
        raise DownloadError('yt-dlp timed out downloading the video') from exc


def download(video_id: str, *, want_captions: bool, language: str | None,
             metadata_lang: str | None = None) -> DownloadResult:
    """Fetch audio as mp3 into a fresh temp dir; the caller deletes that dir after moving the audio.

    `metadata_lang` (ytdlp.youtube_lang) picks the language of the title, description and chapters.
    """
    bitrate = resolve_bitrate()
    auth = auth_args()
    url = watch_url(video_id)
    args = _download_args(bitrate, auth, url, want_captions=want_captions, language=language)
    work_dir = tempfile.mkdtemp(prefix='minuspod-yt-')
    try:
        result = _run_download(args, work_dir, metadata_lang)
        if result.returncode != 0 and want_captions and _is_subtitle_error(result.stderr):
            logger.warning(
                f"yt-dlp caption download failed for {video_id}; retrying audio without captions: "
                f"{ytdlp.trimmed_error(result.stderr)}"
            )
            retry_args = _download_args(bitrate, auth, url, want_captions=False, language=language)
            result = _run_download(retry_args, work_dir, metadata_lang)
        if result.returncode != 0:
            raise DownloadError(ytdlp.trimmed_error(result.stderr), needs_update=ytdlp.needs_update(result.stderr),
                                kind=classify_error(result.stderr))
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

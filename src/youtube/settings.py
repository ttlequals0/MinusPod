"""Global and per-feed YouTube settings."""
from dataclasses import dataclass, replace

from youtube import ytdlp

BOUNDS = {
    'processDelayMinutes': (0, 1440),
    'sbProbeAttempts': (0, 48),
    'sbProbeIntervalMinutes': (5, 1440),
    'sbWatchIntervalHours': (1, 48),
    'sbWatchMaxDays': (1, 60),
}


INT_KEYS = {
    'processDelayMinutes': 'youtube_process_delay_minutes',
    'sbProbeAttempts': 'youtube_sb_probe_attempts',
    'sbProbeIntervalMinutes': 'youtube_sb_probe_interval_minutes',
    'sbWatchIntervalHours': 'youtube_sb_watch_interval_hours',
    'sbWatchMaxDays': 'youtube_sb_watch_max_days',
}
DEFAULTS = {
    'youtube_enabled': False,
    'youtube_process_delay_minutes': 60,
    'youtube_sb_probe_attempts': 6,
    'youtube_sb_probe_interval_minutes': 30,
    'youtube_sb_watch_interval_hours': 6,
    'youtube_sb_watch_max_days': 7,
    'youtube_llm_fallback': True,
    'youtube_captions_as_transcript': True,
    'youtube_hide_private': True,
    'youtube_transcribe_fallback': False,
    'youtube_ytdlp_auto_update': True,
    'youtube_ytdlp_extra_args': '',
    'youtube_proxy_url': '',
    'youtube_sb_submit_enabled': False,
}


def enabled(db) -> bool:
    """Master switch: when off, no YouTube network activity runs."""
    return db.get_setting_bool('youtube_enabled', DEFAULTS['youtube_enabled'])


def validate_yt_int(field: str, value, bound_key: str) -> str | None:
    """Error text when a nullable integer override is out of its bound, else None."""
    if value is None:
        return None
    lo, hi = BOUNDS[bound_key]
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        return f'{field} must be an integer between {lo} and {hi} or null'
    return None


@dataclass(frozen=True)
class YouTubeSettings:
    enabled: bool
    process_delay_minutes: int
    probe_attempts: int
    probe_interval_minutes: int
    watch_interval_hours: int
    watch_max_days: int
    llm_fallback: bool
    captions_as_transcript: bool
    hide_private: bool
    transcribe_fallback: bool
    ytdlp_auto_update: bool
    ytdlp_extra_args: str
    proxy_url: str = ''
    sb_submit_enabled: bool = False


def load_global(db) -> YouTubeSettings:
    d = DEFAULTS
    return YouTubeSettings(
        enabled=db.get_setting_bool('youtube_enabled', d['youtube_enabled']),
        process_delay_minutes=db.get_setting_int('youtube_process_delay_minutes', d['youtube_process_delay_minutes']),
        probe_attempts=db.get_setting_int('youtube_sb_probe_attempts', d['youtube_sb_probe_attempts']),
        probe_interval_minutes=db.get_setting_int(
            'youtube_sb_probe_interval_minutes', d['youtube_sb_probe_interval_minutes']),
        watch_interval_hours=db.get_setting_int('youtube_sb_watch_interval_hours', d['youtube_sb_watch_interval_hours']),
        watch_max_days=db.get_setting_int('youtube_sb_watch_max_days', d['youtube_sb_watch_max_days']),
        llm_fallback=db.get_setting_bool('youtube_llm_fallback', d['youtube_llm_fallback']),
        captions_as_transcript=db.get_setting_bool(
            'youtube_captions_as_transcript', d['youtube_captions_as_transcript']),
        hide_private=db.get_setting_bool('youtube_hide_private', d['youtube_hide_private']),
        transcribe_fallback=db.get_setting_bool(
            'youtube_transcribe_fallback', d['youtube_transcribe_fallback']),
        ytdlp_auto_update=db.get_setting_bool('youtube_ytdlp_auto_update', d['youtube_ytdlp_auto_update']),
        ytdlp_extra_args=db.get_setting('youtube_ytdlp_extra_args') or '',
        proxy_url=db.get_setting('youtube_proxy_url') or '',
        sb_submit_enabled=db.get_setting_bool('youtube_sb_submit_enabled', d['youtube_sb_submit_enabled']),
    )


def _override(podcast: dict, column: str, default, cast=lambda v: v):
    """Per-feed override value; NULL inherits the default, 0 is a real value."""
    value = podcast.get(column)
    return default if value is None else cast(value)


def resolve_feed(db, podcast: dict | None, base: YouTubeSettings | None = None) -> YouTubeSettings:
    """Global settings with the feed's overrides applied; pass `base` to reuse an already-loaded global."""
    base = base or load_global(db)
    if not podcast:
        return base
    return replace(
        base,
        process_delay_minutes=_override(podcast, 'yt_process_delay_minutes_override', base.process_delay_minutes),
        probe_attempts=_override(podcast, 'yt_probe_attempts_override', base.probe_attempts),
        probe_interval_minutes=_override(podcast, 'yt_probe_interval_minutes_override', base.probe_interval_minutes),
        llm_fallback=_override(podcast, 'yt_llm_fallback_override', base.llm_fallback, bool),
        captions_as_transcript=_override(podcast, 'yt_captions_override', base.captions_as_transcript, bool),
        hide_private=_override(podcast, 'yt_hide_private_override', base.hide_private, bool),
        transcribe_fallback=_override(podcast, 'yt_transcribe_override', base.transcribe_fallback, bool),
    )


def llm_on_top(podcast: dict | None) -> bool:
    return bool(podcast and podcast.get('yt_llm_on_top_override'))


def api_key(db) -> str:
    return db.get_secret('youtube_api_key') or ''


def sb_user_id(db) -> str:
    """The generated ID when set, else the pasted one."""
    return db.get_secret('youtube_sb_user_id_generated') or db.get_secret('youtube_sb_user_id') or ''


def extra_args_valid(value: str) -> bool:
    try:
        ytdlp.parse_extra_args(value)
    except ValueError:
        return False
    return True


def proxy_url_valid(value: str) -> bool:
    try:
        ytdlp.parse_proxy_url(value)
    except ValueError:
        return False
    return True

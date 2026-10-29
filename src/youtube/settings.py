"""Global and per-feed YouTube settings."""
from dataclasses import dataclass, replace

BOUNDS = {
    'processDelayMinutes': (0, 1440),
    'sbProbeAttempts': (0, 48),
    'sbProbeIntervalMinutes': (5, 1440),
    'sbWatchIntervalHours': (1, 48),
    'sbWatchMaxDays': (1, 60),
}


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
    ytdlp_auto_update: bool


def load_global(db) -> YouTubeSettings:
    return YouTubeSettings(
        enabled=db.get_setting_bool('youtube_enabled', False),
        process_delay_minutes=db.get_setting_int('youtube_process_delay_minutes', 60),
        probe_attempts=db.get_setting_int('youtube_sb_probe_attempts', 6),
        probe_interval_minutes=db.get_setting_int('youtube_sb_probe_interval_minutes', 30),
        watch_interval_hours=db.get_setting_int('youtube_sb_watch_interval_hours', 6),
        watch_max_days=db.get_setting_int('youtube_sb_watch_max_days', 7),
        llm_fallback=db.get_setting_bool('youtube_llm_fallback', True),
        captions_as_transcript=db.get_setting_bool('youtube_captions_as_transcript', True),
        ytdlp_auto_update=db.get_setting_bool('youtube_ytdlp_auto_update', True),
    )


def _override(podcast: dict, column: str, default, cast=lambda v: v):
    """Per-feed override value; NULL inherits the default, 0 is a real value."""
    value = podcast.get(column)
    return default if value is None else cast(value)


def resolve_feed(db, podcast: dict | None) -> YouTubeSettings:
    base = load_global(db)
    if not podcast:
        return base
    return replace(
        base,
        process_delay_minutes=_override(podcast, 'yt_process_delay_minutes_override', base.process_delay_minutes),
        probe_attempts=_override(podcast, 'yt_probe_attempts_override', base.probe_attempts),
        probe_interval_minutes=_override(podcast, 'yt_probe_interval_minutes_override', base.probe_interval_minutes),
        llm_fallback=_override(podcast, 'yt_llm_fallback_override', base.llm_fallback, bool),
        captions_as_transcript=_override(podcast, 'yt_captions_override', base.captions_as_transcript, bool),
    )


def llm_on_top(podcast: dict | None) -> bool:
    return bool(podcast and podcast.get('yt_llm_on_top_override'))


def api_key(db) -> str:
    return db.get_secret('youtube_api_key') or ''

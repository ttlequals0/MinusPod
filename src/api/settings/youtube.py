"""YouTube settings: GET/PUT /settings/youtube."""
from flask import request

from api import api, error_response, get_database, get_storage, json_response, log_request
from secrets_crypto import is_available as crypto_available
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from youtube import cookies, settings as yt_settings, ytdlp

_INT_KEYS = {
    'processDelayMinutes': 'youtube_process_delay_minutes',
    'sbProbeAttempts': 'youtube_sb_probe_attempts',
    'sbProbeIntervalMinutes': 'youtube_sb_probe_interval_minutes',
    'sbWatchIntervalHours': 'youtube_sb_watch_interval_hours',
    'sbWatchMaxDays': 'youtube_sb_watch_max_days',
}
_BOOL_KEYS = {
    'enabled': 'youtube_enabled',
    'llmFallback': 'youtube_llm_fallback',
    'captionsAsTranscript': 'youtube_captions_as_transcript',
    'ytdlpAutoUpdate': 'youtube_ytdlp_auto_update',
}


def youtube_status_payload(db) -> dict:
    s = yt_settings.load_global(db)
    storage = get_storage()
    return {
        'enabled': s.enabled,
        'apiKeyConfigured': bool(yt_settings.api_key(db)),
        'processDelayMinutes': s.process_delay_minutes,
        'sbProbeAttempts': s.probe_attempts,
        'sbProbeIntervalMinutes': s.probe_interval_minutes,
        'sbWatchIntervalHours': s.watch_interval_hours,
        'sbWatchMaxDays': s.watch_max_days,
        'llmFallback': s.llm_fallback,
        'captionsAsTranscript': s.captions_as_transcript,
        'ytdlp': {
            'autoUpdate': s.ytdlp_auto_update,
            **ytdlp.status(storage, db),
            'lastUpdateAt': db.get_setting('youtube_ytdlp_last_update_at'),
            'lastCheckAt': db.get_setting('youtube_ytdlp_last_check_at'),
            'lastError': db.get_setting('youtube_ytdlp_last_error'),
        },
        'cookies': cookies.status(storage),
        'bounds': {k: list(v) for k, v in yt_settings.BOUNDS.items()},
    }


@api.route('/settings/youtube', methods=['GET'])
@log_request
def get_youtube_settings():
    return json_response(youtube_status_payload(get_database()))


@api.route('/settings/youtube', methods=['PUT'])
@log_request
def put_youtube_settings():
    data = request.get_json(silent=True) or {}
    writes = []
    for key, db_key in _INT_KEYS.items():
        if key not in data:
            continue
        lo, hi = yt_settings.BOUNDS[key]
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return error_response(f'{key} must be an integer', 400)
        try:
            n = int(value)
        except ValueError:
            return error_response(f'{key} must be an integer', 400)
        if n < lo or n > hi:
            return error_response(f'{key} must be between {lo} and {hi}', 400)
        writes.append((db_key, str(n)))
    for key, db_key in _BOOL_KEYS.items():
        if key in data:
            if not isinstance(data[key], bool):
                return error_response(f'{key} must be a boolean', 400)
            writes.append((db_key, 'true' if data[key] else 'false'))
    if 'apiKey' in data and data['apiKey'] is not None and not isinstance(data['apiKey'], str):
        return error_response('apiKey must be a string or null', 400)
    if data.get('apiKey') and not crypto_available():
        return error_response('provider_crypto_unavailable', 409)
    db = get_database()
    for db_key, value in writes:
        db.set_setting(db_key, value, is_default=False)
    if 'apiKey' in data:
        try:
            set_or_clear_secret(db, 'youtube_api_key', data['apiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
    return json_response(youtube_status_payload(db))

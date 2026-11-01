"""YouTube settings: GET/PUT /settings/youtube, POST /settings/youtube/reset and sb-user-id (GET, generate)."""
import logging
import re
import secrets

from flask import request

from api import api, error_response, get_database, get_storage, json_response, limiter, log_request
from database.settings import YOUTUBE_RESET_SETTING_KEYS
from secrets_crypto import is_available as crypto_available
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from youtube import cookies, settings as yt_settings, ytdlp

logger = logging.getLogger('podcast.api')

_SB_GENERATED_KEY = 'youtube_sb_user_id_generated'
_SB_PASTED_KEY = 'youtube_sb_user_id'
_SB_USER_ID_RE = re.compile(r'[\x21-\x7e]{32,200}')
_BOOL_KEYS = {
    'enabled': 'youtube_enabled',
    'llmFallback': 'youtube_llm_fallback',
    'captionsAsTranscript': 'youtube_captions_as_transcript',
    'hidePrivate': 'youtube_hide_private',
    'transcribeFallback': 'youtube_transcribe_fallback',
    'ytdlpAutoUpdate': 'youtube_ytdlp_auto_update',
    'sbSubmitEnabled': 'youtube_sb_submit_enabled',
}


def youtube_status_payload(db) -> dict:
    s = yt_settings.load_global(db)
    storage = get_storage()
    return {
        'enabled': s.enabled,
        'apiKeyConfigured': bool(yt_settings.api_key(db)),
        'sbSubmitEnabled': s.sb_submit_enabled,
        'sbUserIdConfigured': bool(yt_settings.sb_user_id(db)),
        'sbUserIdGenerated': bool(db.get_secret(_SB_GENERATED_KEY)),
        'processDelayMinutes': s.process_delay_minutes,
        'sbProbeAttempts': s.probe_attempts,
        'sbProbeIntervalMinutes': s.probe_interval_minutes,
        'sbWatchIntervalHours': s.watch_interval_hours,
        'sbWatchMaxDays': s.watch_max_days,
        'llmFallback': s.llm_fallback,
        'captionsAsTranscript': s.captions_as_transcript,
        'hidePrivate': s.hide_private,
        'transcribeFallback': s.transcribe_fallback,
        'proxyUrl': ytdlp.mask_proxy_url(s.proxy_url),
        'ytdlpExtraArgs': s.ytdlp_extra_args,
        'ytdlp': {
            'autoUpdate': s.ytdlp_auto_update,
            **ytdlp.status(db),
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
    db = get_database()
    data = request.get_json(silent=True) or {}
    writes = []
    for key, db_key in yt_settings.INT_KEYS.items():
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
    if 'ytdlpExtraArgs' in data:
        extra_args = data['ytdlpExtraArgs']
        if not isinstance(extra_args, str):
            return error_response('ytdlpExtraArgs must be a string', 400)
        try:
            ytdlp.parse_extra_args(extra_args)
        except ValueError as exc:
            return error_response(str(exc), 400)
        writes.append(('youtube_ytdlp_extra_args', extra_args.strip()))
    if 'proxyUrl' in data:
        if not isinstance(data['proxyUrl'], str):
            return error_response('proxyUrl must be a string', 400)
        try:
            proxy_url = ytdlp.parse_proxy_url(data['proxyUrl'])
        except ValueError as exc:
            return error_response(str(exc), 400)
        stored_proxy = db.get_setting('youtube_proxy_url') or ''
        if proxy_url != ytdlp.mask_proxy_url(stored_proxy):
            try:
                writes.append(('youtube_proxy_url', ytdlp.restore_proxy_password(proxy_url, stored_proxy)))
            except ValueError as exc:
                return error_response(str(exc), 400)
    sb_user_id = data.get('sbUserId')
    if 'sbUserId' in data and sb_user_id is not None and not isinstance(sb_user_id, str):
        return error_response('sbUserId must be a string or null', 400)
    if sb_user_id is not None:
        sb_user_id = sb_user_id.strip()
    if sb_user_id and not _SB_USER_ID_RE.fullmatch(sb_user_id):
        return error_response('sbUserId must be 32 to 200 printable ASCII characters', 400)
    if 'apiKey' in data and data['apiKey'] is not None and not isinstance(data['apiKey'], str):
        return error_response('apiKey must be a string or null', 400)
    if 'sbUserId' in data and db.get_secret(_SB_GENERATED_KEY):
        return error_response('generated', 409)
    if (data.get('apiKey') or sb_user_id) and not crypto_available():
        return error_response('provider_crypto_unavailable', 409)
    for db_key, value in writes:
        db.set_setting(db_key, value, is_default=False)
    if 'apiKey' in data:
        try:
            set_or_clear_secret(db, 'youtube_api_key', data['apiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
    if 'sbUserId' in data:
        try:
            set_or_clear_secret(db, _SB_PASTED_KEY, sb_user_id)
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        if db.get_secret(_SB_GENERATED_KEY):  # a generate landed after the check above; it wins
            db.clear_secret(_SB_PASTED_KEY)
            return error_response('generated', 409)
    return json_response(youtube_status_payload(db))


@api.route('/settings/youtube/sb-user-id', methods=['GET'])
@limiter.limit("10/minute")
@log_request
def get_sb_user_id():
    """Reveal the stored SponsorBlock private user ID."""
    sb_user_id = yt_settings.sb_user_id(get_database())
    if not sb_user_id:
        return error_response('not_configured', 404)
    return json_response({'sbUserId': sb_user_id})


@api.route('/settings/youtube/sb-user-id/generate', methods=['POST'])
@limiter.limit("10/minute")
@log_request
def generate_sb_user_id():
    """Create and store a SponsorBlock private user ID; refuses when one is stored."""
    db = get_database()
    if db.get_secret(_SB_GENERATED_KEY) or db.get_secret(_SB_PASTED_KEY):
        return error_response('configured', 409)
    new_id = secrets.token_hex(24)
    try:
        set_or_clear_secret(db, _SB_GENERATED_KEY, new_id)
    except SecretWriteRejected:
        return error_response('provider_crypto_unavailable', 409)
    db.clear_secret(_SB_PASTED_KEY)  # a concurrent PUT may have written one; the generated key wins
    logger.info('Generated a SponsorBlock user ID')
    return json_response({'sbUserId': new_id})


@api.route('/settings/youtube/reset', methods=['POST'])
@log_request
def reset_youtube_settings():
    """Restore the YouTube tunables, proxy and extra arguments; keep the switch, key, user id and cookies."""
    db = get_database()
    for key in YOUTUBE_RESET_SETTING_KEYS:
        db.reset_setting(key)
    logger.info('Reset YouTube settings to defaults')
    return json_response(youtube_status_payload(db))

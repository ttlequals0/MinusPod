"""Portable runtime configuration export, validation, preview, and apply."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from functools import partial
from urllib.parse import urlsplit

import email_service
import webhook_service
from community_sync import DEFAULT_CRON as COMMUNITY_SYNC_DEFAULT_CRON
from config import (
    PODCAST_SEARCH_PROVIDERS, STAGE_TUNABLE_PAYLOAD_KEYS,
    SYSTEMONE_TUNABLE_DEFAULTS, SYSTEMONE_TUNABLE_PROFILE_KEYS,
    PROCESSING_MODE_CUE_ONLY, cue_only_missing_roles,
    get_stage_tunable, resolve_community_sync_categories,
    resolve_feed_processing_mode, resolve_jit_blocked_user_agents,
    resolve_segment_category_actions_map, resolve_max_ad_duration_confirmed,
    SEARCH_PROVIDER_ITUNES, SEARCH_PROVIDER_PODCASTINDEX,
)
from db_backup_service import (
    DEFAULT_CRON as DB_BACKUP_DEFAULT_CRON,
    KEEP_COUNT_MAX, KEEP_COUNT_MIN, validate_backup_dest,
)
from database.settings import SETTINGS_REGISTRY, registry_default
from systemone.tuning import SystemOneSettingsError, merge_profile, profile_from_snapshot
from fx_rates import FxRateError, get_usd_rate
from llm_route import SAME_AS_DETECTION, VALID_SLOTS
from pattern_cleanup import BATCH_SIZE_RANGE, UNUSED_DAYS_RANGE
from podping_listener import get_podping_nodes, normalize_podping_nodes
from rss_parser import _feed_trust
from rate_limit_hold import clear_hold_for_provider_change
from secrets_crypto import SECRET_SETTING_KEYS, decrypt, is_available, is_ciphertext
from utils.cron import is_valid_expression
from utils.safe_http import _validate_for_tier
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from utils.url import SSRFError
from utils.time import utc_now_iso
from utils.validation import is_dangerous_slug, is_valid_slug
from utils.feed_guid import compute_feed_guid

FORMAT = 'minuspod-runtime-config'
FORMAT_VERSION = 1
MAX_CONFIG_REQUEST_BYTES = 10 * 1024 * 1024
EXCLUDED_SETTINGS = frozenset({
    'pattern_cleanup_schedule_anchor', 'provider_budget_fx_fetched_at',
    'provider_budget_fx_source_date', 'provider_budget_fx_rate',
    'provider_crypto_salt', 'provider_config_revision', 'config_transfer_revision',
})
EXTRA_SETTINGS = frozenset({
    'model_pricing_overrides', 'retention_days', 'original_retention_days',
    'podcast_search_provider', 'community_sync_enabled', 'community_sync_cron',
    'update_check_enabled', 'update_channel',
    'update_patterns_from_reviewer_adjustments', 'min_trim_threshold',
    'email_enabled', 'email_events', 'email_smtp_host', 'email_smtp_port',
    'email_smtp_security', 'email_smtp_username', 'email_smtp_from',
    'email_recipients', 'email_smtp_password', 'webhooks',
    'db_backup_enabled', 'db_backup_cron', 'db_backup_dest',
    'db_backup_keep_count', 'feed_auth_key', 'podping_nodes',
})
PORTABLE_SETTINGS = frozenset(SETTINGS_REGISTRY) | EXTRA_SETTINGS
PORTABLE_SETTINGS -= EXCLUDED_SETTINGS
PATTERN_CLEANUP_SETTINGS = frozenset({
    'pattern_cleanup_enabled', 'pattern_cleanup_cron', 'pattern_cleanup_batch_size',
    'pattern_cleanup_unused_days', 'pattern_cleanup_provider', 'pattern_cleanup_model',
})
RAW_TRANSFER_SETTINGS = PATTERN_CLEANUP_SETTINGS | {'notification_timezone'}
NULLABLE_OPTIONAL_SETTINGS = frozenset({
    'audio_replacement_sound_enabled', 'audio_mp3_stream_copy_enabled',
    'verification_model', 'chapters_model', 'pattern_cleanup_model',
    'secondary_provider', 'failover_llm_provider',
    'llm_timeout_seconds', 'llm_max_retries',
    'secondary_llm_timeout_seconds', 'secondary_llm_max_retries',
    'failover_llm_timeout_seconds', 'failover_llm_max_retries',
    'failover_whisper_max_attempts', 'detection_reasoning_budget',
    'detection_reasoning_level', 'verification_reasoning_budget',
    'verification_reasoning_level', 'reviewer_reasoning_budget',
    'reviewer_reasoning_level', 'chapter_boundary_reasoning_budget',
    'chapter_boundary_reasoning_level', 'chapter_title_reasoning_budget',
    'chapter_title_reasoning_level', 'ollama_num_ctx',
})
STRUCTURED_SETTING_TYPES = {
    'email_events': list,
    'jit_blocked_user_agents': list,
    'model_pricing_overrides': dict,
    'podping_nodes': list,
    'segment_category_actions': dict,
    'community_sync_categories': list,
    'webhooks': list,
    **{key: dict for key in SYSTEMONE_TUNABLE_PROFILE_KEYS.values()},
}

SECRET_ENV = {
    'anthropic_api_key': 'ANTHROPIC_API_KEY',
    'openai_api_key': 'OPENAI_API_KEY',
    'systemone_api_key': 'SYSTEMONE_API_KEY',
    'typesafe_api_key': 'TYPESAFE_API_KEY',
    'openrouter_api_key': 'OPENROUTER_API_KEY',
    'ollama_api_key': 'OLLAMA_API_KEY',
    'secondary_provider_api_key': 'SECONDARY_PROVIDER_API_KEY',
    'whisper_api_key': 'WHISPER_API_KEY',
    'failover_llm_api_key': 'FAILOVER_LLM_API_KEY',
    'failover_whisper_api_key': 'FAILOVER_WHISPER_API_KEY',
    'podcast_index_api_key': 'PODCAST_INDEX_API_KEY',
    'podcast_index_api_secret': 'PODCAST_INDEX_API_SECRET',
    'email_smtp_password': 'EMAIL_SMTP_PASSWORD',
}
BOOLEAN_EXTRA_SETTINGS = frozenset({
    'community_sync_enabled', 'update_check_enabled',
    'update_patterns_from_reviewer_adjustments', 'email_enabled', 'db_backup_enabled',
})

FEED_COLUMNS = (
    'title', 'description', 'source_url', 'network_id', 'dai_platform',
    'network_id_override', 'audio_analysis_override', 'auto_process_override',
    'language_override', 'download_user_agent_override', 'feed_user_agent_override',
    'title_override', 'detection_notes', 'detection_mode',
    'chapters_mode', 'chapters_in_notes', 'own_episode_guids',
    'cue_template_score_override', 'cue_create_from_pairs_override',
    'cue_pair_min_break_override', 'cue_pair_max_break_override',
    'cue_pair_max_break_fraction_override', 'cue_snap_confidence_override',
    'cue_snap_lead_override', 'cue_snap_lag_override', 'silence_snap_enabled',
    'transition_snap_enabled', 'max_ad_duration_override',
    'max_ad_duration_reject_override', 'ad_detection_exclude_start_override',
    'splice_veto_enabled', 'cue_gated_approval', 'differential_fetch_enabled',
    'differential_fetch_mode', 'max_episodes', 'only_expose_processed_episodes',
    'website_url', 'passthrough_enabled', 'skip_ad_detection',
    'segment_category_actions', 'detect_show_segments', 'skip_second_pass',
    'transcript_differential', 'skip_transcription', 'cue_only_safety',
    'queue_priority', 'title_skip_patterns', 'description_skip_patterns',
    'title_skip_action', 'min_duration_seconds', 'max_duration_seconds',
    'low_ad_yield_action', 'episode_logs', 'retention_days_override',
    'keep_original_audio_override', 'user_tags', 'author', 'explicit',
    'audio_replacement_sound_override', 'audio_mp3_stream_copy_override',
    'categories', 'p20_channel_json',
)
FEED_JSON_COLUMNS = frozenset({
    'segment_category_actions', 'title_skip_patterns', 'description_skip_patterns',
    'user_tags', 'categories', 'p20_channel_json',
})
FEED_BOOL_COLUMNS = frozenset({
    'own_episode_guids', 'silence_snap_enabled', 'transition_snap_enabled',
    'splice_veto_enabled', 'cue_gated_approval', 'only_expose_processed_episodes',
    'passthrough_enabled', 'skip_ad_detection', 'detect_show_segments',
    'skip_second_pass', 'transcript_differential', 'skip_transcription',
    'explicit', 'cue_create_from_pairs_override', 'differential_fetch_enabled',
    'keep_original_audio_override',
    'audio_replacement_sound_override', 'audio_mp3_stream_copy_override',
})


class ConfigTransferError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _setting_raw_value(db, key, raw_settings=None):
    raw_settings = raw_settings if raw_settings is not None else db.get_all_settings()
    raw_entry = raw_settings.get(key)
    raw_value = raw_entry.get('value') if isinstance(raw_entry, dict) else None
    if key in {
            'system_prompt_override', 'verification_prompt_override',
            'review_prompt_override', 'resurrect_prompt_override',
            'chapter_prompt_override'}:
        return raw_value or ''
    if key == 'claude_model':
        return raw_value or ''
    if key == 'detection_provider':
        return raw_value or 'primary'
    if key in ('verification_provider', 'chapters_provider'):
        return raw_value or SAME_AS_DETECTION
    if key in {'verification_model', 'chapters_model', 'pattern_cleanup_model',
               'secondary_provider', 'failover_llm_provider'}:
        return raw_value
    if key == 'podping_nodes':
        return get_podping_nodes(db)
    if key == 'model_pricing_overrides':
        return db.get_model_pricing_overrides()
    if key == 'pattern_cleanup_provider':
        return db.get_setting(key) or ''
    if key in {
            'email_enabled', 'email_events', 'email_smtp_host', 'email_smtp_port',
            'email_smtp_security', 'email_smtp_username', 'email_smtp_from',
            'email_recipients'}:
        email_config = email_service.load_email_config(db)
        return {
            'email_enabled': email_config.enabled,
            'email_events': email_config.events,
            'email_smtp_host': email_config.host,
            'email_smtp_port': email_config.port,
            'email_smtp_security': email_config.security,
            'email_smtp_username': email_config.username,
            'email_smtp_from': email_config.from_addr,
            'email_recipients': ', '.join(email_config.recipients),
        }[key]
    if key == 'webhooks':
        return webhook_service.load_webhooks(db)
    if key == 'retention_days':
        return int(db.get_setting('retention_days') or '30')
    if key == 'original_retention_days':
        retention_days = _setting_raw_value(db, 'retention_days', raw_settings)
        return int(db.get_setting('original_retention_days') or retention_days)
    if key == 'min_trim_threshold':
        return db.get_setting_float('min_trim_threshold', default=20.0)
    if key == 'update_patterns_from_reviewer_adjustments':
        return db.get_setting_bool(key, default=True)
    if key == 'community_sync_enabled':
        return db.get_setting_bool(key, default=False)
    if key == 'community_sync_cron':
        return db.get_setting(key) or COMMUNITY_SYNC_DEFAULT_CRON
    if key == 'db_backup_enabled':
        return db.get_setting_bool(key, default=False)
    if key == 'db_backup_cron':
        return db.get_setting(key) or DB_BACKUP_DEFAULT_CRON
    if key == 'db_backup_dest':
        return db.get_setting(key) or ''
    if key == 'db_backup_keep_count':
        return int(db.get_setting(key) or str(KEEP_COUNT_MIN))
    if key == 'update_check_enabled':
        return db.get_setting_bool(key, default=True)
    if key == 'update_channel':
        return db.get_setting(key) or 'stable'
    if key == 'podcast_search_provider':
        explicit = db.get_setting(key)
        if explicit in PODCAST_SEARCH_PROVIDERS:
            return explicit
        api_key = _setting_raw_value(db, 'podcast_index_api_key', raw_settings)
        api_secret = _setting_raw_value(db, 'podcast_index_api_secret', raw_settings)
        return (SEARCH_PROVIDER_PODCASTINDEX if api_key and api_secret
                else SEARCH_PROVIDER_ITUNES)
    spec = SETTINGS_REGISTRY.get(key)
    if spec and spec.stage_tunable:
        return get_stage_tunable(key, settings=raw_settings)
    entry = (raw_settings or {}).get(key)
    value = entry.get('value') if isinstance(entry, dict) else db.get_setting(key)
    if key in SECRET_SETTING_KEYS:
        if value and is_ciphertext(value):
            value = decrypt(db, value)
        if not value:
            value = os.environ.get(SECRET_ENV.get(key, ''), '')
        if key == 'openai_api_key' and value == 'not-needed':
            return None
        return value if value else None
    if key == 'segment_category_actions':
        return resolve_segment_category_actions_map(value)
    if key == 'community_sync_categories':
        return resolve_community_sync_categories(value)
    if key == 'jit_blocked_user_agents':
        return resolve_jit_blocked_user_agents(value)
    if value is None and key in SETTINGS_REGISTRY:
        value = registry_default(key)
    return value


def _current_setting_value(db, key, setting_snapshot):
    value = setting_snapshot.get(key)
    if key in SECRET_SETTING_KEYS and value and is_ciphertext(value):
        return decrypt(db, value)
    return value


def _email_address_invalid(address):
    if any(char in address for char in '\r\n '):
        return True
    _, parsed = parseaddr(address)
    return parsed != address or '@' not in parsed.strip('@')


def _portable_integer(key, value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r'[+-]?\d+', value.strip()):
        return int(value)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ConfigTransferError(f'{key} has an invalid stored integer', 409) from None
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ConfigTransferError(f'{key} has an invalid stored integer', 409)
    return int(numeric)


def _portable_value(key, value):
    if value is None:
        return None
    if key == 'provider_budget_display_currency':
        return str(value).upper()
    if key in SECRET_SETTING_KEYS:
        return str(value)
    if not isinstance(value, str):
        return value
    spec = SETTINGS_REGISTRY.get(key)
    if key in {'llm_timeout_seconds', 'secondary_llm_timeout_seconds'}:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigTransferError(f'{key} has an invalid stored timeout', 409) from None
        if not math.isfinite(number) or number <= 0:
            raise ConfigTransferError(f'{key} has an invalid stored timeout', 409)
        return int(number) if number.is_integer() else number
    structured_type = STRUCTURED_SETTING_TYPES.get(key)
    if structured_type:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if spec and spec.payload_factory:
        contract = spec.payload_factory()
        if isinstance(contract, bool):
            return value.strip().lower() in ('true', '1', 'yes', 'on')
        if isinstance(contract, int):
            return _portable_integer(key, value)
        if isinstance(contract, float):
            return float(value)
        if isinstance(contract, (dict, list)):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            return parsed
    kind = spec.payload_kind if spec else ''
    if key == 'email_smtp_port':
        return _portable_integer(key, value)
    if key in ('retention_days', 'original_retention_days', 'db_backup_keep_count'):
        return _portable_integer(key, value)
    if kind == 'bool':
        return value.strip().lower() in ('true', '1', 'yes', 'on')
    if kind == 'int':
        return _portable_integer(key, value)
    if kind == 'float':
        return float(value)
    if key == 'min_trim_threshold':
        return float(value)
    if key in BOOLEAN_EXTRA_SETTINGS:
        return value.strip().lower() == 'true'
    return value


def _decode_feed_value(column, value):
    if value is None:
        return None
    if column in FEED_JSON_COLUMNS:
        try:
            return json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            raise ConfigTransferError(f'Feed field {column} contains invalid JSON') from None
    if column in FEED_BOOL_COLUMNS:
        return bool(value)
    return value


def export_config(db, app_version):
    settings = {}
    raw_settings = db.get_all_settings()
    for key in sorted(PORTABLE_SETTINGS):
        value = _setting_raw_value(db, key, raw_settings)
        if value is not None or key in NULLABLE_OPTIONAL_SETTINGS:
            settings[key] = _portable_value(key, value)
    cols = ', '.join(('slug', 'feed_type', *FEED_COLUMNS))
    rows = db.get_connection().execute(
        f'SELECT {cols} FROM podcasts ORDER BY slug'  # noqa: S608
    ).fetchall()
    feeds = []
    for row in rows:
        columns = FEED_COLUMNS
        if row['feed_type'] != 'local':
            columns = tuple(column for column in columns if column not in {
                'author', 'explicit', 'categories', 'p20_channel_json',
            })
        if row['feed_type'] == 'recents':
            columns = ('title', 'description', 'source_url')
        feeds.append({
            'slug': row['slug'],
            'feedType': row['feed_type'],
            'settings': {column: _decode_feed_value(column, row[column])
                         for column in columns},
        })
    document = {
        'format': FORMAT,
        'formatVersion': FORMAT_VERSION,
        'appVersion': str(app_version),
        'exportedAt': datetime.now(timezone.utc).isoformat(),
        'containsSecrets': True,
        'settings': settings,
        'feeds': feeds,
    }
    _validate_request_envelope(
        document, 'everything', [feed['slug'] for feed in feeds])
    return document


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _validate_request_envelope(document, scope, selected_feeds=None):
    envelope = {
        'document': document,
        'scope': scope,
        'selectedFeeds': selected_feeds,
        'previewToken': '0' * 64,
    }
    if len(_canonical_json(envelope).encode('utf-8')) > MAX_CONFIG_REQUEST_BYTES:
        raise ConfigTransferError('Configuration transfer exceeds the 10 MiB request limit', 413)


def _validate_document(document):
    if len(_canonical_json(document).encode('utf-8')) > MAX_CONFIG_REQUEST_BYTES:
        raise ConfigTransferError('Configuration file exceeds the 10 MiB request limit', 413)
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise ConfigTransferError('File is not a MinusPod runtime configuration export')
    if set(document) - {'format', 'formatVersion', 'appVersion', 'exportedAt',
                        'containsSecrets', 'settings', 'feeds'}:
        raise ConfigTransferError('File contains unsupported top-level fields')
    version = document.get('formatVersion')
    if isinstance(version, bool) or not isinstance(version, int) or version > FORMAT_VERSION or version < 1:
        raise ConfigTransferError('Unsupported configuration format version')
    settings = document.get('settings', {})
    feeds = document.get('feeds', [])
    if not isinstance(settings, dict) or not isinstance(feeds, list):
        raise ConfigTransferError('settings must be an object and feeds must be an array')
    unknown = sorted(set(settings) - PORTABLE_SETTINGS)
    if any(key in EXCLUDED_SETTINGS or key in {
            'flask_secret_key', 'app_password', 'provider_crypto_salt',
            'provider_config_revision'} for key in settings):
        raise ConfigTransferError('File contains non-transferable runtime or account settings')
    seen = set()
    for feed in feeds:
        if not isinstance(feed, dict) or not isinstance(feed.get('settings', {}), dict):
            raise ConfigTransferError('Each feed must be an object with a settings object')
        slug = feed.get('slug')
        feed_type = feed.get('feedType')
        if is_dangerous_slug(slug):
            raise ConfigTransferError('Feed slug is invalid')
        if slug in seen:
            raise ConfigTransferError('File contains duplicate feed slugs')
        seen.add(slug)
        if feed_type not in ('subscribed', 'local', 'recents'):
            raise ConfigTransferError(f'Unsupported feed type for {slug}')
        fields = feed.get('settings', {})
        if set(fields) - set(FEED_COLUMNS):
            raise ConfigTransferError(f'Feed {slug} contains unsupported settings')
        source_url = fields.get('source_url', '')
        if feed_type == 'subscribed':
            if not isinstance(source_url, str):
                raise ConfigTransferError(f'Feed {slug} needs an HTTP(S) source_url')
            try:
                parsed_url = urlsplit(source_url)
                port = parsed_url.port
            except ValueError:
                raise ConfigTransferError(f'Feed {slug} has an invalid source_url') from None
            if (parsed_url.scheme not in ('http', 'https') or not parsed_url.hostname
                    or (port is not None and not 1 <= port <= 65535)):
                raise ConfigTransferError(f'Feed {slug} needs an HTTP(S) source_url')
        for key, value in fields.items():
            _validate_feed_value(slug, key, value)
        if feed_type == 'local' and (not isinstance(fields.get('title'), str)
                                     or not fields['title'].strip()):
            raise ConfigTransferError(f'Local feed {slug} needs a title')
        if feed_type == 'local' and fields.get('source_url') != f'local://{slug}':
            raise ConfigTransferError(f'Local feed {slug} has an invalid identity')
        if feed_type == 'recents' and set(fields) - {'title', 'description', 'source_url'}:
            raise ConfigTransferError(f'Recents feed {slug} contains unsupported settings')
        if feed_type == 'recents' and fields.get('source_url') != 'recents://':
            raise ConfigTransferError(f'Recents feed {slug} has an invalid identity')
        if feed_type != 'local' and set(fields) & {
                'author', 'explicit', 'categories', 'p20_channel_json'}:
            raise ConfigTransferError(f'Feed {slug} contains local-only metadata')
    for key, value in settings.items():
        if key in PORTABLE_SETTINGS:
            _validate_setting_value(key, value)
    _validate_pattern_cleanup_settings(settings)
    return {'settings': settings, 'feeds': feeds, 'unknown': unknown}


def _validate_pattern_cleanup_settings(settings):
    if 'pattern_cleanup_enabled' in settings and not isinstance(
            settings['pattern_cleanup_enabled'], bool):
        raise ConfigTransferError('pattern_cleanup_enabled must be a boolean')
    if 'pattern_cleanup_cron' in settings:
        cron = settings['pattern_cleanup_cron']
        if not isinstance(cron, str) or not is_valid_expression(cron.strip()):
            raise ConfigTransferError('pattern_cleanup_cron is invalid')
    if 'pattern_cleanup_batch_size' in settings:
        value = settings['pattern_cleanup_batch_size']
        if (not isinstance(value, int) or isinstance(value, bool)
                or not BATCH_SIZE_RANGE[0] <= value <= BATCH_SIZE_RANGE[1]):
            raise ConfigTransferError('pattern_cleanup_batch_size is out of range')
    if 'pattern_cleanup_unused_days' in settings:
        value = settings['pattern_cleanup_unused_days']
        if (not isinstance(value, int) or isinstance(value, bool)
                or not UNUSED_DAYS_RANGE[0] <= value <= UNUSED_DAYS_RANGE[1]):
            raise ConfigTransferError('pattern_cleanup_unused_days is out of range')
    if 'pattern_cleanup_provider' in settings:
        provider = settings['pattern_cleanup_provider']
        if provider is not None and (
                not isinstance(provider, str)
                or provider not in (*VALID_SLOTS, SAME_AS_DETECTION, '', 'a', 'b')):
            raise ConfigTransferError('pattern_cleanup_provider is invalid')
    if 'pattern_cleanup_model' in settings:
        model = settings['pattern_cleanup_model']
        if model is not None and not isinstance(model, str):
            raise ConfigTransferError('pattern_cleanup_model must be a string or null')
        if isinstance(model, str) and len(model.strip()) > 200:
            raise ConfigTransferError('pattern_cleanup_model must be 200 characters or fewer')
    if 'notification_timezone' in settings:
        timezone_value = settings['notification_timezone']
        spec = SETTINGS_REGISTRY['notification_timezone']
        if (not isinstance(timezone_value, str)
                or not spec.validator(timezone_value.strip())):
            raise ConfigTransferError('notification_timezone is invalid')


def _validate_setting_value(key, value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigTransferError(f'{key} must be finite')
    if isinstance(value, (dict, list)):
        _validate_json_tree(value, key)
    if value is None:
        if (key not in SECRET_SETTING_KEYS and key != 'feed_auth_key'
                and key not in NULLABLE_OPTIONAL_SETTINGS
                and key not in SYSTEMONE_TUNABLE_PROFILE_KEYS.values()):
            raise ConfigTransferError(f'{key} cannot be null')
        return
    spec = SETTINGS_REGISTRY.get(key)
    if spec and spec.stage_tunable:
        kind = next(kind for _, setting_key, kind in STAGE_TUNABLE_PAYLOAD_KEYS
                    if setting_key == key)
        if kind == 'float' and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ConfigTransferError(f'{key} must be a number')
        if kind in ('int', 'budget', 'ollama_ctx') and (
                isinstance(value, bool) or not isinstance(value, int)):
            raise ConfigTransferError(f'{key} must be an integer')
        if kind == 'level' and not isinstance(value, str):
            raise ConfigTransferError(f'{key} must be a string')
    if key in STRUCTURED_SETTING_TYPES and not isinstance(value, STRUCTURED_SETTING_TYPES[key]):
        raise ConfigTransferError(f'{key} must be a {STRUCTURED_SETTING_TYPES[key].__name__}')
    if spec:
        if spec.payload_factory:
            contract = spec.payload_factory()
            if isinstance(contract, bool):
                if not isinstance(value, bool):
                    raise ConfigTransferError(f'{key} must be boolean')
            elif isinstance(contract, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConfigTransferError(f'{key} must be an integer')
            elif isinstance(contract, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigTransferError(f'{key} must be a number')
            elif isinstance(contract, str):
                if not isinstance(value, str):
                    raise ConfigTransferError(f'{key} must be a string')
            elif isinstance(contract, (dict, list)) and not isinstance(value, type(contract)):
                raise ConfigTransferError(f'{key} must be a {type(contract).__name__}')
        elif spec.stage_tunable or key == 'podping_nodes':
            pass
        elif spec.payload_kind == 'bool' and not isinstance(value, bool):
            raise ConfigTransferError(f'{key} must be boolean')
        elif spec.payload_kind == 'int' and (isinstance(value, bool) or not isinstance(value, int)):
            raise ConfigTransferError(f'{key} must be an integer')
        elif spec.payload_kind == 'float' and (isinstance(value, bool)
                                               or not isinstance(value, (int, float))):
            raise ConfigTransferError(f'{key} must be a number')
        elif spec.payload_kind == 'str' and not isinstance(value, str):
            raise ConfigTransferError(f'{key} must be a string')
    if spec and spec.validator and not spec.validator(_setting_to_storage(value)):
        if key != 'provider_budget_display_currency':
            raise ConfigTransferError(f'{key} has an invalid value')
    if key == 'provider_budget_display_currency' and (
            not isinstance(value, str) or len(value.strip()) != 3
            or not value.strip().isascii() or not value.strip().isalpha()):
        raise ConfigTransferError('provider_budget_display_currency must be a three-letter currency code')
    if key in SECRET_SETTING_KEYS and not isinstance(value, str):
        raise ConfigTransferError(f'{key} must be a string or null')
    if key in SECRET_SETTING_KEYS and is_ciphertext(value):
        raise ConfigTransferError(f'{key} must contain a decrypted credential value')
    if key in ('podping_nodes', 'email_events') and not isinstance(value, list):
        raise ConfigTransferError(f'{key} must be an array')
    if key == 'model_pricing_overrides' and not isinstance(value, dict):
        raise ConfigTransferError(f'{key} must be an object')
    if key == 'model_pricing_overrides':
        for model_id, rates in value.items():
            if not isinstance(model_id, str) or not model_id.strip():
                raise ConfigTransferError('model_pricing_overrides contains an invalid model')
            if rates is None:
                continue
            if not isinstance(rates, dict):
                raise ConfigTransferError('model_pricing_overrides contains an invalid model')
            if set(rates) != {'inputCostPerMtok', 'outputCostPerMtok'}:
                raise ConfigTransferError('model_pricing_overrides contains invalid rate fields')
            for rate in rates.values():
                if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                        or not math.isfinite(rate) or rate < 0):
                    raise ConfigTransferError('model_pricing_overrides rates must be finite and non-negative')
    if key == 'webhooks':
        _validate_webhooks(value)
    if key == 'feed_auth_key' and (not isinstance(value, str)
                                   or not re.fullmatch(r'[0-9a-f]{64}', value)):
        raise ConfigTransferError('feed_auth_key has an invalid format')
    if key in ('db_backup_cron', 'community_sync_cron'):
        if not isinstance(value, str) or not is_valid_expression(value):
            raise ConfigTransferError(f'{key} is not a valid cron expression')
    if key == 'db_backup_dest' and not isinstance(value, str):
        raise ConfigTransferError('db_backup_dest must be a string')
    if key == 'email_smtp_port' and (not isinstance(value, int) or isinstance(value, bool)
                                     or not 1 <= value <= 65535):
        raise ConfigTransferError('email_smtp_port must be between 1 and 65535')
    if key == 'email_smtp_security':
        if value not in email_service.VALID_SECURITY:
            raise ConfigTransferError('email_smtp_security is invalid')
    if key == 'email_events':
        if any(not isinstance(event, str) or event not in webhook_service.VALID_EVENTS for event in value):
            raise ConfigTransferError('email_events contains an unsupported event')
    if key == 'email_recipients' and not isinstance(value, str):
        raise ConfigTransferError('email_recipients must be a string')
    if key == 'email_smtp_host' and (not isinstance(value, str)
                                     or any(char in value for char in '\r\n ')):
        raise ConfigTransferError('email_smtp_host is invalid')
    if key == 'email_smtp_from':
        if not isinstance(value, str) or (value and (
                any(char in value for char in '\r\n ') or parseaddr(value)[1] != value
                or '@' not in value.strip('@'))):
            raise ConfigTransferError('email_smtp_from is not a valid email address')
    if key == 'email_smtp_username' and (
            not isinstance(value, str) or any(char in value for char in '\r\n')):
        raise ConfigTransferError('email_smtp_username must be a string without line breaks')
    if key == 'email_recipients':
        recipients = email_service.parse_recipients(value)
        if any(_email_address_invalid(address) for address in recipients):
            raise ConfigTransferError('email_recipients contains an invalid address')
    if key in ('retention_days', 'original_retention_days'):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigTransferError(f'{key} must be an integer')
        if not 0 <= value <= 3650:
            raise ConfigTransferError(f'{key} must be between 0 and 3650')
    if key == 'db_backup_keep_count':
        if (isinstance(value, bool) or not isinstance(value, int)
                or not KEEP_COUNT_MIN <= value <= KEEP_COUNT_MAX):
            raise ConfigTransferError('db_backup_keep_count is out of range')
    if key == 'update_channel' and value not in ('stable', 'edge'):
        raise ConfigTransferError('update_channel must be stable or edge')
    if key == 'podcast_search_provider':
        if value not in PODCAST_SEARCH_PROVIDERS:
            raise ConfigTransferError('podcast_search_provider is invalid')
    if key == 'min_trim_threshold' and (isinstance(value, bool)
                                        or not isinstance(value, (int, float))
                                        or not 0 < value <= 120):
        raise ConfigTransferError('min_trim_threshold must be greater than 0 and at most 120')
    if key in BOOLEAN_EXTRA_SETTINGS and not isinstance(value, bool):
        raise ConfigTransferError(f'{key} must be boolean')


def _validate_json_tree(value, key):
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigTransferError(f'{key} must contain finite numbers')
    if isinstance(value, dict):
        for child in value.values():
            _validate_json_tree(child, key)
    elif isinstance(value, list):
        for child in value:
            _validate_json_tree(child, key)


def _validate_webhooks(webhooks):
    if not isinstance(webhooks, list) or len(webhooks) > 10:
        raise ConfigTransferError('webhooks must be an array with at most 10 entries')
    seen = set()
    for webhook in webhooks:
        if not isinstance(webhook, dict):
            raise ConfigTransferError('Each webhook must be an object')
        if set(webhook) - {'id', 'url', 'events', 'secret', 'enabled', 'payloadTemplate', 'contentType'}:
            raise ConfigTransferError('Webhook contains unsupported fields')
        if not isinstance(webhook.get('id'), str) or not webhook['id'] or webhook['id'] in seen:
            raise ConfigTransferError('Webhook IDs must be unique strings')
        seen.add(webhook['id'])
        url = webhook.get('url')
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            port = parsed.port if parsed else None
        except ValueError:
            parsed, port = None, None
        if (not parsed or parsed.scheme not in ('https', 'http') or not parsed.hostname
                or parsed.username or parsed.password or (port is not None and not 1 <= port <= 65535)):
            raise ConfigTransferError('Webhook URL must be an HTTP(S) URL without credentials')
        events = webhook.get('events')
        if (not isinstance(events, list) or not events
                or any(event not in webhook_service.VALID_EVENTS for event in events)):
            raise ConfigTransferError('Webhook events are invalid')
        if not isinstance(webhook.get('enabled', True), bool):
            raise ConfigTransferError('Webhook enabled must be boolean')
        if 'secret' in webhook and webhook['secret'] is not None and not isinstance(webhook['secret'], str):
            raise ConfigTransferError('Webhook secret must be a string or null')
        template = webhook.get('payloadTemplate')
        if template is not None:
            if not isinstance(template, str):
                raise ConfigTransferError('Webhook payloadTemplate must be a string or null')
            try:
                webhook_service.render_template_preview(template)
            except Exception:
                raise ConfigTransferError('Webhook payloadTemplate is invalid') from None
def _validate_feed_value(slug, key, value):
    if value is None:
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigTransferError(f'{slug}.{key} must be finite')
    if isinstance(value, bool) and key not in FEED_BOOL_COLUMNS:
        raise ConfigTransferError(f'{slug}.{key} has an invalid type')
    if key in FEED_JSON_COLUMNS:
        if not isinstance(value, (dict, list, str)):
            raise ConfigTransferError(f'{slug}.{key} has an invalid type')
    elif key in FEED_BOOL_COLUMNS and not isinstance(value, bool):
        raise ConfigTransferError(f'{slug}.{key} must be boolean or null')
    elif key == 'source_url' and not isinstance(value, str):
        raise ConfigTransferError(f'{slug}.source_url must be a string')
    elif key not in FEED_JSON_COLUMNS | FEED_BOOL_COLUMNS and not isinstance(value, (str, int, float)):
        raise ConfigTransferError(f'{slug}.{key} has an invalid type')


def _setting_to_storage(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (dict, list)):
        return _canonical_json(value)
    if value is None:
        return ''
    return str(value)


def _destination_snapshot(db, settings, feeds):
    raw_settings = db.get_all_settings()
    setting_snapshot = {
        key: raw_settings.get(key, {}).get('value') for key in settings
    }
    setting_snapshot['config_transfer_revision'] = raw_settings.get(
        'config_transfer_revision', {}).get('value')
    feed_snapshot = {slug: None for slug in feeds}
    if feeds:
        placeholders = ', '.join('?' for _ in feeds)
        rows = db.get_connection().execute(
            f"SELECT slug, feed_type, {', '.join(FEED_COLUMNS)} FROM podcasts "  # noqa: S608
            f"WHERE slug IN ({placeholders})",
            tuple(feeds),
        ).fetchall()
        feed_snapshot.update({row['slug']: _canonical_json(dict(row)) for row in rows})
    return setting_snapshot, feed_snapshot


def _resolve_scope(plan, scope, selected_feeds):
    if scope not in ('everything', 'global', 'feeds'):
        raise ConfigTransferError('scope must be everything, global, or feeds')
    feeds = plan['feeds'] if scope in ('everything', 'feeds') else []
    if scope == 'feeds':
        if selected_feeds is None:
            selected = {feed['slug'] for feed in feeds}
        else:
            if not isinstance(selected_feeds, list) or any(not isinstance(s, str) for s in selected_feeds):
                raise ConfigTransferError('selectedFeeds must be an array of feed slugs')
            selected = set(selected_feeds)
            unknown = selected - {feed['slug'] for feed in feeds}
            if unknown:
                raise ConfigTransferError('Selection contains feeds absent from the file')
        feeds = [feed for feed in feeds if feed['slug'] in selected]
    settings = ({key: value for key, value in plan['settings'].items()
                 if key in PORTABLE_SETTINGS}
                if scope in ('everything', 'global') else {})
    return settings, feeds


def _normalize_import_settings(settings, db=None):
    from api import settings as settings_api

    normalized = dict(settings)
    snapshot = db.get_all_settings() if db is not None else {}
    for (slot, provider), key in SYSTEMONE_TUNABLE_PROFILE_KEYS.items():
        if key not in normalized:
            continue
        try:
            value = normalized[key]
            defaults = SYSTEMONE_TUNABLE_DEFAULTS[provider]
            normalized[key] = (dict(defaults) if value is None else merge_profile(
                defaults, profile_from_snapshot(snapshot, slot, provider), value))
        except SystemOneSettingsError as exc:
            raise ConfigTransferError(f'{key} is invalid: {exc}') from None
    if 'model_pricing_overrides' in normalized:
        pricing, issue = settings_api._model_pricing_patch(normalized['model_pricing_overrides'])
        if issue:
            raise ConfigTransferError(*issue)
        normalized['model_pricing_overrides'] = {
            model: rates for model, rates in pricing.items() if rates is not None}
    if 'pattern_cleanup_cron' in normalized:
        normalized['pattern_cleanup_cron'] = normalized['pattern_cleanup_cron'].strip()
    if 'pattern_cleanup_provider' in normalized:
        normalized['pattern_cleanup_provider'] = {
            'a': 'primary', 'b': 'secondary',
        }.get(normalized['pattern_cleanup_provider'], normalized['pattern_cleanup_provider'])
    if 'pattern_cleanup_model' in normalized and normalized['pattern_cleanup_model'] is not None:
        normalized['pattern_cleanup_model'] = normalized['pattern_cleanup_model'].strip()
    if 'notification_timezone' in normalized:
        normalized['notification_timezone'] = normalized['notification_timezone'].strip()
    if 'provider_budget_display_currency' in normalized:
        normalized['provider_budget_display_currency'] = normalized[
            'provider_budget_display_currency'].upper()
    if 'podping_nodes' in normalized:
        try:
            normalized['podping_nodes'] = normalize_podping_nodes(normalized['podping_nodes'])
        except ValueError as exc:
            raise ConfigTransferError(f'podping_nodes is invalid: {exc}') from None
    return normalized


def _validate_import_feed_urls(feeds):
    trust = _feed_trust()
    for feed in feeds:
        if feed['feedType'] != 'subscribed':
            continue
        source_url = feed['settings'].get('source_url', '')
        try:
            _validate_for_tier(source_url, trust)
        except SSRFError as exc:
            raise ConfigTransferError(
                f"Feed {feed['slug']} source URL failed security validation: {exc}") from None


def build_preview(db, document, scope, selected_feeds=None):
    _validate_request_envelope(document, scope, selected_feeds)
    plan = _validate_document(document)
    settings, feeds = _resolve_scope(plan, scope, selected_feeds)
    settings = _normalize_import_settings(settings, db)
    _validate_retention_pair(db, settings)
    if any(key.startswith('provider_budget_') for key in settings):
        enabled = settings.get('provider_budget_enabled', db.get_setting('provider_budget_enabled'))
        action = settings.get('provider_budget_unknown_cost', db.get_setting('provider_budget_unknown_cost'))
        reserve = settings.get('provider_budget_unknown_reserve_microusd',
                               db.get_setting('provider_budget_unknown_reserve_microusd') or '0')
        if str(enabled).lower() == 'true' and action == 'reserve' and int(reserve) == 0:
            raise ConfigTransferError('provider_budget_unknown_reserve_microusd must be positive for reserve')
    issue = _settings_validation_error(db, _setting_payload(settings), feeds)
    if issue:
        raise issue
    if 'db_backup_dest' in settings:
        try:
            validate_backup_dest(settings['db_backup_dest'], db.data_dir)
        except ValueError as exc:
            raise ConfigTransferError(f'db_backup_dest is invalid: {exc}') from None
    if settings.get('email_enabled') is True:
        current_email = email_service.load_email_config(db)
        host = settings.get('email_smtp_host', current_email.host)
        from_addr = settings.get('email_smtp_from', current_email.from_addr)
        recipients_raw = settings.get('email_recipients', ', '.join(current_email.recipients))
        recipients = email_service.parse_recipients(recipients_raw)
        if not (host and from_addr and recipients):
            raise ConfigTransferError('Enabled email notifications require an SMTP host, from address, and recipient')
    setting_snapshot, feed_snapshot = _destination_snapshot(
        db, settings, [feed['slug'] for feed in feeds])
    changes = []
    warnings = []
    for key, value in settings.items():
        current = _current_setting_value(db, key, setting_snapshot)
        clears_row = (value is None or value == '' and key in (
            SECRET_SETTING_KEYS | {'pattern_cleanup_provider', 'secondary_provider',
                                   'failover_llm_provider'}))
        desired = None if clears_row else _setting_to_storage(value)
        if current != desired:
            changes.append({'kind': 'setting', 'key': key, 'secret': key in SECRET_SETTING_KEYS})
    added, updated = [], []
    confirmed_ceiling = settings.get(
        'max_ad_duration_confirmed_seconds', resolve_max_ad_duration_confirmed(db))
    for feed in feeds:
        slug = feed['slug']
        current = db.get_connection().execute(
            f"SELECT slug, feed_type, {', '.join(FEED_COLUMNS)} "  # noqa: S608
            "FROM podcasts WHERE slug = ?",
            (slug,),
        ).fetchone()
        if current is None:
            if feed['feedType'] == 'recents':
                raise ConfigTransferError('A recents feed must already exist on the destination')
            if not is_valid_slug(slug):
                raise ConfigTransferError(f'Feed {slug} cannot be created with this slug')
            added.append(slug)
        else:
            if current['feed_type'] != feed['feedType']:
                raise ConfigTransferError(f'Feed identity conflict for {slug}')
            source = feed.get('settings', {}).get('source_url', '')
            if current['source_url'] != source:
                raise ConfigTransferError(f'Feed source conflict for {slug}')
            updated.append(slug)
        source = feed.get('settings', {}).get('source_url', '')
        if feed['feedType'] == 'subscribed':
            duplicate = db.get_connection().execute(
                'SELECT slug FROM podcasts WHERE source_url = ? AND slug != ? LIMIT 1',
                (source, slug),
            ).fetchone()
            if duplicate:
                raise ConfigTransferError('Source URL already belongs to another feed')
        if feed.get('settings', {}).get('detection_mode') == 'cue_only':
            cue_templates = []
            if current is not None:
                podcast = db.get_podcast_by_slug(slug)
                cue_templates = db.list_cue_templates_for_feed_ui(podcast['id']) if podcast else []
            if cue_only_missing_roles(cue_templates):
                warnings.append(f'{slug} uses cue-only processing and needs enabled start and end cue templates on the destination.')
        _validated_feed_updates(
            db, dict(current) if current is not None else None,
            feed['settings'], feed['feedType'], confirmed_ceiling=confirmed_ceiling)
    token_input = {
        'document': document,
        'scope': scope,
        'selected': sorted(feed['slug'] for feed in feeds),
        'destination': [setting_snapshot, feed_snapshot],
    }
    digest = hashlib.sha256(_canonical_json(token_input).encode('utf-8')).hexdigest()
    return {
        'previewToken': digest,
        'scope': scope,
        'settings': [key for key in settings],
        'changedSettings': changes,
        'addedFeeds': added,
        'updatedFeeds': updated,
        'skippedUnknownSettings': plan['unknown'],
        'preservesTargetOnlyFeeds': True,
        'warning': 'The JSON file contains configured credentials and private feed URLs. It excludes administrator sign-in credentials and media assets.',
        'warnings': warnings,
        'selectedFeedSlugs': [feed['slug'] for feed in feeds],
    }


def _validated_feed_updates(db, podcast, payload, feed_type, *, confirmed_ceiling=None):
    from api import feeds as feed_api

    float_columns = {
        column: (api_field, lo, hi)
        for api_field, column, lo, hi in feed_api._CUE_FLOAT_OVERRIDE_FIELDS
    }
    updates = {}
    for key, value in payload.items():
        if key == 'p20_channel_json' and value is not None:
            if isinstance(value, (dict, list)):
                if not isinstance(value, dict):
                    raise ConfigTransferError('p20_channel_json must be an object or null')
                p20_value = dict(value)
            else:
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    raise ConfigTransferError('p20_channel_json must contain valid JSON') from None
                if not isinstance(parsed, dict):
                    raise ConfigTransferError('p20_channel_json must contain an object')
                p20_value = parsed
            guid = p20_value.pop('guid', None)
            p20_value, error = feed_api._validate_p20(p20_value)
            if error:
                raise ConfigTransferError(f'{key} is invalid: {error}')
            if guid is not None:
                if not isinstance(guid, str) or not feed_api._P20_FEED_GUID_RE.fullmatch(guid):
                    raise ConfigTransferError('p20 channel guid is invalid')
                p20_value['guid'] = guid
            value = _canonical_json(p20_value)
        elif key == 'categories' and value is not None:
            value, error = feed_api._validate_local_categories(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'user_tags' and value is not None:
            if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
                raise ConfigTransferError('user_tags must be an array of strings or null')
            value = _canonical_json(value)
        elif key in ('cue_create_from_pairs_override', 'differential_fetch_enabled',
                     'keep_original_audio_override', 'audio_replacement_sound_override',
                     'audio_mp3_stream_copy_override'):
            value, error = feed_api._normalize_cue_bool_override(value, key)
            if error:
                raise ConfigTransferError(error)
        elif key in FEED_JSON_COLUMNS and key not in (
                'title_skip_patterns', 'description_skip_patterns',
                'segment_category_actions') and value is not None:
            value = _canonical_json(value) if not isinstance(value, str) else value
        elif key in FEED_BOOL_COLUMNS and value is not None:
            value = int(value)
        if key == 'max_episodes':
            value, error = feed_api._normalize_max_episodes(value, feed_type in ('local', 'recents'))
            if error:
                raise ConfigTransferError(error)
        elif key == 'retention_days_override':
            value, error = feed_api._validate_retention_override(value)
            if error:
                raise ConfigTransferError(error)
        elif key in ('queue_priority', 'own_episode_guids') and value is not None:
            if key == 'queue_priority' and (isinstance(value, bool) or value not in (-10, 0, 10)):
                raise ConfigTransferError('queue_priority must be -10, 0, 10, or null')
        elif key in ('language_override', 'download_user_agent_override', 'feed_user_agent_override',
                     'title_override', 'detection_notes'):
            normalizer = {
                'language_override': feed_api._normalize_language_override,
                'download_user_agent_override': feed_api._normalize_download_user_agent_override,
                'feed_user_agent_override': feed_api._normalize_feed_user_agent_override,
                'title_override': feed_api._normalize_title_override,
                'detection_notes': feed_api._normalize_detection_notes,
            }[key]
            value, error = normalizer(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'detection_mode':
            value, error = feed_api._normalize_detection_mode(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'chapters_mode':
            value, error = feed_api._normalize_chapters_mode(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'chapters_in_notes':
            value, error = feed_api._normalize_override(
                value, feed_api.CHAPTERS_IN_NOTES_VALUES, 'chaptersInNotes')
            if error:
                raise ConfigTransferError(error)
        elif key == 'title_skip_patterns':
            value, error = feed_api._normalize_title_skip_patterns(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'description_skip_patterns':
            value, error = feed_api._normalize_description_skip_patterns(value)
            if error:
                raise ConfigTransferError(error)
        elif key in ('min_duration_seconds', 'max_duration_seconds'):
            field = 'minDurationSeconds' if key == 'min_duration_seconds' else 'maxDurationSeconds'
            value, error = feed_api._normalize_duration_limit(value, field)
            if error:
                raise ConfigTransferError(error)
        elif key == 'title_skip_action':
            value, error = feed_api._normalize_title_skip_action(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'segment_category_actions':
            value, error = feed_api._normalize_segment_category_actions(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'low_ad_yield_action':
            value, error = feed_api._normalize_low_ad_yield_action(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'episode_logs':
            value, error = feed_api._normalize_episode_logs(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'cue_only_safety':
            value, error = feed_api._normalize_cue_only_safety(value)
            if error:
                raise ConfigTransferError(error)
        elif key == 'differential_fetch_mode':
            if value not in (None, 'auto', 'on', 'off'):
                raise ConfigTransferError('differential_fetch_mode is invalid')
        elif key in ('title', 'description', 'author', 'network_id', 'dai_platform',
                     'network_id_override', 'website_url', 'auto_process_override'):
            if value is not None and not isinstance(value, str):
                raise ConfigTransferError(f'{key} must be a string or null')
            if key == 'auto_process_override' and value not in (None, 'true', 'false', '1', '0'):
                raise ConfigTransferError('auto_process_override is invalid')
        if key in float_columns:
            api_field, lo, hi = float_columns[key]
            value, error = feed_api._normalize_cue_float_override(value, api_field, lo, hi)
            if error:
                raise ConfigTransferError(error)
            if (key == 'ad_detection_exclude_start_override'
                    and value is not None and 0 < value < 1):
                raise ConfigTransferError(
                    'ad_detection_exclude_start_override must be 0 or between 1 and 600')
            if key == 'max_ad_duration_reject_override' and value is not None:
                ceiling = (confirmed_ceiling if confirmed_ceiling is not None
                           else resolve_max_ad_duration_confirmed(db))
                if value > ceiling:
                    raise ConfigTransferError(
                        'max_ad_duration_reject_override exceeds the global limit')
        if key in FEED_BOOL_COLUMNS and value is not None and not isinstance(value, int):
            raise ConfigTransferError(f'{key} must be boolean or null')
        if key in ('source_url', 'title') and value is not None and not str(value).strip():
            raise ConfigTransferError(f'{key} cannot be empty')
        updates[key] = value
    range_error = feed_api._validate_duration_range({**(podcast or {}), **updates})
    if range_error:
        raise ConfigTransferError(range_error)
    if updates.get('skip_transcription'):
        effective = {**(podcast or {}), **updates}
        if resolve_feed_processing_mode(effective) != PROCESSING_MODE_CUE_ONLY:
            raise ConfigTransferError('skip_transcription requires cue_only processing mode')
    if podcast and 'source_url' in updates and updates['source_url'] != podcast['source_url']:
        raise ConfigTransferError('Import cannot repoint an existing feed')
    return updates


def _setting_payload(settings):
    payload = {}
    for key, value in settings.items():
        spec = SETTINGS_REGISTRY.get(key)
        if (key not in RAW_TRANSFER_SETTINGS and spec and spec.payload_key
                and not (value is None and key in ('verification_model', 'chapters_model'))):
            payload[spec.payload_key] = value
    secret_payload_keys = {
        'typesafe_api_key': 'typesafeApiKey',
        'systemone_api_key': 'systemoneApiKey',
        'openrouter_api_key': 'openrouterApiKey',
        'secondary_provider_api_key': 'secondaryProviderApiKey',
        'whisper_api_key': 'whisperApiKey',
        'failover_llm_api_key': 'failoverLlmApiKey',
        'failover_whisper_api_key': 'failoverWhisperApiKey',
        'podcast_index_api_key': 'podcastIndexApiKey',
    }
    for setting_key, payload_key in secret_payload_keys.items():
        if setting_key in settings:
            payload[payload_key] = settings[setting_key]
    for payload_key, setting_key in (
            ('detectionProvider', 'detection_provider'),
            ('verificationProvider', 'verification_provider'),
            ('chaptersProvider', 'chapters_provider')):
        if setting_key in settings:
            payload[payload_key] = settings[setting_key] or ''
    if settings.get('model_pricing_overrides') is not None:
        payload['modelPricingOverrides'] = settings['model_pricing_overrides']
    for payload_key, setting_key, _kind in STAGE_TUNABLE_PAYLOAD_KEYS:
        if setting_key in settings:
            payload[payload_key] = settings[setting_key]
    profiles = {}
    for (slot, provider), key in SYSTEMONE_TUNABLE_PROFILE_KEYS.items():
        if key in settings:
            profiles.setdefault(slot, {})[provider] = settings[key]
    if profiles:
        payload['systemOneTunables'] = profiles
    return payload


def _settings_validation_error(db, payload, feeds=None):
    from api import settings as settings_api
    feed_modes = {
        feed['slug']: feed.get('settings', {}).get('chapters_mode')
        for feed in (feeds or ()) if 'chapters_mode' in feed.get('settings', {})
    }
    if not payload and not feed_modes:
        return None
    issue = settings_api.validate_settings_payload(
        db, payload, allow_inactive_tunables=True,
        prospective_feed_modes=feed_modes)
    if issue is not None:
        return ConfigTransferError(*issue)
    return None


def _validate_retention_pair(db, settings):
    if not {'retention_days', 'original_retention_days'} & settings.keys():
        return
    try:
        stored_retention = int(db.get_setting('retention_days') or '30')
        stored_original = db.get_setting('original_retention_days')
        retention = settings.get('retention_days', stored_retention)
        original = settings.get(
            'original_retention_days',
            int(stored_original) if stored_original else retention)
    except (TypeError, ValueError):
        raise ConfigTransferError('Destination retention settings are invalid', 409) from None
    if retention > 0 and original > retention:
        raise ConfigTransferError(
            'original_retention_days must not exceed retention_days when retention is enabled')


def _phase_applied_setting_keys(settings):
    managed = {
        key for key, value in settings.items()
        if (spec := SETTINGS_REGISTRY.get(key))
        and spec.payload_key and key not in RAW_TRANSFER_SETTINGS
        and not (value is None and key in ('verification_model', 'chapters_model'))
    }
    managed.update({
        key for key in (
            'openrouter_api_key', 'secondary_provider_api_key', 'whisper_api_key',
            'failover_llm_api_key', 'failover_whisper_api_key', 'podcast_index_api_key',
        ) if key in settings
    })
    managed.update({
        key for key in ('detection_provider', 'verification_provider', 'chapters_provider')
        if settings.get(key) is not None
    })
    managed.update({
        key for key in settings
        if SETTINGS_REGISTRY.get(key) and SETTINGS_REGISTRY[key].stage_tunable
    })
    managed.update(key for key in settings if key in SYSTEMONE_TUNABLE_PROFILE_KEYS.values())
    managed.update(key for key in settings if key in (
        'systemone_base_url', 'typesafe_api_key', 'systemone_api_key'))
    return managed


def apply_config(db, document, scope, selected_feeds, preview_token):
    from api import settings as settings_api

    plan = _validate_document(document)
    settings, feeds = _resolve_scope(plan, scope, selected_feeds)
    _validate_request_envelope(document, scope, selected_feeds)
    reset_profiles = {key for key in SYSTEMONE_TUNABLE_PROFILE_KEYS.values()
                      if key in settings and settings[key] is None}
    settings = _normalize_import_settings(settings, db)
    _validate_import_feed_urls(feeds)
    payload = _setting_payload({key: None if key in reset_profiles else value
                                for key, value in settings.items()})
    issue = _settings_validation_error(db, payload, feeds)
    if issue:
        raise issue
    endpoint_issue = settings_api.validate_provider_endpoint_security(payload)
    if endpoint_issue:
        raise ConfigTransferError(*endpoint_issue)
    if any(key in SECRET_SETTING_KEYS and value for key, value in settings.items()) and not is_available():
        raise ConfigTransferError('Destination secret encryption is unavailable', 409)
    fx_rate = None
    if 'provider_budget_display_currency' in settings:
        try:
            fx_rate = get_usd_rate(settings['provider_budget_display_currency'])
        except FxRateError as exc:
            raise ConfigTransferError(str(exc), 503) from exc
    _deferred = {name: [] for name in settings_api._POST_COMMIT_BUCKETS}
    settings_api._deferred.buckets = _deferred
    changed_stages = []
    previous_calibration = None
    changed_feeds = []
    added_feeds = []
    priority_changes = []
    refresh_slugs = set()
    rss_fields = {
        'max_episodes', 'only_expose_processed_episodes', 'title_override',
        'source_url', 'own_episode_guids', 'title_skip_patterns',
        'description_skip_patterns',
        'title_skip_action', 'min_duration_seconds', 'max_duration_seconds',
        'title', 'author', 'explicit', 'categories',
        'p20_channel_json', 'description', 'chapters_in_notes',
    }
    try:
        with db.settings_transaction() as conn:
            current = build_preview(db, document, scope, selected_feeds)
            if current['previewToken'] != preview_token:
                raise ConfigTransferError('Destination changed since preview; preview again', 409)
            previous_identities = settings_api._model_identity_snapshot(db)
            model_settings = {
                'detection': 'claude_model', 'review': 'review_model',
                'verification': 'verification_model', 'chapters': 'chapters_model',
            }
            old_models = {stage: db.get_setting(key) for stage, key in model_settings.items()
                          if key in settings}
            if old_models and previous_calibration is None:
                previous_calibration = settings_api.calibration_revision()
            if payload:
                changed_stages, previous_calibration = settings_api.apply_settings_payload_in_transaction(
                    db, payload, allow_inactive_tunables=True)
            phase_applied_setting_keys = _phase_applied_setting_keys(settings)
            primary_credentials = {
                'anthropic_api_key': 'anthropic',
                'openai_api_key': 'openai-compatible',
                'ollama_api_key': 'ollama',
                'typesafe_api_key': 'typesafe',
                'systemone_api_key': 'systemone-compatible',
            }
            for key, value in settings.items():
                if key in phase_applied_setting_keys:
                    continue
                if (key == 'pattern_cleanup_enabled' and value is True
                        and not db.get_setting_bool(key, default=False)):
                    db.set_setting('pattern_cleanup_schedule_anchor', utc_now_iso(), is_default=False)
                if key in SECRET_SETTING_KEYS:
                    old_secret = db.get_setting(key)
                    if old_secret and is_ciphertext(old_secret):
                        old_secret = decrypt(db, old_secret)
                    try:
                        set_or_clear_secret(db, key, value)
                    except SecretWriteRejected:
                        raise ConfigTransferError('Destination secret encryption is unavailable', 409) from None
                    if key in primary_credentials and (old_secret or '') != (value or ''):
                        settings_api._after_commit(
                            partial(clear_hold_for_provider_change, db, 'Provider credentials restored',
                                    provider_key=primary_credentials[key]), bucket='holds')
                elif key == 'pattern_cleanup_provider' and value == '':
                    db.clear_setting(key)
                elif value is None:
                    db.clear_setting(key)
                else:
                    db.set_setting(key, _setting_to_storage(value), is_default=False)
            if fx_rate is not None:
                for key, value in {
                    'provider_budget_display_currency': fx_rate.currency,
                    'provider_budget_fx_rate': str(fx_rate.local_per_usd),
                    'provider_budget_fx_source_date': fx_rate.source_date,
                    'provider_budget_fx_fetched_at': utc_now_iso(),
                }.items():
                    if value is None:
                        db.clear_setting(key)
                    else:
                        db.set_setting(key, value, is_default=False)
            if any(key in settings for key in (
                    'anthropic_api_key', 'openai_api_key', 'ollama_api_key',
                    'typesafe_api_key', 'systemone_api_key', 'systemone_base_url')):
                settings_api._after_commit(settings_api.invalidate_provider_cache)
            null_keys = {key for key, value in settings.items() if value is None}
            if null_keys & {'llm_provider', 'openai_base_url', 'claude_model',
                            'verification_model', 'review_model', 'chapters_model',
                            'detection_provider', 'verification_provider', 'chapters_provider',
                            'anthropic_api_key', 'openai_api_key', 'openrouter_api_key',
                            'ollama_api_key', 'secondary_provider_api_key', 'failover_llm_api_key',
                            'typesafe_api_key', 'systemone_api_key', 'systemone_base_url'}:
                settings_api._after_commit(settings_api.invalidate_provider_cache)
            if null_keys & {'whisper_model', 'whisper_backend', 'whisper_api_base_url',
                            'whisper_api_key', 'whisper_api_model', 'failover_whisper_api_key'}:
                settings_api._after_commit(settings_api._mark_whisper_for_reload)
                settings_api._after_commit(settings_api._refresh_whisper_pool)
            if null_keys & {'download_user_agent', 'feed_user_agent'}:
                settings_api._after_commit(settings_api.invalidate_user_agent_cache)
            if 'feed_auth_key' in settings:
                settings_api._after_commit(db.clear_all_podcast_etags)
            for feed in feeds:
                slug = feed['slug']
                current_row = conn.execute(
                    f"SELECT slug, id, feed_type, {', '.join(FEED_COLUMNS)} "  # noqa: S608
                    "FROM podcasts WHERE slug = ?",
                    (slug,),
                ).fetchone()
                if current_row and (current_row['feed_type'] != feed['feedType']
                                    or current_row['source_url'] != feed.get('settings', {}).get('source_url', '')):
                    raise ConfigTransferError(f'Feed identity conflict for {slug}')
                source_url = feed.get('settings', {}).get('source_url', '')
                duplicate_source = conn.execute(
                    'SELECT slug FROM podcasts WHERE source_url = ? AND slug != ? LIMIT 1',
                    (source_url, slug),
                ).fetchone()
                if feed['feedType'] == 'subscribed' and duplicate_source:
                    raise ConfigTransferError('Source URL already belongs to another feed')
                updates = _validated_feed_updates(
                    db, dict(current_row) if current_row else None,
                    feed['settings'], feed['feedType'])
                if current_row and feed['feedType'] == 'subscribed':
                    updates.pop('title', None)
                if current_row is None:
                    if feed['feedType'] == 'recents':
                        raise ConfigTransferError('A recents feed must already exist on the destination')
                    title = updates.pop('title', None)
                    source_url = updates.pop('source_url', f'local://{slug}')
                    db.create_podcast(slug, source_url, title, feed['feedType'], conn=conn)
                    if feed['feedType'] == 'local' and 'p20_channel_json' not in updates:
                        from api.feeds import _apply_p20_merge, _public_feed_url
                        feed_url = _public_feed_url(slug, db.get_setting('feed_auth_key'))
                        channel = _apply_p20_merge(
                            {'medium': 'podcast', 'locked': 'yes'}, {})
                        channel['guid'] = compute_feed_guid(feed_url)
                        updates['p20_channel_json'] = _canonical_json(channel)
                    db.update_podcast(slug, conn=conn, **updates)
                    added_feeds.append(slug)
                    refresh_slugs.add(slug)
                else:
                    updates.pop('source_url', None)
                    db.update_podcast(slug, conn=conn, **updates)
                    changed_feeds.append(slug)
                    if rss_fields & updates.keys():
                        refresh_slugs.add(slug)
                    if ('queue_priority' in updates
                            and updates['queue_priority'] != current_row['queue_priority']):
                        priority_changes.append((current_row['id'], updates['queue_priority'] or 0))
                fresh = conn.execute('SELECT id, network_id, network_id_override, queue_priority FROM podcasts WHERE slug = ?', (slug,)).fetchone()
                if 'network_id' in updates or 'network_id_override' in updates:
                    effective_network = updates.get('network_id_override') or fresh['network_id_override'] or fresh['network_id']
                    db.retag_network_cue_templates(fresh['id'], effective_network, conn=conn)
            for stage, key in model_settings.items():
                if stage in old_models and db.get_setting(key) != old_models[stage]:
                    changed_stages.append(stage)
            supplied_models = dict(payload)
            for key, value in settings.items():
                spec = SETTINGS_REGISTRY.get(key)
                if spec and spec.payload_key:
                    supplied_models[spec.payload_key] = value
            if 'pattern_cleanup_model' in settings:
                supplied_models['patternCleanupModel'] = settings['pattern_cleanup_model']
            changed_stages.extend(settings_api._clear_models_for_identity_changes(
                db, previous_identities, supplied_models))
            changed_stages = sorted(set(changed_stages))
            try:
                transfer_revision = int(db.get_setting('config_transfer_revision') or 0)
            except (TypeError, ValueError):
                transfer_revision = 0
            db.set_setting('config_transfer_revision', str(transfer_revision + 1), is_default=False)
    except settings_api._PhaseRejected as rejected:
        settings_api._deferred.buckets = None
        response = rejected.response
        body = response.get_json(silent=True) if hasattr(response, 'get_json') else {}
        status = getattr(response, 'status_code', 400)
        raise ConfigTransferError(
            (body or {}).get('error', 'Settings failed validation'), status) from rejected
    except BaseException:
        settings_api._deferred.buckets = None
        raise
    settings_api._deferred.buckets = None
    settings_api._run_post_commit(_deferred['side_effects'])
    settings_api._run_post_commit(_deferred['holds'])
    if previous_calibration is not None:
        settings_api.finish_settings_payload_after_commit(db, changed_stages, previous_calibration)
    warnings = _after_feed_commit(
        db, changed_feeds + added_feeds, priority_changes, refresh_slugs)
    return {'message': 'Configuration imported', 'addedFeeds': added_feeds,
            'updatedFeeds': changed_feeds, 'warnings': warnings}


def _after_feed_commit(db, slugs, priority_changes, refresh_slugs):
    if not slugs:
        return []
    from main_app.feeds import invalidate_feed_cache, refresh_rss_feed
    warnings = []
    invalidate_feed_cache()
    for podcast_id, priority in priority_changes:
        try:
            db.restamp_pending_priorities(podcast_id, priority)
        except Exception:
            warnings.append('Pending queue priorities could not be refreshed.')
    for slug in slugs:
        if slug not in refresh_slugs:
            continue
        try:
            db.update_podcast_etag(slug, None, None)
            podcast = db.get_podcast_by_slug(slug)
            if podcast:
                outcome = refresh_rss_feed(slug, podcast.get('source_url'), force=True)
                if not outcome.success:
                    warnings.append('One or more served feeds could not be refreshed after commit.')
        except Exception:
            logging.getLogger('podcast.config_transfer').exception(
                'Imported feed refresh failed for %s after commit', slug)
            warnings.append('One or more served feeds could not be refreshed after commit.')
    return sorted(set(warnings))

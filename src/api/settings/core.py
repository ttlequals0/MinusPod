"""Core settings routes: /settings, community pattern sync and /networks."""

import os
from flask import request
from api import (
    api, log_request, json_response, error_response,
    get_database, limiter,
)
from config import (
    OPENROUTER_BASE_URL, DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_ENCODER_COMPRESSION_LEVEL,
    AD_DETECTION_PARALLEL_WINDOWS_DEFAULT,
    AD_DETECTION_PARALLEL_WINDOWS_MIN,
    AD_DETECTION_PARALLEL_WINDOWS_MAX,
    AD_REVIEWER_PARALLEL_ADS_DEFAULT,
    AD_REVIEWER_PARALLEL_ADS_MIN,
    AD_REVIEWER_PARALLEL_ADS_MAX,
    coerce_bool_setting,
    ad_chapter_compat_view,
    MIN_CONTENT_BETWEEN_ADS_SECONDS,
    MAX_AD_DURATION, MAX_AD_DURATION_CONFIRMED, MAX_AD_DURATION_WINDOW,
    get_env_backed_int,
    MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX, MAX_RSS_BYTES_MIN,
    MAX_AUDIO_DOWNLOAD_MB_MIN,
    PODCAST_SEARCH_PROVIDERS,
    EPISODE_LOG_RETENTION_DAYS_MIN, EPISODE_LOG_RETENTION_DAYS_MAX,
    resolve_download_history_retention_days,
    resolve_segment_category_actions_map,
    resolve_community_sync_categories,
    resolve_jit_blocked_user_agents,
)
from systemone.tuning import profile_payload
from api.podcast_search import resolve_search_provider, search_provider_ready
from database.settings import (
    SETTINGS_REGISTRY,
    registry_default, registry_get_default,
)
from llm_client import (
    get_effective_provider, get_effective_base_url, get_api_key, get_effective_openrouter_api_key,
)
from utils.opml import modified_feed_url
from api.settings import common, operations
from youtube import settings as yt_settings


# ========== Settings Endpoints ==========

def _build_settings_payload():
    """Build the full settings payload returned by GET /settings, reused by
    GET /system/config-export so the two never drift."""
    db = get_database()
    from database import (
        DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT,
        DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT,
        DEFAULT_CHAPTER_PROMPT, DEFAULT_PATTERN_CLEANUP_PROMPT,
    )
    from config import (
        AUDIO_CUE_FREQ_MIN_HZ, AUDIO_CUE_FREQ_MAX_HZ,
        AUDIO_CUE_PROMINENCE_DB, AUDIO_CUE_MIN_CONFIDENCE,
        AUDIO_CUE_TEMPLATE_SCORE, AUDIO_CUE_SNAP_CONFIDENCE,
        AUDIO_CUE_SNAP_LEAD_SECONDS, AUDIO_CUE_SNAP_LAG_SECONDS,
        AUDIO_CUE_FORMANT_ATTEN_DB,
        AUDIO_CUE_CAPTURE_MIN_SECONDS, AUDIO_CUE_CAPTURE_MAX_SECONDS,
        AUDIO_CUE_CAPTURE_MAX_INTRO_SECONDS, AUDIO_CUE_CAPTURE_MAX_OUTRO_SECONDS,
        AUDIO_CUE_PAIR_CONFIDENCE, AUDIO_CUE_PAIR_MIN_BREAK_SECONDS,
        AUDIO_CUE_PAIR_MAX_BREAK_SECONDS, AUDIO_CUE_PAIR_MAX_BREAK_FRACTION,
        AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS,
        SILENCE_SNAP_NOISE_DB, SILENCE_SNAP_MIN_DURATION_SECONDS,
        SILENCE_SNAP_MAX_DISTANCE_SECONDS,
    )
    snapshot = db.get_all_settings()
    settings = common._settings_view(snapshot)

    # Shorthand for building {value, isDefault} response dicts
    def _sv(key, value=None):
        """Build a setting value response dict."""
        return {
            'value': value if value is not None else common._setting_value(settings, key),
            'isDefault': common._setting_is_default(settings, key),
        }

    # Get current model settings
    current_model = common._setting_value(settings, 'claude_model')
    verification_model = common._setting_value(settings, 'verification_model')
    chapters_model = common._setting_value(settings, 'chapters_model')

    # Per-phase provider routing overrides: a SLOT (primary/secondary), not
    # a provider type (see llm_route.py). Unset (None) means "inherit":
    # verification/chapters fall back to detection's slot, detection falls
    # back to primary.
    detection_provider = common._setting_value(settings, 'detection_provider')
    verification_provider = common._setting_value(settings, 'verification_provider')
    chapters_provider = common._setting_value(settings, 'chapters_provider')

    # Get whisper model setting (defaults to env var or 'small')
    default_whisper_model = registry_default('whisper_model')
    whisper_model = common._setting_value(settings, 'whisper_model', default_whisper_model)

    # Get boolean settings (fallback strings come from the registry)
    auto_process_value = common._setting_value(
        settings, 'auto_process_enabled', registry_default('auto_process_enabled'))
    auto_process_enabled = auto_process_value.lower() in ('true', '1', 'yes')
    vtt_value = common._setting_value(
        settings, 'vtt_transcripts_enabled', registry_default('vtt_transcripts_enabled'))
    vtt_enabled = vtt_value.lower() in ('true', '1', 'yes')
    chapters_value = common._setting_value(
        settings, 'chapters_enabled', registry_default('chapters_enabled'))
    chapters_enabled = chapters_value.lower() in ('true', '1', 'yes')
    chapters_mode = common._setting_value(
        settings, 'chapters_mode', registry_default('chapters_mode'))
    skip_second_pass = coerce_bool_setting(common._setting_value(
        settings, 'skip_second_pass', registry_default('skip_second_pass')))
    transcript_differential_enabled = coerce_bool_setting(common._setting_value(
        settings, 'transcript_differential_enabled',
        registry_default('transcript_differential_enabled')))
    differential_fetch_mode = common._setting_value(
        settings, 'differential_fetch_mode', registry_default('differential_fetch_mode'))
    chapters_in_notes = coerce_bool_setting(common._setting_value(
        settings, 'chapters_in_notes', registry_default('chapters_in_notes')))
    only_expose_processed_value = common._setting_value(
        settings, 'only_expose_processed_default',
        registry_default('only_expose_processed_default'))
    only_expose_processed_default = (
        only_expose_processed_value.lower() in ('true', '1', 'yes'))
    detect_show_segments_default = coerce_bool_setting(common._setting_value(
        settings, 'detect_show_segments', registry_default('detect_show_segments')))
    text_recurrence_hints = coerce_bool_setting(common._setting_value(
        settings, 'text_recurrence_hints', registry_default('text_recurrence_hints')))
    ad_addressing_mode = common._setting_value(
        settings, 'ad_addressing_mode', registry_default('ad_addressing_mode'))
    seed_sponsors = {
        key: coerce_bool_setting(common._setting_value(
            settings, key, registry_default(key)))
        for key in ('seed_sponsors_detection', 'seed_sponsors_verification',
                    'seed_sponsors_reviewer', 'seed_sponsors_resurrect')
    }
    process_new_episodes_first = coerce_bool_setting(common._setting_value(
        settings, 'process_new_episodes_first',
        registry_default('process_new_episodes_first')))
    artwork_watermark_value = common._setting_value(
        settings, 'artwork_watermark_enabled',
        registry_default('artwork_watermark_enabled'))
    artwork_watermark_enabled = (
        artwork_watermark_value.lower() in ('true', '1', 'yes'))
    artwork_badge_position = common._setting_value(
        settings, 'artwork_badge_position',
        registry_default('artwork_badge_position'))
    low_ad_yield_action = common._setting_value(
        settings, 'low_ad_yield_action',
        registry_default('low_ad_yield_action'))
    episode_log_retention_days = get_env_backed_int(
        'episode_log_retention_days',
        floor=EPISODE_LOG_RETENTION_DAYS_MIN,
        ceiling=EPISODE_LOG_RETENTION_DAYS_MAX,
        settings=settings)
    episode_log_level = common._setting_value(
        settings, 'episode_log_level', registry_default('episode_log_level'))
    download_user_agent = common._setting_value(
        settings, 'download_user_agent', registry_default('download_user_agent'))
    feed_user_agent = common._setting_value(
        settings, 'feed_user_agent', registry_default('feed_user_agent'))
    log_download_query = coerce_bool_setting(common._setting_value(
        settings, 'log_download_query', registry_default('log_download_query')))
    download_history_enabled = coerce_bool_setting(common._setting_value(
        settings, 'download_history_enabled',
        registry_default('download_history_enabled')))
    download_geo_lookup_enabled = coerce_bool_setting(common._setting_value(
        settings, 'download_geo_lookup_enabled',
        registry_default('download_geo_lookup_enabled')))
    download_history_retention_days = resolve_download_history_retention_days(db)
    feed_auth_enabled = coerce_bool_setting(
        common._setting_value(settings, 'feed_auth_enabled',
                       registry_default('feed_auth_enabled')))
    # Bearer key for authenticated feeds; intentionally readable (the UI/API
    # must display it so the operator can subscribe apps with it).
    feed_auth_key = common._setting_value(settings, 'feed_auth_key', '') or None
    # Copyable OPML import-by-URL links, non-null only when feed auth is on
    # (the /opml route is key-gated and 404s otherwise). Server-built because
    # the frontend cannot know the public feed BASE_URL.
    opml_modified_url = opml_original_url = None
    if feed_auth_enabled and feed_auth_key:
        # Reuse the keyed-URL shaper so the ?key= convention lives in one place.
        _opml_base = os.environ.get('BASE_URL', 'http://localhost:8000')
        opml_modified_url = modified_feed_url(_opml_base, 'opml/modified.opml', feed_auth_key)
        opml_original_url = modified_feed_url(_opml_base, 'opml/original.opml', feed_auth_key)

    try:
        max_feed_episodes = int(common._setting_value(
            settings, 'max_feed_episodes', registry_default('max_feed_episodes')))
    except (ValueError, TypeError):
        max_feed_episodes = registry_get_default('max_feed_episodes')

    try:
        rss_refresh_interval_minutes = int(common._setting_value(
            settings, 'rss_refresh_interval_minutes',
            registry_default('rss_refresh_interval_minutes')))
    except (ValueError, TypeError):
        rss_refresh_interval_minutes = registry_get_default('rss_refresh_interval_minutes')

    def _int_setting(key):
        try:
            return int(common._setting_value(settings, key, registry_default(key)))
        except (TypeError, ValueError):
            return registry_get_default(key)

    def _str_setting(key):
        return common._setting_value(settings, key, registry_default(key))

    auto_process_max_age_hours = _int_setting('auto_process_max_age_hours')
    queue_manual_boost = _int_setting('queue_manual_boost')
    queue_fresh_boost = _int_setting('queue_fresh_boost')
    queue_bulk_boost = _int_setting('queue_bulk_boost')

    podping_enabled = coerce_bool_setting(common._setting_value(
        settings, 'podping_enabled', registry_default('podping_enabled')))

    omit_temperature = coerce_bool_setting(common._setting_value(
        settings, 'omit_temperature', registry_default('omit_temperature')))
    llm_json_schema_enabled = coerce_bool_setting(common._setting_value(
        settings, 'llm_json_schema_enabled', registry_default('llm_json_schema_enabled')))

    segment_category_actions = resolve_segment_category_actions_map(
        common._setting_value(settings, 'segment_category_actions',
                       registry_default('segment_category_actions')))

    community_sync_categories = resolve_community_sync_categories(
        common._setting_value(settings, 'community_sync_categories',
                       registry_default('community_sync_categories')))

    jit_blocked_user_agents = resolve_jit_blocked_user_agents(
        common._setting_value(settings, 'jit_blocked_user_agents',
                       registry_default('jit_blocked_user_agents')))

    # Get min cut confidence (ad detection aggressiveness)
    try:
        min_cut_confidence = float(common._setting_value(
            settings, 'min_cut_confidence', registry_default('min_cut_confidence')))
    except (ValueError, TypeError):
        min_cut_confidence = registry_get_default('min_cut_confidence')

    # LLM provider settings
    llm_provider = get_effective_provider()
    openai_base_url = get_effective_base_url()
    pricing_source_mode = common._setting_value(
        settings, 'pricing_source_mode', registry_default('pricing_source_mode'))
    model_pricing_overrides = db.get_model_pricing_overrides()
    api_key = get_api_key()
    api_key_configured = bool(api_key and api_key != 'not-needed')
    openrouter_api_key = get_effective_openrouter_api_key()
    openrouter_api_key_configured = bool(openrouter_api_key)

    # Optional secondary provider: a second full provider config that stage
    # settings can route to via the 'secondary' slot.
    secondary_provider_enabled = coerce_bool_setting(common._setting_value(
        settings, 'secondary_provider_enabled',
        registry_default('secondary_provider_enabled')))
    secondary_provider = common._setting_value(settings, 'secondary_provider')
    secondary_provider_base_url = common._setting_value(
        settings, 'secondary_provider_base_url',
        registry_default('secondary_provider_base_url'))
    secondary_provider_api_key_configured = bool(db.get_secret('secondary_provider_api_key'))

    # Provider failover settings and per-slot timeout overrides.
    def _int_or_blank(db_key):
        raw = common._setting_value(settings, db_key)
        return int(raw) if raw else None

    def _number_or_blank(db_key):
        raw = common._setting_value(settings, db_key)
        if not raw:
            return None
        number = float(raw)
        return int(number) if number.is_integer() else number

    failover_llm_enabled = coerce_bool_setting(common._setting_value(
        settings, 'failover_llm_enabled', registry_default('failover_llm_enabled')))
    failover_llm_provider = common._setting_value(settings, 'failover_llm_provider')
    failover_llm_base_url = common._setting_value(
        settings, 'failover_llm_base_url', registry_default('failover_llm_base_url'))
    failover_llm_timeout_seconds = _int_or_blank('failover_llm_timeout_seconds')
    failover_llm_max_retries = _int_or_blank('failover_llm_max_retries')
    failover_llm_detection_model = common._setting_value(
        settings, 'failover_llm_detection_model', registry_default('failover_llm_detection_model'))
    failover_llm_review_model = common._setting_value(
        settings, 'failover_llm_review_model', registry_default('failover_llm_review_model'))
    failover_llm_verification_model = common._setting_value(
        settings, 'failover_llm_verification_model',
        registry_default('failover_llm_verification_model'))
    failover_llm_chapters_model = common._setting_value(
        settings, 'failover_llm_chapters_model', registry_default('failover_llm_chapters_model'))
    failover_llm_api_key_configured = bool(db.get_secret('failover_llm_api_key'))

    failover_whisper_enabled = coerce_bool_setting(common._setting_value(
        settings, 'failover_whisper_enabled', registry_default('failover_whisper_enabled')))
    failover_whisper_backend = common._setting_value(
        settings, 'failover_whisper_backend', registry_default('failover_whisper_backend'))
    failover_whisper_model = common._setting_value(
        settings, 'failover_whisper_model', registry_default('failover_whisper_model'))
    failover_whisper_api_base_url = common._setting_value(
        settings, 'failover_whisper_api_base_url', registry_default('failover_whisper_api_base_url'))
    failover_whisper_api_model = common._setting_value(
        settings, 'failover_whisper_api_model', registry_default('failover_whisper_api_model'))
    failover_whisper_api_timeout_seconds = _int_setting('failover_whisper_api_timeout_seconds')
    failover_whisper_max_attempts = _int_setting('failover_whisper_max_attempts')
    failover_whisper_language = common._setting_value(
        settings, 'failover_whisper_language', registry_default('failover_whisper_language'))
    failover_whisper_api_key_configured = bool(db.get_secret('failover_whisper_api_key'))

    failover_probe_interval_minutes = _int_setting('failover_probe_interval_minutes')
    failover_recovery_probes = _int_setting('failover_recovery_probes')

    provider_a_timeout_seconds = _number_or_blank('llm_timeout_seconds')
    provider_a_max_retries = _int_or_blank('llm_max_retries')
    provider_b_timeout_seconds = _number_or_blank('secondary_llm_timeout_seconds')
    provider_b_max_retries = _int_or_blank('secondary_llm_max_retries')
    whisper_max_attempts = _int_setting('whisper_max_attempts')

    podcast_index_api_key = common._setting_value(settings, 'podcast_index_api_key', '') or os.environ.get('PODCAST_INDEX_API_KEY', '')

    # Whisper backend settings (env var defaults, resolved via the registry)
    default_whisper_backend = registry_default('whisper_backend')
    default_whisper_api_base_url = registry_default('whisper_api_base_url')
    default_whisper_api_model = registry_default('whisper_api_model')
    default_whisper_language = registry_default('whisper_language')
    default_whisper_compute_type = registry_default('whisper_compute_type')
    default_vad_gap_enabled = registry_get_default('vad_gap_detection_enabled')

    default_vad_gap_start = registry_get_default('vad_gap_start_min_seconds')
    default_vad_gap_mid = registry_get_default('vad_gap_mid_min_seconds')
    default_vad_gap_tail = registry_get_default('vad_gap_tail_min_seconds')
    whisper_backend = common._setting_value(settings, 'whisper_backend', default_whisper_backend)
    whisper_api_base_url = common._setting_value(settings, 'whisper_api_base_url', default_whisper_api_base_url)
    whisper_api_key = common._setting_value(settings, 'whisper_api_key', '')
    whisper_api_model = common._setting_value(settings, 'whisper_api_model', default_whisper_api_model)
    whisper_language = common._setting_value(settings, 'whisper_language', default_whisper_language)
    whisper_compute_type = common._setting_value(settings, 'whisper_compute_type', default_whisper_compute_type)
    vad_gap_enabled_raw = common._setting_value(settings, 'vad_gap_detection_enabled', str(default_vad_gap_enabled).lower())
    vad_gap_enabled = str(vad_gap_enabled_raw).lower() in ('true', '1', 'yes')

    def _db_float(key, default):
        try:
            return float(common._setting_value(settings, key, default))
        except (ValueError, TypeError):
            return default

    vad_gap_start = _db_float('vad_gap_start_min_seconds', default_vad_gap_start)
    vad_gap_mid = _db_float('vad_gap_mid_min_seconds', default_vad_gap_mid)
    vad_gap_tail = _db_float('vad_gap_tail_min_seconds', default_vad_gap_tail)
    min_content_between_ads = _db_float('min_content_between_ads_seconds', MIN_CONTENT_BETWEEN_ADS_SECONDS)
    ad_detection_exclude_start = _db_float('ad_detection_exclude_start_seconds', 0.0)
    max_ad_duration = _db_float('max_ad_duration_seconds', MAX_AD_DURATION)
    max_ad_duration_confirmed = _db_float('max_ad_duration_confirmed_seconds',
                                          MAX_AD_DURATION_CONFIRMED)
    max_llm_window_ad = _db_float('max_llm_window_ad_seconds', MAX_AD_DURATION_WINDOW)

    # Detection tuning (2.76.0): verification-miss hold/autocut confidence,
    # learning confidence floors, differential correlation/hold thresholds.
    verification_miss_hold_min_confidence = _db_float(
        'verification_miss_hold_min_confidence',
        registry_get_default('verification_miss_hold_min_confidence'))
    verification_miss_autocut_min_confidence = _db_float(
        'verification_miss_autocut_min_confidence',
        registry_get_default('verification_miss_autocut_min_confidence'))
    learning_min_confidence = _db_float(
        'learning_min_confidence', registry_get_default('learning_min_confidence'))
    learning_min_confidence_long = _db_float(
        'learning_min_confidence_long', registry_get_default('learning_min_confidence_long'))
    differential_measured_corr_max = _db_float(
        'differential_measured_corr_max', registry_get_default('differential_measured_corr_max'))
    differential_hold_min_seconds = _db_float(
        'differential_hold_min_seconds', registry_get_default('differential_hold_min_seconds'))

    audio_bitrate = common._setting_value(settings, 'audio_bitrate', DEFAULT_AUDIO_BITRATE)
    audio_encoder_compression_level = common._setting_value(
        settings, 'audio_encoder_compression_level', DEFAULT_AUDIO_ENCODER_COMPRESSION_LEVEL)
    audio_replacement_sound_enabled = coerce_bool_setting(common._setting_value(
        settings, 'audio_replacement_sound_enabled', registry_default('audio_replacement_sound_enabled')))
    audio_mp3_stream_copy_enabled = coerce_bool_setting(common._setting_value(
        settings, 'audio_mp3_stream_copy_enabled', registry_default('audio_mp3_stream_copy_enabled')))
    audio_normalize_enabled_raw = common._setting_value(
        settings, 'audio_normalize_enabled', registry_default('audio_normalize_enabled'))
    audio_normalize_enabled = str(audio_normalize_enabled_raw).lower() in ('true', '1', 'yes')
    audio_normalize_intensity = common._setting_value(
        settings, 'audio_normalize_intensity', registry_default('audio_normalize_intensity'))
    skip_flac_raw = common._setting_value(
        settings, 'skip_flac_compression', registry_default('skip_flac_compression'))
    skip_flac = coerce_bool_setting(skip_flac_raw)

    default_parallel_windows = str(AD_DETECTION_PARALLEL_WINDOWS_DEFAULT)
    parallel_windows_raw = common._setting_value(
        settings, 'ad_detection_parallel_windows', default_parallel_windows
    )
    parallel_windows = common._clamped_int(
        parallel_windows_raw, AD_DETECTION_PARALLEL_WINDOWS_DEFAULT,
        AD_DETECTION_PARALLEL_WINDOWS_MIN, AD_DETECTION_PARALLEL_WINDOWS_MAX,
    )

    default_reviewer_parallel = str(AD_REVIEWER_PARALLEL_ADS_DEFAULT)
    reviewer_parallel_raw = common._setting_value(
        settings, 'ad_reviewer_parallel_ads', default_reviewer_parallel
    )
    reviewer_parallel = common._clamped_int(
        reviewer_parallel_raw, AD_REVIEWER_PARALLEL_ADS_DEFAULT,
        AD_REVIEWER_PARALLEL_ADS_MIN, AD_REVIEWER_PARALLEL_ADS_MAX,
    )

    max_artwork_bytes = get_env_backed_int(
        'max_artwork_bytes', floor=MAX_ARTWORK_BYTES_MIN,
        ceiling=MAX_ARTWORK_BYTES_MAX, settings=settings)
    max_rss_bytes = get_env_backed_int(
        'max_rss_bytes', floor=MAX_RSS_BYTES_MIN, settings=settings)
    max_audio_download_mb = get_env_backed_int(
        'max_audio_download_mb', floor=MAX_AUDIO_DOWNLOAD_MB_MIN,
        settings=settings)
    provider_requests_per_min = get_env_backed_int(
        'provider_requests_per_min', floor=0, settings=settings)
    provider_requests_per_day = get_env_backed_int(
        'provider_requests_per_day', floor=0, settings=settings)
    secondary_provider_requests_per_min = get_env_backed_int(
        'secondary_provider_requests_per_min', floor=0, settings=settings)
    secondary_provider_requests_per_day = get_env_backed_int(
        'secondary_provider_requests_per_day', floor=0, settings=settings)
    provider_tokens_per_min = get_env_backed_int(
        'provider_tokens_per_min', floor=0, settings=settings)
    secondary_provider_tokens_per_min = get_env_backed_int(
        'secondary_provider_tokens_per_min', floor=0, settings=settings)

    def _db_int(key, default):
        try:
            return int(common._setting_value(settings, key, default))
        except (ValueError, TypeError):
            return default

    learning_min_pattern_duration = _db_int(
        'learning_min_pattern_duration', registry_get_default('learning_min_pattern_duration'))
    learning_max_pattern_duration = _db_int(
        'learning_max_pattern_duration', registry_get_default('learning_max_pattern_duration'))
    whisper_api_timeout_seconds = _db_int(
        'whisper_api_timeout_seconds', registry_get_default('whisper_api_timeout_seconds'))
    transcribe_max_chunk_seconds = _db_int(
        'transcribe_max_chunk_seconds', registry_get_default('transcribe_max_chunk_seconds'))
    transcribe_concurrent_chunks = _db_int(
        'transcribe_concurrent_chunks', registry_get_default('transcribe_concurrent_chunks'))
    transcribe_chunk_overlap_seconds = _db_int(
        'transcribe_chunk_overlap_seconds', registry_get_default('transcribe_chunk_overlap_seconds'))
    whisper_pool_max_requests = _db_int(
        'whisper_pool_max_requests', registry_get_default('whisper_pool_max_requests'))
    whisper_pool_max_episodes = _db_int(
        'whisper_pool_max_episodes', registry_get_default('whisper_pool_max_episodes'))
    whisper_pool_enabled_raw = common._setting_value(
        settings, 'whisper_pool_enabled', registry_default('whisper_pool_enabled'))
    whisper_pool_enabled = coerce_bool_setting(whisper_pool_enabled_raw)

    # Per-stage LLM tunables: resolved value (DB > env > default) and env-default provenance.
    from config import (
        get_stage_tunable, stage_tunable_env_override,
        STAGE_TUNABLE_DEFAULTS, STAGE_TUNABLE_PAYLOAD_KEYS,
    )

    def _tu(db_key):
        # Reuse the loaded settings dict to avoid a DB read per tunable.
        return {
            'value': get_stage_tunable(db_key, settings=settings),
            'isDefault': common._setting_is_default(settings, db_key),
            'envOverride': stage_tunable_env_override(db_key),
        }

    tunables_payload = {
        payload_key: _tu(db_key) for payload_key, db_key, _kind in STAGE_TUNABLE_PAYLOAD_KEYS
    }

    enable_ad_review_raw = common._setting_value(
        settings, 'enable_ad_review', registry_default('enable_ad_review'))
    enable_ad_review = str(enable_ad_review_raw).strip().lower() == 'true'
    review_model = common._setting_value(settings, 'review_model', registry_default('review_model'))
    review_provider = common._setting_value(
        settings, 'review_provider', registry_default('review_provider'))
    try:
        review_max_boundary_shift = int(common._setting_value(
            settings, 'review_max_boundary_shift', registry_default('review_max_boundary_shift')))
    except (ValueError, TypeError):
        review_max_boundary_shift = registry_get_default('review_max_boundary_shift')
    # `or DEFAULT` (not just the _setting_value fallback) so a stored empty/whitespace
    # row also yields the default text -- _setting_value only covers a missing row, so a
    # blank one would render an unrecoverable empty textarea. All four prompts share this
    # contract, hence the same coalesce on system/verification below.
    review_prompt = common._setting_value(settings, 'review_prompt', DEFAULT_REVIEW_PROMPT) or DEFAULT_REVIEW_PROMPT
    resurrect_prompt = common._setting_value(settings, 'resurrect_prompt', DEFAULT_RESURRECT_PROMPT) or DEFAULT_RESURRECT_PROMPT
    chapter_prompt = common._setting_value(settings, 'chapter_prompt', DEFAULT_CHAPTER_PROMPT) or DEFAULT_CHAPTER_PROMPT
    pattern_cleanup_prompt = (common._setting_value(settings, 'pattern_cleanup_prompt')
                              or DEFAULT_PATTERN_CLEANUP_PROMPT)

    # Audio cue detection experiment (#350)
    audio_cue_enabled = str(common._setting_value(
        settings, 'audio_cue_detection_enabled',
        registry_default('audio_cue_detection_enabled'))).strip().lower() == 'true'
    audio_cue_create_from_pairs = coerce_bool_setting(common._setting_value(
        settings, 'audio_cue_create_from_pairs',
        registry_default('audio_cue_create_from_pairs')))
    audio_cue_cut_matches = coerce_bool_setting(common._setting_value(
        settings, 'audio_cue_cut_matches',
        registry_default('audio_cue_cut_matches')))

    dai_differential_overrides_keep = coerce_bool_setting(common._setting_value(
        settings, 'dai_differential_overrides_keep',
        registry_default('dai_differential_overrides_keep')))
    splice_veto_enabled = coerce_bool_setting(common._setting_value(
        settings, 'splice_veto_enabled', registry_default('splice_veto_enabled')))

    # Compatibility fields (spec 1.4): derived from segment_category_actions,
    # not their own retired settings.
    ad_chapters_enabled, ad_chapter_categories = ad_chapter_compat_view(segment_category_actions)
    ad_chapters_include_held = coerce_bool_setting(
        _str_setting('ad_chapters_include_held'))
    ad_chapter_title_format = _str_setting('ad_chapter_title_format')
    ad_chapter_held_title_format = _str_setting('ad_chapter_held_title_format')
    ad_chapter_resume_title = _str_setting('ad_chapter_resume_title')
    ad_chapter_min_confidence = _db_float(
        'ad_chapter_min_confidence',
        registry_get_default('ad_chapter_min_confidence'))

    # Learned positional prior experiment (#360)
    positional_prior_enabled = coerce_bool_setting(common._setting_value(
        settings, 'positional_prior_enabled',
        registry_default('positional_prior_enabled')))

    def _cue_num(key, default):
        try:
            return float(common._setting_value(settings, key, str(default)))
        except (ValueError, TypeError):
            return float(default)

    audio_cue_freq_min = int(_cue_num('audio_cue_freq_min_hz', AUDIO_CUE_FREQ_MIN_HZ))
    audio_cue_freq_max = int(_cue_num('audio_cue_freq_max_hz', AUDIO_CUE_FREQ_MAX_HZ))
    audio_cue_prominence = _cue_num('audio_cue_prominence_db', AUDIO_CUE_PROMINENCE_DB)
    audio_cue_min_conf = _cue_num('audio_cue_min_confidence', AUDIO_CUE_MIN_CONFIDENCE)
    audio_cue_template_score = _cue_num('audio_cue_template_score', AUDIO_CUE_TEMPLATE_SCORE)
    audio_cue_formant_atten = _cue_num('audio_cue_formant_atten_db', AUDIO_CUE_FORMANT_ATTEN_DB)
    audio_cue_snap_conf = _cue_num('audio_cue_snap_confidence', AUDIO_CUE_SNAP_CONFIDENCE)
    audio_cue_snap_lead = _cue_num('audio_cue_snap_lead_seconds', AUDIO_CUE_SNAP_LEAD_SECONDS)
    audio_cue_snap_lag = _cue_num('audio_cue_snap_lag_seconds', AUDIO_CUE_SNAP_LAG_SECONDS)
    audio_cue_capture_min = _cue_num('audio_cue_capture_min_seconds', AUDIO_CUE_CAPTURE_MIN_SECONDS)
    audio_cue_capture_max = _cue_num('audio_cue_capture_max_seconds', AUDIO_CUE_CAPTURE_MAX_SECONDS)
    audio_cue_capture_max_intro = _cue_num('audio_cue_capture_max_intro_seconds', AUDIO_CUE_CAPTURE_MAX_INTRO_SECONDS)
    audio_cue_capture_max_outro = _cue_num('audio_cue_capture_max_outro_seconds', AUDIO_CUE_CAPTURE_MAX_OUTRO_SECONDS)
    audio_cue_pair_conf = _cue_num('audio_cue_pair_confidence', AUDIO_CUE_PAIR_CONFIDENCE)
    audio_cue_pair_min_break = _cue_num('audio_cue_pair_min_break_seconds', AUDIO_CUE_PAIR_MIN_BREAK_SECONDS)
    audio_cue_pair_max_break = _cue_num('audio_cue_pair_max_break_seconds', AUDIO_CUE_PAIR_MAX_BREAK_SECONDS)
    audio_cue_pair_max_break_fraction = _cue_num('audio_cue_pair_max_break_fraction', AUDIO_CUE_PAIR_MAX_BREAK_FRACTION)
    audio_cue_pair_orient_window = _cue_num('audio_cue_pair_orient_window_seconds', AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS)

    # Silence-snap tunables (Phase B boundary snap)
    silence_snap_noise_db = _cue_num('silence_snap_noise_db', SILENCE_SNAP_NOISE_DB)
    silence_snap_min_duration = _cue_num('silence_snap_min_duration_seconds', SILENCE_SNAP_MIN_DURATION_SECONDS)
    silence_snap_max_distance = _cue_num('silence_snap_max_distance_seconds', SILENCE_SNAP_MAX_DISTANCE_SECONDS)

    payload = {
        'systemPrompt': _sv('system_prompt', common._setting_value(settings, 'system_prompt', DEFAULT_SYSTEM_PROMPT) or DEFAULT_SYSTEM_PROMPT),
        'verificationPrompt': _sv('verification_prompt', common._setting_value(settings, 'verification_prompt', DEFAULT_VERIFICATION_PROMPT) or DEFAULT_VERIFICATION_PROMPT),
        'enableAdReview': _sv('enable_ad_review', enable_ad_review),
        'reviewModel': _sv('review_model', review_model),
        'reviewProvider': _sv('review_provider', review_provider),
        'reviewMaxBoundaryShift': _sv('review_max_boundary_shift', review_max_boundary_shift),
        'reviewPrompt': _sv('review_prompt', review_prompt),
        'resurrectPrompt': _sv('resurrect_prompt', resurrect_prompt),
        'chapterPrompt': _sv('chapter_prompt', chapter_prompt),
        'patternCleanupPrompt': _sv('pattern_cleanup_prompt', pattern_cleanup_prompt),
        'systemPromptOverride': _sv('system_prompt_override', common._setting_value(settings, 'system_prompt_override', '') or ''),
        'verificationPromptOverride': _sv('verification_prompt_override', common._setting_value(settings, 'verification_prompt_override', '') or ''),
        'reviewPromptOverride': _sv('review_prompt_override', common._setting_value(settings, 'review_prompt_override', '') or ''),
        'resurrectPromptOverride': _sv('resurrect_prompt_override', common._setting_value(settings, 'resurrect_prompt_override', '') or ''),
        'chapterPromptOverride': _sv('chapter_prompt_override', common._setting_value(settings, 'chapter_prompt_override', '') or ''),
        'claudeModel': _sv('claude_model', current_model),
        'verificationModel': _sv('verification_model', verification_model),
        'detectionProvider': _sv('detection_provider', detection_provider),
        'verificationProvider': _sv('verification_provider', verification_provider),
        'whisperModel': _sv('whisper_model', whisper_model),
        'autoProcessEnabled': _sv('auto_process_enabled', auto_process_enabled),
        'autoProcessMaxAgeHours': _sv('auto_process_max_age_hours', auto_process_max_age_hours),
        'maxFeedEpisodes': _sv('max_feed_episodes', max_feed_episodes),
        'rssRefreshIntervalMinutes': _sv(
            'rss_refresh_interval_minutes', rss_refresh_interval_minutes),
        'queueManualBoost': _sv('queue_manual_boost', queue_manual_boost),
        'queueFreshBoost': _sv('queue_fresh_boost', queue_fresh_boost),
        'queueBulkBoost': _sv('queue_bulk_boost', queue_bulk_boost),
        'podpingEnabled': _sv('podping_enabled', podping_enabled),
        'segmentCategoryActions': _sv(
            'segment_category_actions', segment_category_actions),
        'communitySyncCategories': _sv(
            'community_sync_categories', community_sync_categories),
        'jitBlockedUserAgents': _sv('jit_blocked_user_agents', jit_blocked_user_agents),
        'onlyExposeProcessedDefault': _sv(
            'only_expose_processed_default', only_expose_processed_default),
        'detectShowSegments': _sv(
            'detect_show_segments', detect_show_segments_default),
        'textRecurrenceHints': _sv(
            'text_recurrence_hints', text_recurrence_hints),
        'adAddressingMode': _sv(
            'ad_addressing_mode', ad_addressing_mode),
        'seedSponsorsDetection': _sv(
            'seed_sponsors_detection', seed_sponsors['seed_sponsors_detection']),
        'seedSponsorsVerification': _sv(
            'seed_sponsors_verification', seed_sponsors['seed_sponsors_verification']),
        'seedSponsorsReviewer': _sv(
            'seed_sponsors_reviewer', seed_sponsors['seed_sponsors_reviewer']),
        'seedSponsorsResurrect': _sv(
            'seed_sponsors_resurrect', seed_sponsors['seed_sponsors_resurrect']),
        'processNewEpisodesFirst': _sv(
            'process_new_episodes_first', process_new_episodes_first),
        'artworkWatermarkEnabled': _sv(
            'artwork_watermark_enabled', artwork_watermark_enabled),
        'artworkBadgePosition': _sv(
            'artwork_badge_position', artwork_badge_position),
        'lowAdYieldAction': _sv('low_ad_yield_action', low_ad_yield_action),
        'episodeLogRetentionDays': _sv(
            'episode_log_retention_days', episode_log_retention_days),
        'episodeLogLevel': _sv('episode_log_level', episode_log_level),
        'downloadUserAgent': _sv('download_user_agent', download_user_agent),
        'feedUserAgent': _sv('feed_user_agent', feed_user_agent),
        'logDownloadQuery': _sv('log_download_query', log_download_query),
        'feedAuthEnabled': _sv('feed_auth_enabled', feed_auth_enabled),
        'downloadHistoryEnabled': _sv(
            'download_history_enabled', download_history_enabled),
        'downloadGeoLookupEnabled': _sv(
            'download_geo_lookup_enabled', download_geo_lookup_enabled),
        'downloadHistoryRetentionDays': _sv(
            'download_history_retention_days', download_history_retention_days),
        'feedAuthKey': feed_auth_key,
        'opmlModifiedUrl': opml_modified_url,
        'opmlOriginalUrl': opml_original_url,
        'vttTranscriptsEnabled': _sv('vtt_transcripts_enabled', vtt_enabled),
        'chaptersEnabled': _sv('chapters_enabled', chapters_enabled),
        'chaptersMode': _sv('chapters_mode', chapters_mode),
        'chaptersInNotes': _sv('chapters_in_notes', chapters_in_notes),
        'skipSecondPass': _sv('skip_second_pass', skip_second_pass),
        'transcriptDifferentialEnabled': _sv(
            'transcript_differential_enabled', transcript_differential_enabled),
        'differentialFetchMode': _sv(
            'differential_fetch_mode', differential_fetch_mode),
        'adChaptersEnabled': _sv('segment_category_actions', ad_chapters_enabled),
        'adChapterCategories': _sv('segment_category_actions', ad_chapter_categories),
        'adChaptersIncludeHeld': _sv('ad_chapters_include_held', ad_chapters_include_held),
        'adChapterTitleFormat': _sv('ad_chapter_title_format', ad_chapter_title_format),
        'adChapterHeldTitleFormat': _sv(
            'ad_chapter_held_title_format', ad_chapter_held_title_format),
        'adChapterResumeTitle': _sv('ad_chapter_resume_title', ad_chapter_resume_title),
        'adChapterMinConfidence': _sv('ad_chapter_min_confidence', ad_chapter_min_confidence),
        'chaptersModel': _sv('chapters_model', chapters_model),
        'chaptersProvider': _sv('chapters_provider', chapters_provider),
        'patternCleanupEnabled': _sv(
            'pattern_cleanup_enabled', coerce_bool_setting(_str_setting('pattern_cleanup_enabled'))),
        'patternCleanupCron': _sv('pattern_cleanup_cron', _str_setting('pattern_cleanup_cron')),
        'patternCleanupBatchSize': _sv(
            'pattern_cleanup_batch_size', _int_setting('pattern_cleanup_batch_size')),
        'patternCleanupUnusedDays': _sv(
            'pattern_cleanup_unused_days', _int_setting('pattern_cleanup_unused_days')),
        'patternCleanupProvider': _sv('pattern_cleanup_provider'),
        'patternCleanupModel': _sv('pattern_cleanup_model'),
        'minCutConfidence': _sv('min_cut_confidence', min_cut_confidence),
        'llmProvider': _sv('llm_provider', llm_provider),
        'omitTemperature': _sv('omit_temperature', omit_temperature),
        'llmJsonSchemaEnabled': _sv('llm_json_schema_enabled', llm_json_schema_enabled),
        'openaiBaseUrl': _sv('openai_base_url', openai_base_url),
        'pricingSourceMode': _sv('pricing_source_mode', pricing_source_mode),
        'modelPricingOverrides': _sv(
            'model_pricing_overrides', model_pricing_overrides),
        'openrouterApiKeyConfigured': openrouter_api_key_configured,
        'typesafeApiKeyConfigured': bool(
            db.get_secret('typesafe_api_key') or os.environ.get('TYPESAFE_API_KEY')),
        'systemoneApiKeyConfigured': bool(
            db.get_secret('systemone_api_key') or os.environ.get('SYSTEMONE_API_KEY')),
        'systemoneBaseUrl': _sv('systemone_base_url', ''),
        'secondaryProviderEnabled': _sv('secondary_provider_enabled', secondary_provider_enabled),
        'secondaryProvider': _sv('secondary_provider', secondary_provider),
        'secondaryProviderBaseUrl': _sv('secondary_provider_base_url', secondary_provider_base_url),
        'secondaryProviderApiKeyConfigured': secondary_provider_api_key_configured,
        'providerRequestsPerMin': _sv('provider_requests_per_min', provider_requests_per_min),
        'providerRequestsPerDay': _sv('provider_requests_per_day', provider_requests_per_day),
        'secondaryProviderRequestsPerMin': _sv(
            'secondary_provider_requests_per_min', secondary_provider_requests_per_min),
        'secondaryProviderRequestsPerDay': _sv(
            'secondary_provider_requests_per_day', secondary_provider_requests_per_day),
        'providerTokensPerMin': _sv('provider_tokens_per_min', provider_tokens_per_min),
        'secondaryProviderTokensPerMin': _sv(
            'secondary_provider_tokens_per_min', secondary_provider_tokens_per_min),
        # Provider failover (#806).
        'failoverLlmEnabled': _sv('failover_llm_enabled', failover_llm_enabled),
        'failoverLlmProvider': _sv('failover_llm_provider', failover_llm_provider),
        'failoverLlmBaseUrl': _sv('failover_llm_base_url', failover_llm_base_url),
        'failoverLlmTimeoutSeconds': _sv('failover_llm_timeout_seconds', failover_llm_timeout_seconds),
        'failoverLlmMaxRetries': _sv('failover_llm_max_retries', failover_llm_max_retries),
        'failoverLlmDetectionModel': _sv('failover_llm_detection_model', failover_llm_detection_model),
        'failoverLlmReviewModel': _sv('failover_llm_review_model', failover_llm_review_model),
        'failoverLlmVerificationModel': _sv(
            'failover_llm_verification_model', failover_llm_verification_model),
        'failoverLlmChaptersModel': _sv('failover_llm_chapters_model', failover_llm_chapters_model),
        'failoverLlmApiKeyConfigured': failover_llm_api_key_configured,
        'failoverWhisperEnabled': _sv('failover_whisper_enabled', failover_whisper_enabled),
        'failoverWhisperBackend': _sv('failover_whisper_backend', failover_whisper_backend),
        'failoverWhisperModel': _sv('failover_whisper_model', failover_whisper_model),
        'failoverWhisperApiBaseUrl': _sv(
            'failover_whisper_api_base_url', failover_whisper_api_base_url),
        'failoverWhisperApiModel': _sv('failover_whisper_api_model', failover_whisper_api_model),
        'failoverWhisperApiTimeoutSeconds': _sv(
            'failover_whisper_api_timeout_seconds', failover_whisper_api_timeout_seconds),
        'failoverWhisperMaxAttempts': _sv('failover_whisper_max_attempts', failover_whisper_max_attempts),
        'failoverWhisperLanguage': _sv('failover_whisper_language', failover_whisper_language),
        'failoverWhisperApiKeyConfigured': failover_whisper_api_key_configured,
        'failoverProbeIntervalMinutes': _sv(
            'failover_probe_interval_minutes', failover_probe_interval_minutes),
        'failoverRecoveryProbes': _sv('failover_recovery_probes', failover_recovery_probes),
        'providerATimeoutSeconds': _sv('llm_timeout_seconds', provider_a_timeout_seconds),
        'providerAMaxRetries': _sv('llm_max_retries', provider_a_max_retries),
        'providerBTimeoutSeconds': _sv('secondary_llm_timeout_seconds', provider_b_timeout_seconds),
        'providerBMaxRetries': _sv('secondary_llm_max_retries', provider_b_max_retries),
        'whisperMaxAttempts': _sv('whisper_max_attempts', whisper_max_attempts),
        'podcastIndexApiKeyConfigured': bool(podcast_index_api_key),
        'youtubeEnabled': _sv('youtube_enabled', yt_settings.load_global(db).enabled),
        'youtubeApiKeyConfigured': bool(yt_settings.api_key(db)),
        # value is resolved, not raw: unset falls back to PodcastIndex when
        # its credentials exist (pre-option installs keep their behavior),
        # else iTunes. isDefault marks a derived value; saving from the UI
        # makes the choice explicit.
        'podcastSearchProvider': _search_provider_setting(settings),
        # Whether the resolved provider can actually run search right now
        # (AddFeed gates its search UI on this, not just a resolved name).
        'podcastSearchReady': search_provider_ready(),
        'openrouterBaseUrl': OPENROUTER_BASE_URL,
        'whisperBackend': _sv('whisper_backend', whisper_backend),
        'whisperApiBaseUrl': _sv('whisper_api_base_url', whisper_api_base_url),
        'whisperApiKeyConfigured': bool(whisper_api_key),
        'whisperApiModel': _sv('whisper_api_model', whisper_api_model),
        'whisperLanguage': _sv('whisper_language', whisper_language),
        'whisperComputeType': _sv('whisper_compute_type', whisper_compute_type),
        'vadGapDetectionEnabled': _sv('vad_gap_detection_enabled', vad_gap_enabled),
        'vadGapStartMinSeconds': _sv('vad_gap_start_min_seconds', vad_gap_start),
        'vadGapMidMinSeconds': _sv('vad_gap_mid_min_seconds', vad_gap_mid),
        'vadGapTailMinSeconds': _sv('vad_gap_tail_min_seconds', vad_gap_tail),
        'minContentBetweenAdsSeconds': _sv('min_content_between_ads_seconds', min_content_between_ads),
        'adDetectionExcludeStartSeconds': _sv(
            'ad_detection_exclude_start_seconds', ad_detection_exclude_start),
        'maxAdDurationSeconds': _sv('max_ad_duration_seconds', max_ad_duration),
        'maxAdDurationConfirmedSeconds': _sv('max_ad_duration_confirmed_seconds',
                                             max_ad_duration_confirmed),
        'maxLlmWindowAdSeconds': _sv('max_llm_window_ad_seconds', max_llm_window_ad),
        'audioCueDetectionEnabled': _sv('audio_cue_detection_enabled', audio_cue_enabled),
        'audioCueFreqMinHz': _sv('audio_cue_freq_min_hz', audio_cue_freq_min),
        'audioCueFreqMaxHz': _sv('audio_cue_freq_max_hz', audio_cue_freq_max),
        'audioCueProminenceDb': _sv('audio_cue_prominence_db', audio_cue_prominence),
        'audioCueMinConfidence': _sv('audio_cue_min_confidence', audio_cue_min_conf),
        'audioCueCreateFromPairs': _sv('audio_cue_create_from_pairs', audio_cue_create_from_pairs),
        'audioCueCutMatches': _sv('audio_cue_cut_matches', audio_cue_cut_matches),
        'audioCueTemplateScore': _sv('audio_cue_template_score', audio_cue_template_score),
        'audioCueFormantAttenDb': _sv('audio_cue_formant_atten_db', audio_cue_formant_atten),
        'audioCueSnapConfidence': _sv('audio_cue_snap_confidence', audio_cue_snap_conf),
        'audioCueSnapLeadSeconds': _sv('audio_cue_snap_lead_seconds', audio_cue_snap_lead),
        'audioCueSnapLagSeconds': _sv('audio_cue_snap_lag_seconds', audio_cue_snap_lag),
        'audioCueCaptureMinSeconds': _sv('audio_cue_capture_min_seconds', audio_cue_capture_min),
        'audioCueCaptureMaxSeconds': _sv('audio_cue_capture_max_seconds', audio_cue_capture_max),
        'audioCueCaptureMaxIntroSeconds': _sv('audio_cue_capture_max_intro_seconds', audio_cue_capture_max_intro),
        'audioCueCaptureMaxOutroSeconds': _sv('audio_cue_capture_max_outro_seconds', audio_cue_capture_max_outro),
        'audioCuePairConfidence': _sv('audio_cue_pair_confidence', audio_cue_pair_conf),
        'audioCuePairMinBreakSeconds': _sv('audio_cue_pair_min_break_seconds', audio_cue_pair_min_break),
        'audioCuePairMaxBreakSeconds': _sv('audio_cue_pair_max_break_seconds', audio_cue_pair_max_break),
        'audioCuePairMaxBreakFraction': _sv('audio_cue_pair_max_break_fraction', audio_cue_pair_max_break_fraction),
        'audioCuePairOrientWindowSeconds': _sv('audio_cue_pair_orient_window_seconds', audio_cue_pair_orient_window),
        'silenceSnapNoiseDb': _sv('silence_snap_noise_db', silence_snap_noise_db),
        'silenceSnapMinDurationSeconds': _sv('silence_snap_min_duration_seconds', silence_snap_min_duration),
        'silenceSnapMaxDistanceSeconds': _sv('silence_snap_max_distance_seconds', silence_snap_max_distance),
        'verificationMissHoldMinConfidence': _sv(
            'verification_miss_hold_min_confidence', verification_miss_hold_min_confidence),
        'verificationMissAutocutMinConfidence': _sv(
            'verification_miss_autocut_min_confidence', verification_miss_autocut_min_confidence),
        'learningMinConfidence': _sv('learning_min_confidence', learning_min_confidence),
        'learningMinConfidenceLong': _sv('learning_min_confidence_long', learning_min_confidence_long),
        'learningMinPatternDuration': _sv('learning_min_pattern_duration', learning_min_pattern_duration),
        'learningMaxPatternDuration': _sv('learning_max_pattern_duration', learning_max_pattern_duration),
        'differentialMeasuredCorrMax': _sv('differential_measured_corr_max', differential_measured_corr_max),
        'differentialHoldMinSeconds': _sv('differential_hold_min_seconds', differential_hold_min_seconds),
        'daiDifferentialOverridesKeep': _sv(
            'dai_differential_overrides_keep', dai_differential_overrides_keep),
        'spliceVetoEnabled': _sv('splice_veto_enabled', splice_veto_enabled),
        'positionalPriorEnabled': _sv('positional_prior_enabled', positional_prior_enabled),
        'audioBitrate': _sv('audio_bitrate', audio_bitrate),
        'audioEncoderCompressionLevel': _sv(
            'audio_encoder_compression_level', audio_encoder_compression_level),
        'audioReplacementSoundEnabled': _sv('audio_replacement_sound_enabled', audio_replacement_sound_enabled),
        'audioMp3StreamCopyEnabled': _sv('audio_mp3_stream_copy_enabled', audio_mp3_stream_copy_enabled),
        'audioNormalizeEnabled': _sv('audio_normalize_enabled', audio_normalize_enabled),
        'audioNormalizeIntensity': _sv('audio_normalize_intensity', audio_normalize_intensity),
        'skipFlacCompression': _sv('skip_flac_compression', skip_flac),
        'adDetectionParallelWindows': _sv('ad_detection_parallel_windows', parallel_windows),
        'adReviewerParallelAds': _sv('ad_reviewer_parallel_ads', reviewer_parallel),
        'maxArtworkBytes': _sv('max_artwork_bytes', max_artwork_bytes),
        'maxRssBytes': _sv('max_rss_bytes', max_rss_bytes),
        'maxAudioDownloadMb': _sv('max_audio_download_mb', max_audio_download_mb),
        'localUploadMaxMb': _sv('local_upload_max_mb', _int_setting('local_upload_max_mb')),
        'whisperApiTimeoutSeconds': _sv('whisper_api_timeout_seconds', whisper_api_timeout_seconds),
        'transcribeMaxChunkSeconds': _sv('transcribe_max_chunk_seconds', transcribe_max_chunk_seconds),
        'transcribeConcurrentChunks': _sv('transcribe_concurrent_chunks', transcribe_concurrent_chunks),
        'transcribeChunkOverlapSeconds': _sv('transcribe_chunk_overlap_seconds', transcribe_chunk_overlap_seconds),
        'whisperPoolEnabled': _sv('whisper_pool_enabled', whisper_pool_enabled),
        'whisperPoolMaxRequests': _sv('whisper_pool_max_requests', whisper_pool_max_requests),
        'whisperPoolMaxEpisodes': _sv('whisper_pool_max_episodes', whisper_pool_max_episodes),
        'apiKeyConfigured': api_key_configured,
        'retentionDays': int(db.get_setting('retention_days') or '30'),
        'stageTunables': tunables_payload,
        'stageTunableDefaults': {
            payload_key: STAGE_TUNABLE_DEFAULTS[db_key]
            for payload_key, db_key, _ in STAGE_TUNABLE_PAYLOAD_KEYS
        },
    }
    # profile_payload reads the raw {value, is_default} rows, not the SettingEntry view.
    systemone_profiles, systemone_defaults, systemone_shipped = profile_payload(snapshot)
    payload['systemOneTunables'] = systemone_profiles
    payload['systemOneTunablesIsDefault'] = systemone_defaults
    payload['systemOneTunableDefaults'] = systemone_shipped

    # Provider A/B rename (#806): emit the Provider B alias spellings as
    # copies of the secondary_* values for one release.
    for alias, canonical in common.PROVIDER_B_ALIASES.items():
        if canonical in payload and not alias.endswith('ApiKey'):
            payload[alias] = payload[canonical]
    payload['providerBApiKeyConfigured'] = payload['secondaryProviderApiKeyConfigured']

    # Every per-setting default derives from SETTINGS_REGISTRY;
    # openrouterBaseUrl is a fixed constant, not a setting.
    payload['defaults'] = {
        **{spec.payload_key: registry_get_default(key)
           for key, spec in SETTINGS_REGISTRY.items() if spec.payload_key},
        'openrouterBaseUrl': OPENROUTER_BASE_URL,
    }
    return payload


@api.route('/settings', methods=['GET'])
@log_request
def get_settings():
    """Get all settings."""
    return json_response(_build_settings_payload())


def _search_provider_setting(settings):
    """SettingValue for the podcast search provider: the explicit choice
    when one is stored, else the resolver's derived value (which weighs
    PodcastIndex credentials)."""
    explicit = common._setting_value(settings, 'podcast_search_provider', '') or ''
    if explicit in PODCAST_SEARCH_PROVIDERS:
        return {'value': explicit, 'isDefault': False}
    return {'value': resolve_search_provider(), 'isDefault': True}


@api.route('/networks', methods=['GET'])
@log_request
def list_networks():
    """List known podcast networks plus operator-created custom networks.

    Custom networks are distinct free-text network_id_override values set on any
    feed; surfacing them lets a network created on one feed be selected from the
    dropdown on every other feed. For a custom network the id and the display
    name are the same string. Known networks win id collisions.
    """
    from pattern_service import KNOWN_NETWORKS

    networks = {
        network_id: {'id': network_id, 'name': network_id.replace('_', ' ').title()}
        for network_id in KNOWN_NETWORKS.keys()
    }

    db = get_database()
    for override in db.get_custom_network_overrides():
        if override not in networks:
            networks[override] = {'id': override, 'name': override}

    return json_response({
        'networks': sorted(networks.values(), key=lambda x: x['name'])
    })


@api.route('/community-patterns/sync', methods=['POST'])
@limiter.limit('6/hour')
@log_request
def trigger_community_pattern_sync():
    """Force a sync now. Rate-limited to 6 calls per hour.

    A 404 from the upstream manifest URL is expected when the repo hasn't
    published `patterns/community/index.json` to its default branch yet
    (e.g. the feature is still on a feature branch). Surface that as a
    soft 200 with ``status: no_manifest_yet`` rather than a 502, since
    the local instance is healthy and there's nothing the user can do.
    """
    import requests
    from community_sync import sync_now
    db = get_database()
    try:
        summary = sync_now(db)
    except requests.HTTPError as e:
        resp = e.response
        if resp is not None and resp.status_code == 404:
            return json_response({
                'status': 'no_manifest_yet',
                'message': 'Upstream has not published a manifest at this URL yet.',
            })
        return error_response({'message': 'Sync failed', 'reason': str(e)}, 502)
    except Exception as e:
        return error_response({'message': 'Sync failed', 'reason': str(e)}, 502)
    return json_response(summary)


@api.route('/community-patterns/sync-status', methods=['GET'])
@log_request
def community_pattern_sync_status():
    """Return last-sync metadata."""
    return operations.get_community_sync_settings()


@api.route('/community-patterns/all', methods=['DELETE'])
@log_request
def delete_all_community_patterns():
    """Hard-delete every community pattern on this instance.

    Body must include ``{"confirm": true}`` as a fat-finger guard, matching
    the ``/patterns/bulk-delete`` convention. The UI provides the confirm
    step; this endpoint enforces it for any direct API caller too.
    """
    payload = request.get_json(silent=True) or {}
    if payload.get('confirm') is not True:
        return error_response({
            'message': 'confirm: true required to purge all community patterns'
        }, 400)
    db = get_database()
    deleted = db.delete_all_community_patterns()
    return json_response({'deleted': deleted})

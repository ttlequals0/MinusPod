"""Allowlist for the scrubbed demo snapshot: tables, columns and settings.

A column list is the set of columns kept; an empty list means the table is
emptied (DELETE FROM) and kept. Columns needing value rewriting (source_url,
client_ip, last_request_*, secret_hash, label) are kept here and rewritten by the
snapshot script.

snapshot.py deviates from plain "rewrite": secret_hash becomes 64 zeros (CHECK
length = 64); episode_downloads last_request_line and last_request_headers are set
NULL (the request line carries feed keys); settings upserts set is_default = 0;
tables not listed here are emptied and listed by --report-unclassified.

The settings table is row-filtered by DEMO_SETTINGS_ALLOW in snapshot.py;
system_settings keeps every row (production holds only
processing_admission_paused and startup_backfills_version).
"""
import sqlite3
import tempfile
from pathlib import Path

# Tables whose rows are deleted but whose schema is kept. search_index and
# search_index_changes are placeholders so the TABLE_DDL coverage test passes;
# snapshot.py drops them through DROPPED_TABLE_PREFIXES.
_EMPTIED = (
    'processing_runs', 'upload_reservations', 'auto_process_queue',
    'provider_spend_reservations', 'cue_candidate_scans', 'cue_threshold_scans',
    'cue_cross_episode_scans', 'cue_window_optimize_scans', 'auth_failures',
    'search_index_changes', 'search_index', 'text_patterns',
    '_orphaned_audio_fingerprints', 'episode_feed_details', 'episode_snap_stats',
    'audio_fingerprint_matches', 'deferred_upgrade_steps',
)

# Dropped before export; startup recreates them.
DROPPED_TABLE_PREFIXES = ('search_index',)

# Columns removed from otherwise fully kept tables.
_DROP = {
    'podcasts': (
        'download_user_agent_override', 'feed_user_agent_override',
        'detection_notes', 'chapter_notes', 'last_refresh_error',
    ),
    'episodes': ('error_message', 'sb_last_error', 'quality_notes', 'chapters_regen_error'),
    'processing_history': ('error_message', 'log_file'),
    'audio_cue_templates': ('pcm_blob',),
    'llm_call_usage': ('rate_snapshot',),
    'systemone_call_diagnostics': ('diagnostics_json',),
    'pattern_cleanup_runs': ('error',),
    'audio_fingerprints': (
        'source_podcast_slug', 'source_episode_id', 'source_start', 'source_end',
    ),
}

# Kept columns for tables that keep only a subset.
_ONLY = {
    'feed_subscriber_keys': (
        'id', 'podcast_id', 'secret_hash', 'label', 'created_at',
        'last_used_at', 'revoked_at',
    ),
    'ip_geo_cache': ('ip', 'country', 'looked_up_at', 'status'),
    'schema_migrations': ('name', 'applied_at'),
}

# Tables kept with every column.
_KEPT_ALL = (
    'episode_details', 'settings', 'stats', 'system_settings', 'ad_patterns',
    'pattern_corrections', 'episode_downloads', 'known_sponsors',
    'sponsor_normalizations', 'model_pricing', 'token_usage', 'ad_reviewer_log',
    'cue_detections', 'cue_candidate_dismissals', 'podping_hosts',
    'addressing_log', 'pattern_cleanup_suggestions', 'pattern_cleanup_checks',
    'pattern_cleanup_deleted_actions', 'audio_fingerprints', 'failover_events',
)


def create_full_schema(data_dir: Path) -> Path:
    """Build the complete migrated schema (base DDL plus runtime migrations)."""
    from database import Database

    saved = Database._instance
    Database._instance = None
    try:
        db = Database(str(data_dir))
        conn = getattr(db._local, 'connection', None)
        if conn is not None:
            conn.close()
            db._local.connection = None
    finally:
        Database._instance = saved
    return Path(data_dir) / 'podcast.db'


def _build_export_tables() -> dict[str, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(create_full_schema(Path(tmp)))

        def cols(table: str) -> list[str]:
            return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]

        out: dict[str, list[str]] = {t: [] for t in _EMPTIED}
        for table in _KEPT_ALL:
            out[table] = cols(table)
        for table, dropped in _DROP.items():
            out[table] = [c for c in cols(table) if c not in dropped]
        for table, kept in _ONLY.items():
            out[table] = list(kept)
        conn.close()
    return out


EXPORT_TABLES: dict[str, list[str]] = _build_export_tables()

# Table -> (column holding the podcast reference, what it holds: 'id' is
# podcasts.id, 'slug' is podcasts.slug) for cascade deletes. ad_patterns and
# ad_reviewer_log hold the slug in a TEXT podcast_id column.
PODCAST_REF: dict[str, tuple[str, str]] = {
    'episodes': ('podcast_id', 'id'),
    'ad_patterns': ('podcast_id', 'slug'),
    'pattern_corrections': ('podcast_id', 'id'),
    'processing_runs': ('podcast_id', 'id'),
    'upload_reservations': ('podcast_id', 'id'),
    'feed_subscriber_keys': ('podcast_id', 'id'),
    'episode_downloads': ('podcast_id', 'id'),
    'processing_history': ('podcast_id', 'id'),
    'auto_process_queue': ('podcast_id', 'id'),
    'ad_reviewer_log': ('podcast_id', 'slug'),
    'audio_cue_templates': ('podcast_id', 'id'),
    'cue_detections': ('podcast_id', 'id'),
    'cue_candidate_scans': ('podcast_id', 'id'),
    'cue_threshold_scans': ('podcast_id', 'id'),
    'cue_candidate_dismissals': ('podcast_id', 'id'),
    'cue_cross_episode_scans': ('podcast_id', 'id'),
    'llm_call_usage': ('podcast_id', 'id'),
    'systemone_call_diagnostics': ('podcast_id', 'id'),
    'pattern_cleanup_suggestions': ('podcast_slug', 'slug'),
    'pattern_cleanup_checks': ('podcast_slug', 'slug'),
    'pattern_cleanup_deleted_actions': ('podcast_slug', 'slug'),
    'addressing_log': ('podcast_slug', 'slug'),
    'audio_fingerprint_matches': ('podcast_slug', 'slug'),
}

# Table -> (podcast column, episode column, podcast key kind) for rows keyed by
# episode; a podcast column of '' means the table has an episode FK only (its
# episode column is episodes.id).
EPISODE_REF: dict[str, tuple[str, str, str]] = {
    'episode_details': ('', 'episode_id', ''),
    'pattern_corrections': ('podcast_id', 'episode_id', 'id'),
    'processing_runs': ('podcast_id', 'episode_id', 'id'),
    'episode_downloads': ('podcast_id', 'episode_id', 'id'),
    'processing_history': ('podcast_id', 'episode_id', 'id'),
    'auto_process_queue': ('podcast_id', 'episode_id', 'id'),
    'ad_reviewer_log': ('podcast_id', 'episode_id', 'slug'),
    'cue_detections': ('podcast_id', 'episode_id', 'id'),
    'cue_candidate_scans': ('podcast_id', 'episode_id', 'id'),
    'cue_threshold_scans': ('podcast_id', 'episode_id', 'id'),
    'llm_call_usage': ('podcast_id', 'episode_id', 'id'),
    'systemone_call_diagnostics': ('podcast_id', 'episode_id', 'id'),
    'addressing_log': ('podcast_slug', 'episode_id', 'slug'),
}

# Settings classification: manual pass over SETTINGS_REGISTRY, grouped by the
# registry's own headings. Keys in no list below are denied by the regex in the
# tests (credentials, URLs, auth) or listed in DEMO_SETTINGS_EXCLUDE.

_ALLOW_PROMPTS = (
    'system_prompt', 'verification_prompt', 'review_prompt', 'resurrect_prompt',
    'chapter_prompt',
)

_ALLOW_PATTERN_CLEANUP = (
    'pattern_cleanup_prompt', 'pattern_cleanup_enabled', 'pattern_cleanup_cron',
    'pattern_cleanup_batch_size', 'pattern_cleanup_unused_days',
    'pattern_cleanup_schedule_anchor',
)

_ALLOW_AD_REVIEWER = (
    'enable_ad_review', 'review_max_boundary_shift',
)

_ALLOW_GENERAL = (
    'retention_period_minutes', 'keep_original_audio', 'max_feed_episodes',
    'segment_category_actions', 'community_sync_categories',
    'rss_refresh_interval_minutes', 'queue_manual_boost', 'queue_fresh_boost',
    'queue_bulk_boost', 'queue_order', 'session_lifetime_hours', 'session_remember_days',
    'podping_enabled', 'only_expose_processed_default',
    'feed_itunes_block', 'require_manual_approval_default',
    'whats_new_on_upgrade', 'detect_show_segments', 'seed_sponsors_detection',
    'seed_sponsors_verification', 'seed_sponsors_reviewer',
    'seed_sponsors_resurrect', 'text_recurrence_hints', 'ad_addressing_mode',
    'process_new_episodes_first', 'volume_threshold_db',
    'transition_threshold_db', 'min_cut_confidence', 'vtt_transcripts_enabled',
    'chapters_enabled', 'chapters_mode', 'chapters_in_notes',
    'review_links_in_notes', 'skip_second_pass',
    'transcript_differential_enabled', 'differential_fetch_mode',
    'ad_chapters_include_held', 'ad_chapter_title_format',
    'ad_chapter_held_title_format', 'ad_chapter_resume_title',
    'ad_chapter_min_confidence', 'positional_prior_enabled',
)

_ALLOW_AUDIO_OUTPUT = (
    'audio_replacement_sound_enabled', 'audio_mp3_stream_copy_enabled',
    'audio_normalize_enabled', 'audio_normalize_intensity',
)

_ALLOW_TRANSCRIPTION = (
    'transcribe_max_chunk_seconds', 'transcribe_chunk_overlap_seconds',
)

_ALLOW_WHISPER = ('whisper_model', 'whisper_language')

_ALLOW_VAD = (
    'vad_gap_detection_enabled', 'vad_gap_start_min_seconds',
    'vad_gap_mid_min_seconds', 'vad_gap_tail_min_seconds',
    'min_content_between_ads_seconds', 'ad_detection_exclude_start_seconds',
    'max_ad_duration_seconds', 'max_ad_duration_confirmed_seconds',
    'max_llm_window_ad_seconds',
)

_ALLOW_LLM_PROVIDER = (
    'auto_process_enabled', 'auto_process_max_age_hours',
    'download_history_enabled', 'download_geo_lookup_enabled',
    'download_history_retention_days', 'artwork_watermark_enabled',
    'artwork_badge_position', 'audio_bitrate', 'audio_encoder_compression_level',
    'skip_flac_compression', 'ad_detection_max_failed_window_ratio',
    'low_ad_yield_action', 'episode_log_retention_days', 'episode_log_level',
    'local_upload_max_mb', 'youtube_enabled', 'youtube_llm_fallback',
    'youtube_captions_as_transcript', 'youtube_hide_private',
    'youtube_transcribe_fallback', 'youtube_ytdlp_auto_update',
    'youtube_process_delay_minutes', 'youtube_sb_probe_attempts',
    'youtube_sb_probe_interval_minutes',
)

_ALLOW_AUDIO_CUE = (
    'audio_cue_detection_enabled', 'audio_cue_create_from_pairs',
    'audio_cue_cut_matches', 'audio_cue_freq_min_hz', 'audio_cue_freq_max_hz',
    'audio_cue_prominence_db', 'audio_cue_min_confidence',
    'audio_cue_template_score', 'audio_cue_formant_atten_db',
    'audio_cue_snap_confidence', 'audio_cue_snap_lead_seconds',
    'audio_cue_snap_lag_seconds', 'audio_cue_capture_min_seconds',
    'audio_cue_capture_max_seconds', 'audio_cue_capture_max_intro_seconds',
    'audio_cue_capture_max_outro_seconds', 'audio_cue_pair_confidence',
    'audio_cue_pair_min_break_seconds', 'audio_cue_pair_max_break_seconds',
    'audio_cue_pair_max_break_fraction', 'audio_cue_pair_orient_window_seconds',
)

_ALLOW_FINGERPRINT = (
    'fingerprint_enabled', 'fingerprint_match_strictness', 'fingerprint_min_coverage',
    'fingerprint_gap_tolerance_s', 'fingerprint_min_ad_length_s',
)

_ALLOW_SILENCE_SNAP = (
    'silence_snap_noise_db', 'silence_snap_min_duration_seconds',
    'silence_snap_max_distance_seconds',
)

_ALLOW_DETECTION_TUNING = (
    'verification_miss_hold_min_confidence',
    'verification_miss_autocut_min_confidence', 'learning_min_confidence',
    'learning_min_confidence_long', 'learning_min_pattern_duration',
    'learning_max_pattern_duration', 'differential_measured_corr_max',
    'dai_differential_overrides_keep', 'splice_veto_enabled',
    'differential_hold_min_seconds',
)

_ALLOW_STAGE_TUNABLES = (
    'chapter_target_seconds', 'chapter_window_seconds', 'chapter_max_boundaries',
    'chapter_min_duration_seconds', 'window_size_seconds',
    'window_overlap_seconds',
)

DEMO_SETTINGS_ALLOW: frozenset[str] = frozenset(
    _ALLOW_PROMPTS + _ALLOW_PATTERN_CLEANUP + _ALLOW_AD_REVIEWER + _ALLOW_GENERAL
    + _ALLOW_AUDIO_OUTPUT + _ALLOW_TRANSCRIPTION + _ALLOW_WHISPER + _ALLOW_VAD
    + _ALLOW_LLM_PROVIDER + _ALLOW_AUDIO_CUE + _ALLOW_FINGERPRINT + _ALLOW_SILENCE_SNAP
    + _ALLOW_DETECTION_TUNING + _ALLOW_STAGE_TUNABLES
)

# Registry keys neither allowed nor matched by the credential/URL deny regex,
# dropped on purpose (provider/model/failover wiring, spend and rate limits,
# per-operator identifiers, runtime state). The demo uses registry defaults.
DEMO_SETTINGS_EXCLUDE: frozenset[str] = frozenset({
    # Models and failover wiring
    'claude_model', 'verification_model', 'chapters_model', 'detection_provider',
    'verification_provider', 'chapters_provider', 'secondary_provider_enabled',
    'secondary_provider', 'failover_llm_enabled', 'failover_llm_provider',
    'failover_llm_timeout_seconds', 'failover_llm_max_retries',
    'failover_llm_detection_model', 'failover_llm_review_model',
    'failover_llm_verification_model', 'failover_llm_chapters_model',
    'pattern_cleanup_provider', 'pattern_cleanup_model',
    'failover_whisper_enabled', 'failover_whisper_backend',
    'failover_whisper_model', 'failover_whisper_api_model',
    'failover_whisper_api_timeout_seconds', 'failover_whisper_max_attempts',
    'failover_whisper_language', 'failover_probe_interval_minutes',
    'failover_recovery_probes', 'llm_timeout_seconds', 'llm_max_retries',
    'secondary_llm_timeout_seconds', 'secondary_llm_max_retries',
    'whisper_max_attempts',
    # Prompt overrides: the demo uses the registry defaults
    'system_prompt_override', 'verification_prompt_override',
    'review_prompt_override', 'resurrect_prompt_override',
    'chapter_prompt_override',
    # Notifications
    'notification_timezone',
    # Ad reviewer provider
    'review_model', 'review_provider',
    # General processing: queue, spend, operator state
    'offline_queue_enabled', 'offline_queue_ttl_hours', 'rate_limit_hold_enabled',
    'processing_schedule_enabled', 'processing_schedule_start',
    'processing_schedule_end', 'processing_schedule_days',
    'rate_limit_probe_minutes', 'provider_budget_enabled',
    'provider_budget_daily_limit_microusd', 'provider_budget_max_reservations',
    'provider_budget_unknown_cost', 'provider_budget_unknown_reserve_microusd',
    'provider_budget_display_currency', 'provider_budget_fx_rate',
    'provider_budget_fx_source_date', 'provider_budget_fx_fetched_at',
    'processing_soft_timeout_seconds', 'processing_hard_timeout_seconds',
    'jit_blocked_user_agents', 'podping_nodes', 'pricing_source_mode',
    # Transcription and Whisper backend wiring
    'whisper_api_timeout_seconds', 'transcribe_concurrent_chunks',
    'whisper_pool_enabled', 'whisper_pool_max_requests',
    'whisper_pool_max_episodes', 'whisper_backend', 'whisper_api_model',
    'whisper_compute_type',
    # LLM provider wiring, user agents, limits, yt-dlp state
    'omit_temperature', 'llm_json_schema_enabled', 'llm_provider', 'llm_pre_none_identity',
    'ad_detection_parallel_windows', 'ad_reviewer_parallel_ads',
    'reviewer_calibration_on_change', 'download_user_agent', 'feed_user_agent',
    'log_download_query', 'max_artwork_bytes', 'max_rss_bytes',
    'max_audio_download_mb', 'youtube_sb_watch_interval_hours',
    'youtube_sb_watch_max_days', 'youtube_sb_submit_enabled',
    'youtube_ytdlp_version', 'youtube_ytdlp_last_update_at',
    'youtube_ytdlp_last_check_at', 'youtube_ytdlp_last_error',
    'provider_requests_per_min', 'provider_requests_per_day',
    'secondary_provider_requests_per_min', 'secondary_provider_requests_per_day',
    'provider_tokens_per_min', 'secondary_provider_tokens_per_min',
    # Per-operator YouTube SponsorBlock identity (flagged secret)
    'youtube_sb_user_id', 'youtube_sb_user_id_generated',
    # LLM call tuning
    'detection_temperature', 'detection_max_tokens', 'detection_reasoning_budget',
    'detection_reasoning_level', 'verification_temperature',
    'verification_max_tokens', 'verification_reasoning_budget',
    'verification_reasoning_level', 'reviewer_temperature',
    'reviewer_max_tokens', 'reviewer_reasoning_budget',
    'reviewer_reasoning_level', 'chapter_boundary_temperature',
    'chapter_boundary_max_tokens', 'chapter_boundary_reasoning_budget',
    'chapter_boundary_reasoning_level', 'chapter_title_temperature',
    'chapter_title_max_tokens', 'chapter_title_reasoning_budget',
    'chapter_title_reasoning_level', 'ollama_num_ctx',
    'systemone_tunables_primary_typesafe', 'systemone_tunables_primary_compatible',
    'systemone_tunables_secondary_typesafe',
    'systemone_tunables_secondary_compatible',
})

# Forced to a safe value in the demo. The first group is in SETTINGS_REGISTRY.
SETTINGS_FORCE: dict[str, str] = {
    'feed_auth_enabled': 'false',
    'podping_enabled': 'false',
    'download_geo_lookup_enabled': 'false',
    'youtube_ytdlp_auto_update': 'false',
}

# Read by the app but absent from SETTINGS_REGISTRY, so the registry cannot
# validate them. update_check_enabled defaults to true when the row is missing.
SETTINGS_FORCE_UNREGISTERED: dict[str, str] = {
    'update_check_enabled': 'false',
    'community_sync_enabled': 'false',
    'podcast_search_provider': 'itunes',
    'db_backup_enabled': 'false',
    'email_enabled': 'false',
}

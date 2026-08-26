"""Settings registry regression tests.

Pin the byte-level behavior of the SETTINGS_REGISTRY consumers against
snapshots captured from the pre-registry code (four hand-synchronized
catalogs: schema seeding, reset_setting defaults, the bulk reset endpoint
key list, and the GET /settings defaults block). Long prompt values are
compared via sha256 so the snapshot stays readable.
"""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import (
    SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION,
    DEFAULT_COMMUNITY_SYNC_CATEGORIES_JSON,
)
from database import Database
from database.settings import (
    AD_RESET_SETTING_KEYS, SETTINGS_REGISTRY,
    registry_default, registry_get_default,
)

# Env vars that influence seed/reset defaults; cleared for determinism.
_SEED_ENV_VARS = (
    'RETENTION_PERIOD', 'PROCESSING_SOFT_TIMEOUT', 'PROCESSING_HARD_TIMEOUT',
    'WHISPER_MODEL', 'WHISPER_LANGUAGE', 'WHISPER_BACKEND',
    'WHISPER_API_BASE_URL', 'WHISPER_API_MODEL', 'WHISPER_COMPUTE_TYPE',
    'LLM_PROVIDER', 'OPENAI_MODEL', 'OPENAI_BASE_URL',
    'AUDIO_BITRATE', 'SKIP_FLAC_COMPRESSION',
    'AD_DETECTION_PARALLEL_WINDOWS', 'AD_REVIEWER_PARALLEL_ADS',
    'MINUSPOD_MAX_ARTWORK_BYTES', 'MINUSPOD_MAX_RSS_BYTES',
    'MAX_AUDIO_DOWNLOAD_MB', 'AUTO_PROCESS_ENABLED', 'FEED_AUTH_ENABLED',
    'ARTWORK_WATERMARK_ENABLED', 'ARTWORK_BADGE_POSITION',
    'VAD_GAP_DETECTION_ENABLED', 'VAD_GAP_START_MIN_SECONDS',
    'VAD_GAP_MID_MIN_SECONDS', 'VAD_GAP_TAIL_MIN_SECONDS',
    'TRANSCRIBE_MAX_CHUNK_SECONDS', 'TRANSCRIBE_CONCURRENT_CHUNKS',
    'TRANSCRIBE_CHUNK_OVERLAP_SECONDS',
)

# Snapshot of _seed_default_settings output captured from the pre-registry
# code (fresh DB, settings table emptied, env vars above unset).
# value entries: plain string, or ('sha256', hexdigest) for prompt bodies.
SEED_SNAPSHOT = {
    '_review_prompt_migrated': 'true',
    'audio_bitrate': '128k',
    'chapter_prompt': ('sha256', 'ba78ae10ed245f1b215407d2980358cdf6aff6b5f64dfc1662c6f6848cb418b4'),
    'audio_normalize_enabled': 'false',
    'audio_normalize_intensity': 'normal',
    'auto_process_enabled': 'true',
    'chapters_enabled': 'true',
    'community_sync_categories': DEFAULT_COMMUNITY_SYNC_CATEGORIES_JSON,
    'detect_show_segments': '0',
    'seed_sponsors_detection': 'true',
    'seed_sponsors_verification': 'true',
    'seed_sponsors_reviewer': 'true',
    'seed_sponsors_resurrect': 'true',
    'jit_blocked_user_agents': '[]',
    'process_new_episodes_first': '1',
    'differential_hold_min_seconds': '10',
    'differential_measured_corr_max': '0.60',
    'enable_ad_review': 'false',
    'keep_original_audio': 'true',
    'learning_min_confidence': '0.85',
    'learning_min_confidence_long': '0.92',
    'llm_provider': 'anthropic',
    'max_feed_episodes': '300',
    'min_cut_confidence': '0.80',
    'offline_queue_enabled': 'false',
    'offline_queue_ttl_hours': '48',
    'omit_temperature': 'false',
    'only_expose_processed_default': 'false',
    'openai_base_url': 'http://localhost:8000/v1',
    'podping_enabled': 'false',
    'processing_hard_timeout_seconds': '7200',
    'processing_soft_timeout_seconds': '3600',
    'resurrect_prompt': ('sha256', '6f2018ab3abfd51100c89f025bec61f1c57ac8b32139ff71e3907d100ad7cd2b'),  # Updated for semantic is_ad schema line
    'retention_period_minutes': '1440',
    'review_max_boundary_shift': '60',
    'review_model': 'same_as_pass',
    'review_prompt': ('sha256', '39a5e1808bec2b3036231a120e0fb6635f159eb7e1d33b0821dea178f95ce02f'),  # Updated for semantic is_ad schema line
    'rss_refresh_interval_minutes': '15',
    'segment_category_actions': '{}',
    'system_prompt': ('sha256', '082a8f30ee3b475c44b0f5d1af7a9e4d12035cdfdf3da8d427c8c59037acba58'),  # Updated for cross_promo semantics change
    'transcribe_chunk_overlap_seconds': '30',
    'transcribe_concurrent_chunks': '4',
    'whisper_api_timeout_seconds': '600',
    'transcribe_max_chunk_seconds': '600',
    'transition_threshold_db': '3.5',
    'verification_miss_autocut_min_confidence': '0',
    'verification_miss_hold_min_confidence': '0.60',
    'verification_prompt': ('sha256', 'd806d3afc4c443cbac88157cb78b934b4b3f0d72587326485156e5168cd09a7b'),
    'volume_threshold_db': '3.0',
    'vtt_transcripts_enabled': 'true',
    'whisper_language': 'en',
    'whisper_model': 'small',
}

# The pre-registry bulk reset endpoint reset exactly these keys
# (62 hand-enumerated + 23 stage tunables via STAGE_TUNABLE_PAYLOAD_KEYS).
EXPECTED_AD_RESET_KEYS = {
    'system_prompt', 'verification_prompt', 'claude_model',
    'verification_model', 'whisper_model', 'vtt_transcripts_enabled',
    'chapters_enabled', 'chapters_model',
    'min_cut_confidence', 'auto_process_enabled', 'audio_bitrate',
    'audio_normalize_enabled', 'audio_normalize_intensity',
    'whisper_api_timeout_seconds',
    'transcribe_max_chunk_seconds', 'transcribe_concurrent_chunks',
    'transcribe_chunk_overlap_seconds', 'ad_detection_parallel_windows',
    'ad_reviewer_parallel_ads', 'max_artwork_bytes', 'max_rss_bytes',
    'max_audio_download_mb',
    'llm_provider', 'openai_base_url', 'pricing_source_mode',
    'openrouter_api_key',
    'whisper_backend', 'whisper_api_base_url', 'whisper_api_key',
    'whisper_api_model', 'whisper_compute_type', 'whisper_language',
    'skip_flac_compression', 'vad_gap_detection_enabled',
    'vad_gap_start_min_seconds', 'vad_gap_mid_min_seconds',
    'vad_gap_tail_min_seconds',
    'audio_cue_detection_enabled', 'audio_cue_freq_min_hz',
    'audio_cue_freq_max_hz', 'audio_cue_prominence_db',
    'audio_cue_min_confidence', 'audio_cue_template_score',
    'audio_cue_formant_atten_db', 'audio_cue_create_from_pairs',
    'audio_cue_snap_confidence', 'audio_cue_snap_lead_seconds',
    'audio_cue_snap_lag_seconds', 'audio_cue_capture_min_seconds',
    'audio_cue_capture_max_seconds', 'audio_cue_capture_max_intro_seconds',
    'audio_cue_capture_max_outro_seconds', 'audio_cue_pair_confidence',
    'audio_cue_pair_min_break_seconds', 'audio_cue_pair_max_break_seconds',
    'audio_cue_pair_max_break_fraction',
    'audio_cue_pair_orient_window_seconds',
    'detection_temperature', 'detection_max_tokens',
    'detection_reasoning_budget', 'detection_reasoning_level',
    'verification_temperature', 'verification_max_tokens',
    'verification_reasoning_budget', 'verification_reasoning_level',
    'reviewer_temperature', 'reviewer_max_tokens',
    'reviewer_reasoning_budget', 'reviewer_reasoning_level',
    'chapter_boundary_temperature', 'chapter_boundary_max_tokens',
    'chapter_boundary_reasoning_budget', 'chapter_boundary_reasoning_level',
    'chapter_target_seconds', 'chapter_window_seconds',
    'chapter_max_boundaries', 'chapter_min_duration_seconds',
    'chapter_title_temperature', 'chapter_title_max_tokens',
    'chapter_title_reasoning_budget', 'chapter_title_reasoning_level',
    'ollama_num_ctx', 'window_size_seconds', 'window_overlap_seconds',
    'verification_miss_hold_min_confidence',
    'verification_miss_autocut_min_confidence',
    'learning_min_confidence', 'learning_min_confidence_long',
    'differential_measured_corr_max', 'differential_hold_min_seconds',
}

# Keys reset_setting() must refuse (return False). Membership captured from
# the pre-registry code; intentional exclusions include feed_auth_key (reset
# must never wipe a live key) and the *_prompt_override keys (memory obs
# 26236: cleared by reset_prompts_only, not reset_setting).
NON_RESETTABLE_KEYS = (
    'detect_show_segments', 'process_new_episodes_first',
    'enable_ad_review', 'feed_auth_key', 'keep_original_audio',
    'max_feed_episodes', 'offline_queue_enabled', 'offline_queue_ttl_hours',
    'only_expose_processed_default', 'omit_temperature',
    'podping_enabled', 'positional_prior_enabled',
    'processing_hard_timeout_seconds', 'processing_soft_timeout_seconds',
    'retention_days', 'review_max_boundary_shift', 'review_model',
    'rss_refresh_interval_minutes', 'segment_category_actions',
    'community_sync_categories',
    'system_prompt_override', 'verification_prompt_override',
    'review_prompt_override', 'resurrect_prompt_override',
    'chapter_prompt_override',
    'transition_threshold_db', 'volume_threshold_db',
    'nonexistent_key_xyz',
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in _SEED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _settings_rows(db):
    conn = db.get_connection()
    return {
        row['key']: (row['value'], row['is_default'])
        for row in conn.execute(
            "SELECT key, value, is_default FROM settings")
    }


def _assert_value(key, actual, expected):
    if isinstance(expected, tuple):
        digest = hashlib.sha256(actual.encode()).hexdigest()
        assert digest == expected[1], f"{key}: prompt hash changed"
    else:
        assert actual == expected, f"{key}: {actual!r} != {expected!r}"


class TestSeedSnapshot:
    def test_seed_matches_pre_registry_snapshot(self, temp_db, clean_env):
        conn = temp_db.get_connection()
        conn.execute("DELETE FROM settings")
        conn.commit()
        temp_db._seed_default_settings(conn)
        rows = _settings_rows(temp_db)

        assert set(rows) == set(SEED_SNAPSHOT), (
            f"seeded key set drifted: only-seeded="
            f"{set(rows) - set(SEED_SNAPSHOT)} "
            f"only-snapshot={set(SEED_SNAPSHOT) - set(rows)}")
        for key, expected in SEED_SNAPSHOT.items():
            value, is_default = rows[key]
            assert is_default == 1, f"{key} seeded with is_default={is_default}"
            _assert_value(key, value, expected)

    def test_seed_with_openai_model_env_populates_model_keys(
            self, temp_db, clean_env, monkeypatch):
        monkeypatch.setenv('OPENAI_MODEL', 'gpt-5-mini')
        conn = temp_db.get_connection()
        conn.execute("DELETE FROM settings")
        conn.commit()
        temp_db._seed_default_settings(conn)
        rows = _settings_rows(temp_db)

        for key in ('claude_model', 'verification_model', 'chapters_model'):
            assert rows[key] == ('gpt-5-mini', 1), f"{key}: {rows[key]!r}"

    def test_seed_without_openai_model_env_leaves_model_keys_unset(
            self, temp_db, clean_env):
        conn = temp_db.get_connection()
        conn.execute("DELETE FROM settings")
        conn.commit()
        temp_db._seed_default_settings(conn)
        rows = _settings_rows(temp_db)

        for key in ('claude_model', 'verification_model', 'chapters_model'):
            assert key not in rows, f"{key} unexpectedly seeded: {rows.get(key)!r}"

    def test_fresh_db_init_registry_rows(self, clean_env, tmp_path):
        # On a fresh full init, migrations run before _migrate_from_json, so
        # env-backed and reviewer keys are inserted by migrations and the
        # legacy seed path is skipped. Pin the registry-owned subset.
        Database._instance = None
        try:
            db = Database(data_dir=str(tmp_path))
            rows = _settings_rows(db)
        finally:
            Database._instance = None
        expected = {
            'ad_detection_parallel_windows': '4',
            'ad_reviewer_parallel_ads': '4',
            'artwork_watermark_enabled': 'false',
            'audio_bitrate': '128k',
            'auto_process_enabled': 'true',
            'enable_ad_review': 'false',
            'feed_auth_enabled': 'false',
            'llm_provider': 'anthropic',
            'max_artwork_bytes': '26214400',
            'max_audio_download_mb': '500',
            'max_rss_bytes': '209715200',
            'review_max_boundary_shift': '60',
            'review_model': 'same_as_pass',
            'skip_flac_compression': 'false',
            'review_prompt': SEED_SNAPSHOT['review_prompt'],
            'resurrect_prompt': SEED_SNAPSHOT['resurrect_prompt'],
        }
        for key, exp in expected.items():
            assert key in rows, f"fresh init missing {key}"
            value, is_default = rows[key]
            assert is_default == 1, f"{key} is_default={is_default}"
            _assert_value(key, value, exp)


class TestResetSetting:
    def test_non_resettable_keys_return_false(self, temp_db, clean_env):
        for key in NON_RESETTABLE_KEYS:
            assert temp_db.reset_setting(key) is False, (
                f"{key} unexpectedly resettable")

    def test_reset_does_not_wipe_feed_auth_key(self, temp_db, clean_env):
        temp_db.set_setting('feed_auth_key', 'live-key', is_default=False)
        assert temp_db.reset_setting('feed_auth_key') is False
        assert temp_db.get_setting('feed_auth_key') == 'live-key'

    def test_reset_values_match_pre_registry_defaults(self, temp_db, clean_env):
        expected = {
            'min_cut_confidence': '0.80',
            'retention_period_minutes': '1440',
            'whisper_backend': 'local',
            'whisper_api_model': 'whisper-1',
            'vad_gap_mid_min_seconds': '8.0',
            'min_content_between_ads_seconds': '12.0',
            'audio_cue_freq_min_hz': '1500',
            'audio_cue_pair_orient_window_seconds': '20.0',
            'silence_snap_noise_db': '-50.0',
            'pricing_source_mode': 'auto',
            'transcribe_max_chunk_seconds': '600',
        }
        for key, value in expected.items():
            temp_db.set_setting(key, 'customized', is_default=False)
            assert temp_db.reset_setting(key) is True
            rows = _settings_rows(temp_db)
            assert rows[key] == (value, 1), (
                f"{key}: {rows[key]!r} != {(value, 1)!r}")

    def test_reset_model_key_clears_row_without_env(self, temp_db, clean_env):
        for key in ('claude_model', 'verification_model', 'chapters_model'):
            temp_db.set_setting(key, 'openai/gpt-stale', is_default=False)
            assert temp_db.reset_setting(key) is True
            assert temp_db.get_setting(key) is None

    def test_reset_model_key_uses_env_when_set(self, temp_db, clean_env, monkeypatch):
        monkeypatch.setenv('OPENAI_MODEL', 'gpt-5-mini')
        temp_db.set_setting('claude_model', 'openai/gpt-stale', is_default=False)
        assert temp_db.reset_setting('claude_model') is True
        assert temp_db.get_setting('claude_model') == 'gpt-5-mini'

    def test_reset_prompt_restores_default_text(self, temp_db, clean_env):
        temp_db.set_setting('system_prompt', 'my prompt', is_default=False)
        assert temp_db.reset_setting('system_prompt') is True
        value = temp_db.get_setting('system_prompt')
        _assert_value('system_prompt', value, SEED_SNAPSHOT['system_prompt'])

    def test_reset_stage_tunable_clears_row(self, temp_db, clean_env):
        temp_db.set_setting('detection_temperature', '0.7', is_default=False)
        assert temp_db.reset_setting('detection_temperature') is True
        rows = _settings_rows(temp_db)
        assert rows['detection_temperature'] == ('', 1)

    def test_reset_secret_deletes_row(self, temp_db, clean_env):
        temp_db.set_setting('openrouter_api_key', 'sk-or-abc', is_default=False)
        assert temp_db.reset_setting('openrouter_api_key') is True
        assert temp_db.get_setting('openrouter_api_key') is None

    def test_reset_env_backed_uses_env_default(self, temp_db, monkeypatch):
        monkeypatch.setenv('AUDIO_BITRATE', '192k')
        temp_db.set_setting('audio_bitrate', '256k', is_default=False)
        assert temp_db.reset_setting('audio_bitrate') is True
        rows = _settings_rows(temp_db)
        assert rows['audio_bitrate'] == ('192k', 1)


class TestAdResetKeyList:
    def test_derived_key_list_matches_pre_registry_endpoint(self):
        assert set(AD_RESET_SETTING_KEYS) == EXPECTED_AD_RESET_KEYS
        assert len(AD_RESET_SETTING_KEYS) == len(EXPECTED_AD_RESET_KEYS)


class TestGetDefaults:
    def test_payload_defaults_match_pre_registry_values(self, clean_env):
        expected = {
            'minCutConfidence': 0.80,
            'maxFeedEpisodes': 300,
            'enableAdReview': False,
            'reviewModel': 'same_as_pass',
            'reviewMaxBoundaryShift': 60,
            'vttTranscriptsEnabled': True,
            'onlyExposeProcessedDefault': False,
            'whisperModel': 'small',
            'whisperBackend': 'local',
            'whisperLanguage': 'en',
            'whisperComputeType': 'auto',
            'vadGapDetectionEnabled': True,
            'vadGapMidMinSeconds': 8.0,
            'minContentBetweenAdsSeconds': 12.0,
            'audioCueFreqMinHz': 1500,
            'audioCueProminenceDb': 9.0,
            'silenceSnapNoiseDb': -50.0,
            'audioBitrate': '128k',
            'audioNormalizeEnabled': False,
            'audioNormalizeIntensity': 'normal',
            'skipFlacCompression': False,
            'adDetectionParallelWindows': 4,
            'adReviewerParallelAds': 4,
            'maxArtworkBytes': 26214400,
            'maxRssBytes': 209715200,
            'maxAudioDownloadMb': 500,
            'transcribeMaxChunkSeconds': 600,
            'transcribeConcurrentChunks': 4,
            'transcribeChunkOverlapSeconds': 30,
            'llmProvider': 'anthropic',
            'openaiBaseUrl': 'http://localhost:8000/v1',
            'pricingSourceMode': 'auto',
            'autoProcessEnabled': True,
            'feedAuthEnabled': False,
            'artworkWatermarkEnabled': False,
            'positionalPriorEnabled': False,
            'segmentCategoryActions': {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES},
            'communitySyncCategories': list(SEGMENT_CATEGORIES),
        }
        payload = {
            spec.payload_key: registry_get_default(key)
            for key, spec in SETTINGS_REGISTRY.items() if spec.payload_key
        }
        for name, value in expected.items():
            assert payload[name] == value, (
                f"{name}: {payload[name]!r} != {value!r}")
            assert type(payload[name]) is type(value), (
                f"{name}: type {type(payload[name]).__name__}")

    def test_payload_key_set_matches_pre_registry_defaults_block(self):
        # The pre-registry defaults block had 68 entries: 67 per-setting
        # defaults plus openrouterBaseUrl (a constant the endpoint adds
        # separately). Notably audioCuePairOrientWindowSeconds was absent
        # from it -- preserve that. 2.76.0 added six detection-tuning
        # payload keys (67 -> 73). rssRefreshIntervalMinutes added after
        # (73 -> 74). podpingEnabled added after that (74 -> 75).
        # segmentCategoryActions added after that (75 -> 76).
        # omitTemperature added after that (76 -> 77).
        # communitySyncCategories added after that (77 -> 78).
        # maxAdDurationSeconds + maxAdDurationConfirmedSeconds (78 -> 80).
        # whisperApiTimeoutSeconds added after that (80 -> 81).
        # chapterPrompt added after that (81 -> 82).
        # artworkBadgePosition added after that (82 -> 83).
        # detectShowSegments added after that (83 -> 84).
        # processNewEpisodesFirst added after that (84 -> 85).
        # jitBlockedUserAgents added after that (85 -> 86).
        # lowAdYieldAction added after that (86 -> 87).
        # episodeLogRetentionDays + episodeLogLevel added after that (87 -> 89).
        # seed_sponsors_detection + seed_sponsors_verification +
        # seed_sponsors_reviewer + seed_sponsors_resurrect added after that (89 -> 93).
        payload_keys = {
            spec.payload_key for spec in SETTINGS_REGISTRY.values()
            if spec.payload_key
        }
        assert len(payload_keys) == 93
        assert 'audioCuePairOrientWindowSeconds' not in payload_keys
        assert 'audioCuePairMaxBreakFraction' in payload_keys

    def test_registry_default_strings(self, clean_env):
        assert registry_default('min_cut_confidence') == '0.80'
        assert registry_default('whisper_language') == 'en'
        assert registry_default('audio_cue_freq_max_hz') == '8000'


class TestShippedPromptsTrackTheDefault:
    """Seeding only ever inserted, so an install kept whatever prompt shipped
    when its database was created. One instance was still running an 8442-char
    system prompt with no category guidance while the shipped default was
    10408 chars and required a category on every ad, which is why per-category
    actions never applied.
    """

    def test_the_prompts_are_marked_refreshable(self):
        from database.settings import SETTINGS_REGISTRY
        for key in ('system_prompt', 'verification_prompt',
                    'review_prompt', 'resurrect_prompt', 'chapter_prompt'):
            assert SETTINGS_REGISTRY[key].refresh_default, key

    def test_nothing_else_is_refreshable(self):
        """A user-visible tunable must not be silently reset on upgrade."""
        from database.settings import SETTINGS_REGISTRY
        refreshable = {k for k, s in SETTINGS_REGISTRY.items() if s.refresh_default}
        assert refreshable == {'system_prompt', 'verification_prompt',
                               'review_prompt', 'resurrect_prompt',
                               'chapter_prompt'}

    def test_refreshable_defaults_report_current_text(self):
        from database.settings import iter_refreshable_defaults
        from utils.constants import DEFAULT_SYSTEM_PROMPT
        values = dict(iter_refreshable_defaults())
        assert values['system_prompt'] == DEFAULT_SYSTEM_PROMPT
        assert 'CATEGORY:' in values['system_prompt']

"""Settings mixin for MinusPod database."""
import os
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable, List

from config import (
    normalize_model_key, ENV_BACKED_SETTINGS, resolve_env_backed_default,
    coerce_bool_setting, get_env_backed_int,
    STAGE_TUNABLE_DEFAULTS,
    DEFAULT_AD_DETECTION_MODEL, PROVIDER_ANTHROPIC,
    DEFAULT_OPENAI_BASE_URL,
    WHISPER_COMPUTE_TYPE_DEFAULT,
    AD_DETECTION_PARALLEL_WINDOWS_DEFAULT,
    AD_REVIEWER_PARALLEL_ADS_DEFAULT,
    MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX, MAX_RSS_BYTES_MIN,
    MAX_AUDIO_DOWNLOAD_MB_MIN,
    MIN_CONTENT_BETWEEN_ADS_SECONDS,
    AUDIO_CUE_FREQ_MIN_HZ, AUDIO_CUE_FREQ_MAX_HZ, AUDIO_CUE_PROMINENCE_DB,
    AUDIO_CUE_MIN_CONFIDENCE, AUDIO_CUE_TEMPLATE_SCORE,
    AUDIO_CUE_FORMANT_ATTEN_DB,
    AUDIO_CUE_SNAP_CONFIDENCE, AUDIO_CUE_SNAP_LEAD_SECONDS,
    AUDIO_CUE_SNAP_LAG_SECONDS,
    AUDIO_CUE_CAPTURE_MIN_SECONDS, AUDIO_CUE_CAPTURE_MAX_SECONDS,
    AUDIO_CUE_CAPTURE_MAX_INTRO_SECONDS, AUDIO_CUE_CAPTURE_MAX_OUTRO_SECONDS,
    AUDIO_CUE_PAIR_CONFIDENCE, AUDIO_CUE_PAIR_MIN_BREAK_SECONDS,
    AUDIO_CUE_PAIR_MAX_BREAK_SECONDS, AUDIO_CUE_PAIR_MAX_BREAK_FRACTION,
    AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS,
    SILENCE_SNAP_NOISE_DB, SILENCE_SNAP_MIN_DURATION_SECONDS,
    SILENCE_SNAP_MAX_DISTANCE_SECONDS,
)
from secrets_crypto import (
    CryptoUnavailableError, decrypt, encrypt, is_ciphertext,
    SECRET_SETTING_KEYS,
)

logger = logging.getLogger(__name__)

# Default pricing for known Anthropic models (USD per 1M tokens)
# claude-sonnet-5/fable-5/opus-4-8 values from LiteLLM 2026-07-02.
DEFAULT_MODEL_PRICING = {
    'claude-sonnet-5':            {'name': 'Claude Sonnet 5',   'input': 3.0,  'output': 15.0},
    'claude-fable-5':             {'name': 'Claude Fable 5',    'input': 10.0, 'output': 50.0},
    'claude-opus-4-8':            {'name': 'Claude Opus 4.8',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-7':            {'name': 'Claude Opus 4.7',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-6':            {'name': 'Claude Opus 4.6',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-5-20251101':   {'name': 'Claude Opus 4.5',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-1-20250805':   {'name': 'Claude Opus 4.1',   'input': 15.0, 'output': 75.0},
    'claude-opus-4-20250514':     {'name': 'Claude Opus 4',     'input': 15.0, 'output': 75.0},
    'claude-sonnet-4-6':          {'name': 'Claude Sonnet 4.6', 'input': 3.0,  'output': 15.0},
    'claude-sonnet-4-5-20250929': {'name': 'Claude Sonnet 4.5', 'input': 3.0,  'output': 15.0},
    'claude-sonnet-4-20250514':   {'name': 'Claude Sonnet 4',   'input': 3.0,  'output': 15.0},
    'claude-haiku-4-5-20251001':  {'name': 'Claude Haiku 4.5',  'input': 1.0,  'output': 5.0},
}


# ========== Global settings registry ==========
#
# Single source of truth for global settings defaults. Four consumers derive
# from it (previously four hand-synchronized catalogs; see Issue #301 and the
# audio-cue reset gap for the drift bugs that motivated this):
#   1. schema seeding: iter_seed_defaults() (database/schema/__init__.py)
#   2. reset_setting() below
#   3. the bulk reset endpoint key list: AD_RESET_SETTING_KEYS (api/settings.py)
#   4. GET /settings 'defaults' payload: registry_get_default() (api/settings.py)
#
# Env-backed keys (ENV_BACKED_SETTINGS) and stage tunables
# (STAGE_TUNABLE_DEFAULTS) keep their existing resolution mechanisms;
# their registry entries carry membership flags only.


def _default_system_prompt() -> str:
    from database import DEFAULT_SYSTEM_PROMPT
    return DEFAULT_SYSTEM_PROMPT


def _default_verification_prompt() -> str:
    from database import DEFAULT_VERIFICATION_PROMPT
    return DEFAULT_VERIFICATION_PROMPT


def _default_review_prompt() -> str:
    from database import DEFAULT_REVIEW_PROMPT
    return DEFAULT_REVIEW_PROMPT


def _default_resurrect_prompt() -> str:
    from database import DEFAULT_RESURRECT_PROMPT
    return DEFAULT_RESURRECT_PROMPT


def _seed_env_openai_model() -> Optional[str]:
    """OPENAI_MODEL when the *env-configured* provider is non-Anthropic.

    Seed-time only: the settings table is empty when seeding runs, so the
    provider can only come from the environment.
    """
    provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower()
    return os.environ.get('OPENAI_MODEL') if provider != 'anthropic' else None


def _seed_detection_model() -> str:
    return _seed_env_openai_model() or DEFAULT_AD_DETECTION_MODEL


def _seed_chapters_model() -> str:
    from chapters_generator import CHAPTERS_MODEL
    return _seed_env_openai_model() or CHAPTERS_MODEL


def _reset_detection_model() -> str:
    """Provider-aware default at reset time (effective provider reads DB)."""
    from llm_client import get_effective_provider
    if get_effective_provider() != PROVIDER_ANTHROPIC:
        return os.environ.get('OPENAI_MODEL') or DEFAULT_AD_DETECTION_MODEL
    return DEFAULT_AD_DETECTION_MODEL


def _reset_chapters_model() -> str:
    from chapters_generator import CHAPTERS_MODEL
    from llm_client import get_effective_provider
    if get_effective_provider() != PROVIDER_ANTHROPIC:
        return os.environ.get('OPENAI_MODEL') or CHAPTERS_MODEL
    return CHAPTERS_MODEL


def _payload_chapters_model() -> str:
    from chapters_generator import CHAPTERS_MODEL
    return CHAPTERS_MODEL


def _payload_max_artwork_bytes() -> int:
    return get_env_backed_int('max_artwork_bytes', floor=MAX_ARTWORK_BYTES_MIN,
                              ceiling=MAX_ARTWORK_BYTES_MAX, settings={})


def _payload_max_rss_bytes() -> int:
    return get_env_backed_int('max_rss_bytes', floor=MAX_RSS_BYTES_MIN,
                              settings={})


def _payload_max_audio_download_mb() -> int:
    return get_env_backed_int('max_audio_download_mb',
                              floor=MAX_AUDIO_DOWNLOAD_MB_MIN, settings={})


@dataclass(frozen=True)
class SettingSpec:
    """One global setting.

    default:        static DB-string default.
    env:            env var consulted by registry_default() before `default`.
    env_blank_is_unset: `os.environ.get(env) or default` semantics (a blank
                    env value falls through to `default`).
    factory:        lazy default (prompt constants, provider-aware models);
                    wins over env/default.
    reset_factory:  reset-time override when reset must differ from the
                    seed-time default (e.g. provider-aware model defaults
                    read the DB-effective provider at reset).
    secret:         reset clears via clear_secret (SECRET_SETTING_KEYS).
    stage_tunable:  reset clears the row; DB > env > default at read time.
    env_backed:     ENV_BACKED_SETTINGS owns the default (env var, fallback,
                    validator); registry_default() delegates to
                    resolve_env_backed_default(key), so these specs carry no
                    default/env of their own.
    seeded:         _seed_default_settings inserts this key.
    resettable:     False = reset_setting() refuses (returns False). Keys
                    like feed_auth_key and the *_prompt_override keys are
                    deliberately not resettable; do not flip these to True
                    without checking the original exclusion rationale.
    in_ad_reset:    member of the bulk POST /settings/ad-detection/reset list.
    payload_key:    camelCase name in the GET /settings 'defaults' block
                    (None = absent from that block).
    payload_kind:   coercion for the defaults block: str|bool|int|float.
    payload_factory: overrides payload_kind coercion entirely.
    """
    default: Optional[str] = None
    env: Optional[str] = None
    env_blank_is_unset: bool = False
    factory: Optional[Callable[[], str]] = None
    reset_factory: Optional[Callable[[], str]] = None
    secret: bool = False
    stage_tunable: bool = False
    env_backed: bool = False
    seeded: bool = False
    resettable: bool = True
    in_ad_reset: bool = False
    payload_key: Optional[str] = None
    payload_kind: str = 'str'
    payload_factory: Optional[Callable[[], Any]] = None


SETTINGS_REGISTRY: Dict[str, SettingSpec] = {
    # -- Prompts --
    'system_prompt': SettingSpec(
        factory=_default_system_prompt, seeded=True, in_ad_reset=True,
        payload_key='systemPrompt'),
    'verification_prompt': SettingSpec(
        factory=_default_verification_prompt, seeded=True, in_ad_reset=True,
        payload_key='verificationPrompt'),
    'review_prompt': SettingSpec(
        factory=_default_review_prompt, seeded=True,
        payload_key='reviewPrompt'),
    'resurrect_prompt': SettingSpec(
        factory=_default_resurrect_prompt, seeded=True,
        payload_key='resurrectPrompt'),
    # Per-pass prompt overrides: intentionally NOT resettable via
    # reset_setting (reset_prompts_only clears them explicitly; empty string
    # is the no-override default state, not a registry default).
    'system_prompt_override': SettingSpec(resettable=False),
    'verification_prompt_override': SettingSpec(resettable=False),
    'review_prompt_override': SettingSpec(resettable=False),
    'resurrect_prompt_override': SettingSpec(resettable=False),

    # -- Models --
    'claude_model': SettingSpec(
        reset_factory=_reset_detection_model, in_ad_reset=True,
        payload_key='claudeModel',
        payload_factory=lambda: DEFAULT_AD_DETECTION_MODEL),
    'verification_model': SettingSpec(
        factory=_seed_detection_model, reset_factory=_reset_detection_model,
        seeded=True, in_ad_reset=True, payload_key='verificationModel',
        payload_factory=lambda: DEFAULT_AD_DETECTION_MODEL),
    'chapters_model': SettingSpec(
        factory=_seed_chapters_model, reset_factory=_reset_chapters_model,
        seeded=True, in_ad_reset=True, payload_key='chaptersModel',
        payload_factory=_payload_chapters_model),

    # -- Ad reviewer (seeded; only the prompts are resettable) --
    'enable_ad_review': SettingSpec(
        default='false', seeded=True, resettable=False,
        payload_key='enableAdReview', payload_kind='bool'),
    'review_model': SettingSpec(
        default='same_as_pass', seeded=True, resettable=False,
        payload_key='reviewModel'),
    'review_max_boundary_shift': SettingSpec(
        default='60', seeded=True, resettable=False,
        payload_key='reviewMaxBoundaryShift', payload_kind='int'),

    # -- General processing --
    'retention_period_minutes': SettingSpec(
        default='1440', env='RETENTION_PERIOD', seeded=True),
    'keep_original_audio': SettingSpec(
        default='true', seeded=True, resettable=False),
    'offline_queue_enabled': SettingSpec(
        default='false', seeded=True, resettable=False),
    'offline_queue_ttl_hours': SettingSpec(
        default='48', seeded=True, resettable=False),
    'processing_soft_timeout_seconds': SettingSpec(
        default='3600', env='PROCESSING_SOFT_TIMEOUT', seeded=True,
        resettable=False),
    'processing_hard_timeout_seconds': SettingSpec(
        default='7200', env='PROCESSING_HARD_TIMEOUT', seeded=True,
        resettable=False),
    'max_feed_episodes': SettingSpec(
        default='300', seeded=True, resettable=False,
        payload_key='maxFeedEpisodes', payload_kind='int'),
    'rss_refresh_interval_minutes': SettingSpec(
        default='15', seeded=True, resettable=False,
        payload_key='rssRefreshIntervalMinutes', payload_kind='int'),
    'podping_enabled': SettingSpec(
        default='false', seeded=True, resettable=False,
        payload_key='podpingEnabled', payload_kind='bool'),
    'only_expose_processed_default': SettingSpec(
        default='false', seeded=True, resettable=False,
        payload_key='onlyExposeProcessedDefault', payload_kind='bool'),
    'volume_threshold_db': SettingSpec(
        default='3.0', seeded=True, resettable=False),
    'transition_threshold_db': SettingSpec(
        default='3.5', seeded=True, resettable=False),
    'min_cut_confidence': SettingSpec(
        default='0.80', seeded=True, in_ad_reset=True,
        payload_key='minCutConfidence', payload_kind='float'),
    'vtt_transcripts_enabled': SettingSpec(
        default='true', seeded=True, in_ad_reset=True,
        payload_key='vttTranscriptsEnabled', payload_kind='bool'),
    'chapters_enabled': SettingSpec(
        default='true', seeded=True, in_ad_reset=True,
        payload_key='chaptersEnabled', payload_kind='bool'),
    'pricing_source_mode': SettingSpec(
        default='auto', in_ad_reset=True,
        payload_key='pricingSourceMode'),
    # feed_auth_key is deliberately not resettable: reset must never wipe a
    # live key (rotation is an explicit action).
    'feed_auth_key': SettingSpec(resettable=False),
    # positional_prior_enabled has a GET default but no reset path today.
    'positional_prior_enabled': SettingSpec(
        default='false', resettable=False,
        payload_key='positionalPriorEnabled', payload_kind='bool'),

    # -- Audio output --
    'audio_normalize_enabled': SettingSpec(
        default='false', seeded=True, in_ad_reset=True,
        payload_key='audioNormalizeEnabled', payload_kind='bool'),
    'audio_normalize_intensity': SettingSpec(
        default='normal', seeded=True, in_ad_reset=True,
        payload_key='audioNormalizeIntensity'),

    # -- Transcription (seed uses the static default; reset honors env) --
    'transcribe_max_chunk_seconds': SettingSpec(
        default='600', seeded=True, in_ad_reset=True,
        reset_factory=lambda: os.environ.get('TRANSCRIBE_MAX_CHUNK_SECONDS', '600'),
        payload_key='transcribeMaxChunkSeconds', payload_kind='int'),
    'transcribe_concurrent_chunks': SettingSpec(
        default='4', seeded=True, in_ad_reset=True,
        reset_factory=lambda: os.environ.get('TRANSCRIBE_CONCURRENT_CHUNKS', '4'),
        payload_key='transcribeConcurrentChunks', payload_kind='int'),
    'transcribe_chunk_overlap_seconds': SettingSpec(
        default='30', seeded=True, in_ad_reset=True,
        reset_factory=lambda: os.environ.get('TRANSCRIBE_CHUNK_OVERLAP_SECONDS', '30'),
        payload_key='transcribeChunkOverlapSeconds', payload_kind='int'),

    # -- Whisper --
    'whisper_model': SettingSpec(
        default='small', env='WHISPER_MODEL', seeded=True, in_ad_reset=True,
        payload_key='whisperModel'),
    'whisper_language': SettingSpec(
        default='en', env='WHISPER_LANGUAGE', env_blank_is_unset=True,
        seeded=True, in_ad_reset=True, payload_key='whisperLanguage'),
    'whisper_backend': SettingSpec(
        default='local', env='WHISPER_BACKEND', in_ad_reset=True,
        payload_key='whisperBackend'),
    'whisper_api_base_url': SettingSpec(
        default='', env='WHISPER_API_BASE_URL', in_ad_reset=True,
        payload_key='whisperApiBaseUrl'),
    'whisper_api_model': SettingSpec(
        default='whisper-1', env='WHISPER_API_MODEL', in_ad_reset=True,
        payload_key='whisperApiModel'),
    'whisper_compute_type': SettingSpec(
        default=WHISPER_COMPUTE_TYPE_DEFAULT, env='WHISPER_COMPUTE_TYPE',
        in_ad_reset=True, payload_key='whisperComputeType'),

    # -- VAD gap detection --
    'vad_gap_detection_enabled': SettingSpec(
        default='true', env='VAD_GAP_DETECTION_ENABLED', in_ad_reset=True,
        payload_key='vadGapDetectionEnabled', payload_kind='bool'),
    'vad_gap_start_min_seconds': SettingSpec(
        default='3.0', env='VAD_GAP_START_MIN_SECONDS', in_ad_reset=True,
        payload_key='vadGapStartMinSeconds', payload_kind='float'),
    'vad_gap_mid_min_seconds': SettingSpec(
        default='8.0', env='VAD_GAP_MID_MIN_SECONDS', in_ad_reset=True,
        payload_key='vadGapMidMinSeconds', payload_kind='float'),
    'vad_gap_tail_min_seconds': SettingSpec(
        default='3.0', env='VAD_GAP_TAIL_MIN_SECONDS', in_ad_reset=True,
        payload_key='vadGapTailMinSeconds', payload_kind='float'),
    'min_content_between_ads_seconds': SettingSpec(
        default=str(MIN_CONTENT_BETWEEN_ADS_SECONDS),
        payload_key='minContentBetweenAdsSeconds', payload_kind='float'),

    # -- LLM provider (env-backed keys resolve via ENV_BACKED_SETTINGS) --
    'llm_provider': SettingSpec(
        env_backed=True, seeded=True,
        in_ad_reset=True, payload_key='llmProvider',
        payload_factory=lambda: resolve_env_backed_default('llm_provider')),
    'openai_base_url': SettingSpec(
        default=DEFAULT_OPENAI_BASE_URL, env='OPENAI_BASE_URL', seeded=True,
        in_ad_reset=True, payload_key='openaiBaseUrl'),
    'auto_process_enabled': SettingSpec(
        env_backed=True, seeded=True, in_ad_reset=True,
        payload_key='autoProcessEnabled',
        payload_factory=lambda: coerce_bool_setting(
            resolve_env_backed_default('auto_process_enabled'))),
    'feed_auth_enabled': SettingSpec(
        env_backed=True,
        payload_key='feedAuthEnabled',
        payload_factory=lambda: coerce_bool_setting(
            resolve_env_backed_default('feed_auth_enabled'))),
    'artwork_watermark_enabled': SettingSpec(
        env_backed=True,
        payload_key='artworkWatermarkEnabled',
        payload_factory=lambda: coerce_bool_setting(
            resolve_env_backed_default('artwork_watermark_enabled'))),
    'audio_bitrate': SettingSpec(
        env_backed=True, seeded=True,
        in_ad_reset=True, payload_key='audioBitrate'),
    'skip_flac_compression': SettingSpec(
        env_backed=True,
        in_ad_reset=True, payload_key='skipFlacCompression',
        payload_factory=lambda: coerce_bool_setting(
            resolve_env_backed_default('skip_flac_compression'))),
    'ad_detection_parallel_windows': SettingSpec(
        env_backed=True,
        in_ad_reset=True, payload_key='adDetectionParallelWindows',
        payload_factory=lambda: AD_DETECTION_PARALLEL_WINDOWS_DEFAULT),
    'ad_reviewer_parallel_ads': SettingSpec(
        env_backed=True,
        in_ad_reset=True, payload_key='adReviewerParallelAds',
        payload_factory=lambda: AD_REVIEWER_PARALLEL_ADS_DEFAULT),
    'max_artwork_bytes': SettingSpec(
        env_backed=True, in_ad_reset=True, payload_key='maxArtworkBytes',
        payload_factory=_payload_max_artwork_bytes),
    'max_rss_bytes': SettingSpec(
        env_backed=True, in_ad_reset=True, payload_key='maxRssBytes',
        payload_factory=_payload_max_rss_bytes),
    'max_audio_download_mb': SettingSpec(
        env_backed=True, in_ad_reset=True, payload_key='maxAudioDownloadMb',
        payload_factory=_payload_max_audio_download_mb),

    # -- Audio cue detection (#350) --
    'audio_cue_detection_enabled': SettingSpec(
        default='false', in_ad_reset=True,
        payload_key='audioCueDetectionEnabled', payload_kind='bool'),
    'audio_cue_create_from_pairs': SettingSpec(
        default='false', in_ad_reset=True,
        payload_key='audioCueCreateFromPairs', payload_kind='bool'),
    'audio_cue_freq_min_hz': SettingSpec(
        default=str(AUDIO_CUE_FREQ_MIN_HZ), in_ad_reset=True,
        payload_key='audioCueFreqMinHz', payload_kind='int'),
    'audio_cue_freq_max_hz': SettingSpec(
        default=str(AUDIO_CUE_FREQ_MAX_HZ), in_ad_reset=True,
        payload_key='audioCueFreqMaxHz', payload_kind='int'),
    'audio_cue_prominence_db': SettingSpec(
        default=str(AUDIO_CUE_PROMINENCE_DB), in_ad_reset=True,
        payload_key='audioCueProminenceDb', payload_kind='float'),
    'audio_cue_min_confidence': SettingSpec(
        default=str(AUDIO_CUE_MIN_CONFIDENCE), in_ad_reset=True,
        payload_key='audioCueMinConfidence', payload_kind='float'),
    'audio_cue_template_score': SettingSpec(
        default=str(AUDIO_CUE_TEMPLATE_SCORE), in_ad_reset=True,
        payload_key='audioCueTemplateScore', payload_kind='float'),
    'audio_cue_formant_atten_db': SettingSpec(
        default=str(AUDIO_CUE_FORMANT_ATTEN_DB), in_ad_reset=True,
        payload_key='audioCueFormantAttenDb', payload_kind='float'),
    'audio_cue_snap_confidence': SettingSpec(
        default=str(AUDIO_CUE_SNAP_CONFIDENCE), in_ad_reset=True,
        payload_key='audioCueSnapConfidence', payload_kind='float'),
    'audio_cue_snap_lead_seconds': SettingSpec(
        default=str(AUDIO_CUE_SNAP_LEAD_SECONDS), in_ad_reset=True,
        payload_key='audioCueSnapLeadSeconds', payload_kind='float'),
    'audio_cue_snap_lag_seconds': SettingSpec(
        default=str(AUDIO_CUE_SNAP_LAG_SECONDS), in_ad_reset=True,
        payload_key='audioCueSnapLagSeconds', payload_kind='float'),
    'audio_cue_capture_min_seconds': SettingSpec(
        default=str(AUDIO_CUE_CAPTURE_MIN_SECONDS), in_ad_reset=True,
        payload_key='audioCueCaptureMinSeconds', payload_kind='float'),
    'audio_cue_capture_max_seconds': SettingSpec(
        default=str(AUDIO_CUE_CAPTURE_MAX_SECONDS), in_ad_reset=True,
        payload_key='audioCueCaptureMaxSeconds', payload_kind='float'),
    'audio_cue_capture_max_intro_seconds': SettingSpec(
        default=str(AUDIO_CUE_CAPTURE_MAX_INTRO_SECONDS), in_ad_reset=True,
        payload_key='audioCueCaptureMaxIntroSeconds', payload_kind='float'),
    'audio_cue_capture_max_outro_seconds': SettingSpec(
        default=str(AUDIO_CUE_CAPTURE_MAX_OUTRO_SECONDS), in_ad_reset=True,
        payload_key='audioCueCaptureMaxOutroSeconds', payload_kind='float'),
    'audio_cue_pair_confidence': SettingSpec(
        default=str(AUDIO_CUE_PAIR_CONFIDENCE), in_ad_reset=True,
        payload_key='audioCuePairConfidence', payload_kind='float'),
    'audio_cue_pair_min_break_seconds': SettingSpec(
        default=str(AUDIO_CUE_PAIR_MIN_BREAK_SECONDS), in_ad_reset=True,
        payload_key='audioCuePairMinBreakSeconds', payload_kind='float'),
    'audio_cue_pair_max_break_seconds': SettingSpec(
        default=str(AUDIO_CUE_PAIR_MAX_BREAK_SECONDS), in_ad_reset=True,
        payload_key='audioCuePairMaxBreakSeconds', payload_kind='float'),
    'audio_cue_pair_max_break_fraction': SettingSpec(
        default=str(AUDIO_CUE_PAIR_MAX_BREAK_FRACTION), in_ad_reset=True,
        payload_key='audioCuePairMaxBreakFraction', payload_kind='float'),
    # No payload_key: absent from the GET defaults block today.
    'audio_cue_pair_orient_window_seconds': SettingSpec(
        default=str(AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS), in_ad_reset=True),

    # -- Silence snap (Phase B boundary snap; not in the bulk reset) --
    'silence_snap_noise_db': SettingSpec(
        default=str(SILENCE_SNAP_NOISE_DB),
        payload_key='silenceSnapNoiseDb', payload_kind='float'),
    'silence_snap_min_duration_seconds': SettingSpec(
        default=str(SILENCE_SNAP_MIN_DURATION_SECONDS),
        payload_key='silenceSnapMinDurationSeconds', payload_kind='float'),
    'silence_snap_max_distance_seconds': SettingSpec(
        default=str(SILENCE_SNAP_MAX_DISTANCE_SECONDS),
        payload_key='silenceSnapMaxDistanceSeconds', payload_kind='float'),

    # -- Detection tuning (verification miss, learning, differential; 2.76.0) --
    'verification_miss_hold_min_confidence': SettingSpec(
        default='0.60', seeded=True, in_ad_reset=True,
        payload_key='verificationMissHoldMinConfidence', payload_kind='float'),
    'verification_miss_autocut_min_confidence': SettingSpec(
        default='0', seeded=True, in_ad_reset=True,
        payload_key='verificationMissAutocutMinConfidence', payload_kind='float'),
    'learning_min_confidence': SettingSpec(
        default='0.85', seeded=True, in_ad_reset=True,
        payload_key='learningMinConfidence', payload_kind='float'),
    'learning_min_confidence_long': SettingSpec(
        default='0.92', seeded=True, in_ad_reset=True,
        payload_key='learningMinConfidenceLong', payload_kind='float'),
    'differential_measured_corr_max': SettingSpec(
        default='0.60', seeded=True, in_ad_reset=True,
        payload_key='differentialMeasuredCorrMax', payload_kind='float'),
    'differential_hold_min_seconds': SettingSpec(
        default='10', seeded=True, in_ad_reset=True,
        payload_key='differentialHoldMinSeconds', payload_kind='float'),
}

# Secrets: reset clears the row so env-var fallback takes over. Only the
# two provider-adjacent keys participate in the bulk ad-detection reset.
for _key in sorted(SECRET_SETTING_KEYS):
    SETTINGS_REGISTRY[_key] = SettingSpec(
        secret=True,
        in_ad_reset=_key in ('openrouter_api_key', 'whisper_api_key'))

# Stage tunables: reset clears the row (DB > env > default at read time).
# All of them participate in the bulk reset. Defaults stay owned by
# config.STAGE_TUNABLE_DEFAULTS; the GET payload exposes them via the
# separate stageTunableDefaults block.
for _key in STAGE_TUNABLE_DEFAULTS:
    SETTINGS_REGISTRY[_key] = SettingSpec(stage_tunable=True, in_ad_reset=True)

del _key

# Ordered key list for POST /settings/ad-detection/reset.
AD_RESET_SETTING_KEYS = tuple(
    key for key, spec in SETTINGS_REGISTRY.items() if spec.in_ad_reset)


def _validate_registry():
    """Fail fast if the registry drifts from the mechanism catalogs."""
    env_backed = {k for k, _, _, _ in ENV_BACKED_SETTINGS}
    flagged = {k for k, s in SETTINGS_REGISTRY.items() if s.env_backed}
    if env_backed != flagged:
        raise RuntimeError(
            f"SETTINGS_REGISTRY env_backed mismatch: {env_backed ^ flagged}")
    flagged = {k for k, s in SETTINGS_REGISTRY.items() if s.secret}
    if set(SECRET_SETTING_KEYS) != flagged:
        raise RuntimeError(
            f"SETTINGS_REGISTRY secret mismatch: {set(SECRET_SETTING_KEYS) ^ flagged}")
    flagged = {k for k, s in SETTINGS_REGISTRY.items() if s.stage_tunable}
    if set(STAGE_TUNABLE_DEFAULTS) != flagged:
        raise RuntimeError(
            f"SETTINGS_REGISTRY stage_tunable mismatch: "
            f"{set(STAGE_TUNABLE_DEFAULTS) ^ flagged}")


_validate_registry()


def registry_default(key: str) -> Optional[str]:
    """DB-string default for a key (seed-time semantics)."""
    spec = SETTINGS_REGISTRY[key]
    if spec.env_backed:
        # ENV_BACKED_SETTINGS owns the env var, fallback, and validator.
        return resolve_env_backed_default(key)
    if spec.factory is not None:
        return spec.factory()
    if spec.env is not None:
        if spec.env_blank_is_unset:
            return os.environ.get(spec.env) or spec.default
        return os.environ.get(spec.env, spec.default)
    return spec.default


def registry_get_default(key: str) -> Any:
    """Python-typed default for the GET /settings 'defaults' block."""
    spec = SETTINGS_REGISTRY[key]
    if spec.payload_factory is not None:
        return spec.payload_factory()
    raw = registry_default(key)
    if spec.payload_kind == 'bool':
        return coerce_bool_setting(raw)
    if spec.payload_kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(spec.default)
    if spec.payload_kind == 'float':
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(spec.default)
    return raw


def iter_seed_defaults():
    """(key, value) pairs for schema seeding, in registry order."""
    return [(key, registry_default(key))
            for key, spec in SETTINGS_REGISTRY.items() if spec.seeded]


class SettingsMixin:
    """Settings management methods."""

    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else None

    def get_setting_bool(self, key: str, default: bool = False) -> bool:
        """Get a setting as bool. 'true'/'1'/'yes' -> True (case-insensitive)."""
        v = self.get_setting(key)
        if v is None:
            return default
        return str(v).strip().lower() in ('true', '1', 'yes', 'on')

    def get_setting_float(self, key: str, default: float = 0.0) -> float:
        """Get a setting as float, returning `default` on missing/invalid values."""
        v = self.get_setting(key)
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT key, value, is_default FROM settings")
        settings = {}
        for row in cursor:
            settings[row['key']] = {
                'value': row['value'],
                'is_default': bool(row['is_default'])
            }
        return settings

    def set_setting(self, key: str, value: str, is_default: bool = False):
        """Set a setting value."""
        conn = self.get_connection()
        conn.execute(
            """INSERT INTO settings (key, value, is_default, updated_at)
               VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 is_default = excluded.is_default,
                 updated_at = excluded.updated_at""",
            (key, value, 1 if is_default else 0)
        )
        conn.commit()

    def reset_setting(self, key: str):
        """Reset a setting to its default value (SETTINGS_REGISTRY-driven).

        Secret keys (provider api keys) get DELETEd so the env-var
        fallback in ``llm_client`` takes over. Writing an empty-string
        row would leave a residue that reads as "configured but empty"
        and also trips the plaintext-secret read warning.
        """
        spec = SETTINGS_REGISTRY.get(key)
        if spec is None or not spec.resettable:
            return False

        if spec.secret:
            self.clear_secret(key)
            return True

        if spec.stage_tunable:
            # Stage tunables resolve DB > env > default at read time. Clear the
            # row (empty value, is_default=True) so that resolution takes over --
            # mirrors the clear path in api.settings._apply_stage_tunables and
            # avoids stringifying None for the reasoning budget/level defaults.
            self.set_setting(key, "", is_default=True)
            return True

        # env_backed keys fall through: registry_default() resolves them via
        # resolve_env_backed_default (they carry no reset_factory).
        value = spec.reset_factory() if spec.reset_factory else registry_default(key)
        self.set_setting(key, value, is_default=True)
        return True

    def get_secret(self, key: str) -> Optional[str]:
        """Return a decrypted secret, or None if unset.

        Transparently handles legacy plaintext rows (no envelope prefix) so
        pre-v1.2.0 stored keys keep working until re-saved.
        """
        raw = self.get_setting(key)
        if not raw:
            return None
        if not is_ciphertext(raw):
            return raw
        try:
            return decrypt(self, raw)
        except CryptoUnavailableError:
            logger.warning("secrets_crypto unavailable; skipping decrypt")
            return None
        except Exception:
            logger.exception("secrets_crypto decrypt failed")
            return None

    def set_secret(self, key: str, plaintext: str):
        """Encrypt and store a secret. An enc:v1: input (UI round-trip of a
        masked field) is kept verbatim only if it decrypts under the current DEK;
        otherwise it's treated as plaintext, so a bogus envelope can't be stored
        as an undecryptable secret (creds-3)."""
        if is_ciphertext(plaintext):
            try:
                decrypt(self, plaintext)  # verify it round-trips under our DEK
                self.set_setting(key, plaintext)
                return
            except CryptoUnavailableError:
                # Crypto not configured; can't verify or re-encrypt. Preserve
                # the legacy behavior of storing the envelope verbatim.
                self.set_setting(key, plaintext)
                return
            except Exception:
                logger.warning("set_secret: enc:v1: value did not decrypt; treating as plaintext")
        self.set_setting(key, encrypt(self, plaintext))

    def clear_secret(self, key: str):
        """Remove a stored secret so env-var fallback takes over.

        Deletes the row outright rather than writing an empty string. An empty
        string still occupies a row that happens to be readable; a deleted
        row leaves no residue for an inquisitive attacker who somehow got
        read access to the ``settings`` table but not the ciphertext.
        """
        conn = self.get_connection()
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()

    # ========== System Settings Methods (for schema versioning) ==========

    def get_system_setting(self, key: str) -> Optional[str]:
        """Get a system setting value."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else None

    def set_system_setting(self, key: str, value: str):
        """Set a system setting value."""
        conn = self.get_connection()
        conn.execute(
            """INSERT INTO system_settings (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            (key, value)
        )
        conn.commit()

    def get_pricing_last_updated(self) -> Optional[str]:
        """Get the most recent updated_at from model_pricing table."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT MAX(updated_at) as last_updated FROM model_pricing")
        row = cursor.fetchone()
        return row['last_updated'] if row else None

    def get_model_pricing(self, source: str = None) -> List[Dict]:
        """Get model pricing entries, optionally filtered by source."""
        conn = self.get_connection()
        if source:
            cursor = conn.execute(
                """SELECT match_key, raw_model_id, display_name,
                          input_cost_per_mtok, output_cost_per_mtok,
                          source, updated_at
                   FROM model_pricing WHERE source = ?
                   ORDER BY display_name""",
                (source,)
            )
        else:
            cursor = conn.execute(
                """SELECT match_key, raw_model_id, display_name,
                          input_cost_per_mtok, output_cost_per_mtok,
                          source, updated_at
                   FROM model_pricing ORDER BY display_name"""
            )
        return [
            {
                'matchKey': row['match_key'],
                'rawModelId': row['raw_model_id'],
                'displayName': row['display_name'],
                'inputCostPerMtok': row['input_cost_per_mtok'],
                'outputCostPerMtok': row['output_cost_per_mtok'],
                'source': row['source'],
                'updatedAt': row['updated_at'],
            }
            for row in cursor
        ]

    def seed_default_pricing(self):
        """Seed model_pricing from DEFAULT_MODEL_PRICING.

        Used two ways: as the empty-table fallback when a live fetch fails, and
        as a post-fetch backfill that fills gaps for known models the live source
        has not published yet (e.g. a just-released Claude model). ON CONFLICT
        DO NOTHING means existing rows are never touched. Rows are marked
        source='default' so a later live fetch overwrites them via DO UPDATE.
        """
        conn = self.get_connection()
        inserted = 0
        for model_id, info in DEFAULT_MODEL_PRICING.items():
            key = normalize_model_key(model_id)
            cursor = conn.execute(
                """INSERT INTO model_pricing
                       (model_id, match_key, raw_model_id, display_name,
                        input_cost_per_mtok, output_cost_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'default')
                   ON CONFLICT(match_key) DO NOTHING""",
                (model_id, key, model_id, info['name'], info['input'], info['output'])
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        if inserted > 0:
            logger.info(f"Seeded {inserted} default model pricing entries")

    def upsert_fetched_pricing(self, models: List[Dict], source: str):
        """Bulk upsert pricing fetched from an external source. Rejects negative rows (#237).

        Each row may carry a '_source' key (set by fetch_pricing_chain to record
        which source in the chain actually contributed it). When present it takes
        precedence over the fallback 'source' parameter so the stored source
        reflects true provenance rather than always naming the primary source.
        """
        conn = self.get_connection()
        # Deduplicate by match_key (last entry wins) to avoid PK/UNIQUE conflict
        seen = {}
        for m in models:
            seen[m['match_key']] = m
        models = list(seen.values())
        for m in models:
            if m['input_cost_per_mtok'] < 0 or m['output_cost_per_mtok'] < 0:
                logger.warning(
                    "Rejecting negative pricing for %s (in=%s out=%s, source=%s)",
                    m.get('raw_model_id'), m['input_cost_per_mtok'],
                    m['output_cost_per_mtok'], source,
                )
                continue
            row_source = m.get('_source') or source
            conn.execute(
                """INSERT INTO model_pricing
                       (model_id, match_key, raw_model_id, display_name,
                        input_cost_per_mtok, output_cost_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(match_key) DO UPDATE SET
                     raw_model_id = excluded.raw_model_id,
                     display_name = excluded.display_name,
                     input_cost_per_mtok = excluded.input_cost_per_mtok,
                     output_cost_per_mtok = excluded.output_cost_per_mtok,
                     source = excluded.source,
                     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')""",
                (m['raw_model_id'], m['match_key'], m['raw_model_id'], m['display_name'],
                 m['input_cost_per_mtok'], m['output_cost_per_mtok'], row_source)
            )
        conn.commit()

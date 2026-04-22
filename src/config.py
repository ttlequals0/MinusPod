"""Centralized configuration constants.

All magic numbers and thresholds should be defined here
for easy tuning and consistency across the codebase.
"""
import ipaddress
import os
import re
from urllib.parse import urlparse

# ============================================================
# Confidence Thresholds (0.0 - 1.0 scale)
# ============================================================
CONFIDENCE_STRING_MAP = {
    'high': 0.95,
    'very high': 0.98,
    'medium': 0.75,
    'moderate': 0.75,
    'low': 0.50,
    'very low': 0.30,
}

LOW_CONFIDENCE = 0.50           # Warn/flag for review
REJECT_CONFIDENCE = 0.30        # Auto-reject as false positive
HIGH_CONFIDENCE_OVERRIDE = 0.90 # Override duration limits if above this
MIN_CUT_CONFIDENCE = 0.80       # Minimum to actually remove from audio

# ============================================================
# Duration Limits (seconds)
# ============================================================
MIN_AD_DURATION = 7.0           # Reject if shorter (quick mentions ~10s minimum)
SHORT_AD_WARN = 30.0            # Warn if shorter than 30s
LONG_AD_WARN = 180.0            # Warn if longer than 3 min
MAX_AD_DURATION = 300.0         # Reject if longer (5 min)
MAX_AD_DURATION_CONFIRMED = 900.0  # Allow 15 min if sponsor confirmed
MIN_UNCOVERED_TAIL_DURATION = 15.0  # Min seconds for an uncovered tail to be preserved

# Ad evidence thresholds
CONTENT_DURATION_THRESHOLD = 120.0  # Segments >= this without evidence are likely content
LOW_EVIDENCE_WARN_THRESHOLD = 60.0  # Warn for segments >= this without evidence

# Ad detector specific durations
MIN_TYPICAL_AD_DURATION = 30.0  # Most sponsor reads are 60-120 seconds
MIN_SPONSOR_READ_DURATION = 90.0  # Threshold for extension consideration
SHORT_GAP_THRESHOLD = 120.0     # 2 minutes - gap between ads to merge
MAX_MERGED_DURATION = 300.0     # 5 minutes max for merged ads
MAX_REALISTIC_SIGNAL = 180.0    # 3 minutes - anything longer is suspect
MIN_OVERLAP_TOLERANCE = 120.0   # 2 min tolerance for boundary ads
MAX_AD_DURATION_WINDOW = 420.0  # 7 min max (longest reasonable sponsor read)

# ============================================================
# Position Windows (as fraction of episode duration 0.0 - 1.0)
# ============================================================
PRE_ROLL = (0.0, 0.05)          # First 5%
MID_ROLL_1 = (0.15, 0.85)       # Continuous mid-roll coverage
POST_ROLL = (0.95, 1.0)         # Last 5%

# ============================================================
# Ad Limits
# ============================================================
MAX_AD_PERCENTAGE = 0.30        # 30% of episode is suspicious
MAX_ADS_PER_5MIN = 1            # More than 1 ad per 5 min is suspicious
MERGE_GAP_THRESHOLD = 5.0       # Merge ads within 5s
MAX_SILENT_GAP = 30.0           # Merge ads across silent gaps up to 30s

# ============================================================
# Pattern Matching
# ============================================================
PODCAST_TO_NETWORK_THRESHOLD = 3   # Patterns needed for network promotion
NETWORK_TO_GLOBAL_THRESHOLD = 2    # Networks needed for global promotion
PROMOTION_SIMILARITY_THRESHOLD = 0.75  # TF-IDF similarity for pattern merging
SPONSOR_GLOBAL_THRESHOLD = 3       # Podcasts with same sponsor for global promotion
PATTERN_CORRECTION_OVERLAP_THRESHOLD = 0.5  # 50% overlap triggers duration correction

# ============================================================
# False Positive Cross-Episode Matching
# ============================================================
FALSE_POSITIVE_SIMILARITY_THRESHOLD = 0.75  # TF-IDF similarity to match rejected content
MAX_FALSE_POSITIVE_TEXTS = 100              # Max false positives to load per podcast

# ============================================================
# Processing Limits
# ============================================================
MAX_EPISODE_RETRIES = 4         # Retries before permanent failure (initial + 4 retries = 5 total attempts, ladder 5m/15m/30m/60m)
JIT_RETRY_COOLDOWN_SECONDS = 60 # Base cooldown between JIT retries (doubles per attempt)
WINDOW_SIZE_SECONDS = 600       # Claude processing window (10 min)
WINDOW_OVERLAP_SECONDS = 180    # Overlap between windows (3 min)
MAX_FILE_SIZE_MB = 500          # Maximum audio file size

# ============================================================
# Caching (seconds)
# ============================================================
FEED_CACHE_TTL = 30             # Seconds to cache feed map
RSS_PARSE_CACHE_TTL = 60        # Seconds to cache parsed RSS
SETTINGS_CACHE_TTL = 60         # Seconds to cache settings

# ============================================================
# Background Processing (seconds)
# ============================================================
RSS_REFRESH_INTERVAL = 900      # Seconds between RSS refreshes (15 min)
AUTO_PROCESS_INITIAL_BACKOFF = 30   # Initial backoff when queue busy
AUTO_PROCESS_MAX_BACKOFF = 300      # Maximum backoff (5 min)
GRACEFUL_SHUTDOWN_TIMEOUT = 300     # Seconds to wait for processing

# ============================================================
# Text Pattern Matching Thresholds
# ============================================================
TFIDF_MATCH_THRESHOLD = 0.70         # TF-IDF similarity for content matching
FUZZY_MATCH_THRESHOLD = 0.75         # Fuzzy string match threshold
FINGERPRINT_MATCH_THRESHOLD = 0.65   # Audio fingerprint similarity threshold

# ============================================================
# Ad Boundary Extension (content-based)
# ============================================================
# Timestamp Validation (Claude hallucination correction)
MIN_KEYWORD_LENGTH = 3              # Minimum keyword length for transcript search

BOUNDARY_EXTENSION_WINDOW = 10.0   # Seconds before/after ad to check for ad content
BOUNDARY_EXTENSION_MAX = 15.0      # Max seconds to extend a boundary
AD_CONTENT_URL_PATTERNS = ['.com', '.tv', '.co', '.org', '.net', '.io']
AD_CONTENT_PROMO_PHRASES = [
    'use code', 'percent off', 'visit', 'sign up', 'free trial',
    'promo code', 'check out', 'head to', 'go to', 'click the link',
    'dot com', 'slash', 'coupon', 'discount', 'offer code',
]

# ============================================================
# Ad Duration Estimation
# ============================================================
DEFAULT_AD_DURATION_ESTIMATE = 90.0  # Assumed ad length when only intro/outro found
SPONSOR_MISMATCH_MAX_GAP = 60.0      # Max gap for sponsor mismatch extension

# ============================================================
# Volume Analysis (DAI ads)
# ============================================================
VOLUME_ANOMALY_THRESHOLD_DB = 3.0    # dB deviation from baseline to flag as anomaly

# ============================================================
# Transition Detection (DAI ads)
# ============================================================
TRANSITION_THRESHOLD_DB = 12.0       # Min dB jump between frames to flag (real DAI splices are 12+ dB)
MIN_TRANSITION_AD_DURATION = 15.0    # Min seconds for a valid transition-bounded ad
MAX_TRANSITION_AD_DURATION = 180.0   # Max seconds for a valid transition-bounded ad

# ============================================================
# Audio Processing
# ============================================================
MIN_AD_DURATION_FOR_REMOVAL = 10.0   # Min ad duration to actually remove from audio
POST_ROLL_TRIM_THRESHOLD = 30.0      # Threshold for trimming post-roll content

# ============================================================
# Subprocess Timeouts (seconds)
# ============================================================
FFPROBE_TIMEOUT = 30                 # ffprobe duration/metadata queries
FFMPEG_SHORT_TIMEOUT = 60            # Short ffmpeg operations
FFMPEG_LONG_TIMEOUT = 300            # Long ffmpeg operations (processing)
FFMPEG_CHUNK_TIMEOUT = 120           # Audio chunk extract (seek + transcode)
FPCALC_TIMEOUT = 60                  # Audio fingerprint generation
FPCALC_TIMEOUT_FULL = 120            # Fingerprint the entire episode
SUBPROCESS_VERSION_PROBE = 5         # ffmpeg -version, fpcalc -version

# ============================================================
# LLM Timeouts (seconds)
# ============================================================
LLM_TIMEOUT_DEFAULT = 120.0          # Anthropic / fast cloud APIs
LLM_TIMEOUT_LOCAL = 600.0            # Ollama / local models (10 min)
LLM_RETRY_MAX_RETRIES = 3            # Default retries for cloud APIs
LLM_RETRY_MAX_RETRIES_LOCAL = 2      # Fewer retries for local (each is slow)
AD_DETECTION_MAX_TOKENS = int(os.environ.get('AD_DETECTION_MAX_TOKENS', '2000'))

# ============================================================
# Outbound HTTP
# ============================================================
# Podcast CDN chains are deep -- Megaphone / Art19 / Acast / simplecast
# routinely chain 6-8 redirects per asset request (edge -> regional ->
# storage), and analytics bouncers add more. 5 was too tight and caused
# false "CDN not ready" errors for legitimate feeds. 3 stays on outbound
# APIs (LLM / PodcastIndex / webhook) where long chains are a misconfig
# signal rather than expected behaviour.
HTTP_MAX_REDIRECTS_FEED = 10         # RSS, audio, artwork, VTT, chapters
HTTP_MAX_REDIRECTS_API = 3           # LLM / PodcastIndex / webhook / pricing

# HTTP request timeouts (seconds). Tiered by how much the call is
# expected to do, so a slow network doesn't fail-fast a legitimate
# download nor let a hung API call pin a worker forever.
HTTP_TIMEOUT_PROBE = 5.0              # Short outbound: /version probes,
                                      # provider auth pings, webhook delivery
HTTP_TIMEOUT_API = 10.0               # Standard JSON API (LLM verify, PodcastIndex search)
HTTP_TIMEOUT_EXTERNAL = 15.0          # Third-party scraping (pricing sources)
HTTP_TIMEOUT_FETCH = 30.0             # RSS fetch, artwork / audio download
HTTP_TIMEOUT_WHISPER = 600            # Remote Whisper transcription upload
                                      # (multi-minute audio over slow network)

# ============================================================
# Chunked Transcription (OOM prevention for long episodes)
# ============================================================
CHUNK_OVERLAP_SECONDS = 30           # Overlap between chunks for boundary alignment
CHUNK_MIN_DURATION_SECONDS = 300     # Minimum chunk size (5 minutes)
CHUNK_MAX_DURATION_SECONDS = 3600    # Maximum chunk size (60 minutes)
CHUNK_DEFAULT_DURATION_SECONDS = 1800  # Default if memory detection fails (30 minutes)

# API backend chunk duration (10 min = ~19MB WAV, under 25MB OpenAI API limit)
API_CHUNK_DURATION_SECONDS = 600

# Whisper backend identifiers
WHISPER_BACKEND_LOCAL = 'local'
WHISPER_BACKEND_API = 'openai-api'

# Whisper compute-type values accepted by faster-whisper/CTranslate2.
# 'auto' resolves to float16 on CUDA and int8 on CPU at init time.
WHISPER_COMPUTE_TYPES = ('auto', 'float16', 'int8_float16', 'int8', 'float32')
WHISPER_COMPUTE_TYPE_DEFAULT = 'auto'
# Fallback order when float16 init fails on CUDA (CC < 7.0: Pascal/Maxwell).
WHISPER_COMPUTE_TYPE_FALLBACK_CHAIN = ('int8_float16', 'int8', 'float32')

# VAD gap detector: catches audio regions Whisper's VAD dropped (sped-up
# disclaimers, distorted ad tails) that the transcript-based ad detectors
# never see. A "gap" is a span with no Whisper segment.
VAD_GAP_DETECTION_ENABLED_DEFAULT = True
VAD_GAP_START_MIN_SECONDS_DEFAULT = 3.0  # head: cut when gap >= this
VAD_GAP_MID_MIN_SECONDS_DEFAULT = 8.0    # mid: cut only with signoff AND resume context
VAD_GAP_TAIL_MIN_SECONDS_DEFAULT = 3.0   # tail: cut when no postroll already covers it
VAD_GAP_CONFIDENCE = 0.75                # emitted marker confidence

# OpenRouter API
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
OPENROUTER_HTTP_REFERER = 'https://github.com/ttlequals0/minuspod'
OPENROUTER_APP_TITLE = 'MinusPod'

MASTER_PASSPHRASE = os.environ.get('MINUSPOD_MASTER_PASSPHRASE')


def provider_crypto_ready() -> bool:
    return bool(MASTER_PASSPHRASE)

# ============================================================
# LLM Pricing Configuration
# ============================================================

# Map base URL domains to pricepertoken.com pricing page paths.
# Tuple is (url_type, slug) -> constructs: pricepertoken.com/{url_type}/{slug}
# None means provider has a native pricing API (handled separately).
PROVIDER_PRICING_SLUGS = {
    'api.anthropic.com':                  ('pricing-page/provider', 'anthropic'),
    'api.openai.com':                     ('pricing-page/provider', 'openai'),
    'generativelanguage.googleapis.com':  ('pricing-page/provider', 'google'),
    'api.mistral.ai':                     ('pricing-page/provider', 'mistral'),
    'api.deepseek.com':                   ('pricing-page/provider', 'deepseek'),
    'api.x.ai':                           ('pricing-page/provider', 'xai'),
    'api.perplexity.ai':                  ('pricing-page/provider', 'perplexity'),
    'api.groq.com':                       ('endpoints', 'groq'),
    'api.together.xyz':                   ('endpoints', 'together'),
    'api.fireworks.ai':                   ('endpoints', 'fireworks'),
    'openrouter.ai':                      None,  # Native API
}

# Pricing cache TTL (seconds) - how often to re-fetch pricing data
PRICING_CACHE_TTL = 86400  # 24 hours

# Memory safety margin - don't use all available memory
MEMORY_SAFETY_MARGIN = 0.7           # Use only 70% of available memory

# Whisper model memory profiles (approximate, in GB)
# Format: (base_memory_gb, memory_per_minute_gb)
# Base memory = model weights + fixed overhead
# Per-minute = additional memory for audio processing (scales with duration)
WHISPER_MEMORY_PROFILES = {
    # Correct VRAM values from faster-whisper README (not PyTorch-based Whisper)
    # Format: (base_memory_gb, memory_per_minute_gb)
    'tiny': (1.0, 0.05),      # ~1GB VRAM
    'tiny.en': (1.0, 0.05),
    'base': (1.0, 0.05),      # ~1GB VRAM (was 1.5, corrected)
    'base.en': (1.0, 0.05),
    'small': (2.0, 0.10),     # ~2GB VRAM (was 2.5, corrected)
    'small.en': (2.0, 0.10),
    'medium': (4.0, 0.15),    # ~4GB VRAM (was 5.0, corrected)
    'medium.en': (4.0, 0.15),
    'large': (5.5, 0.25),     # ~5-6GB VRAM (was 10.0, corrected)
    'large-v1': (5.5, 0.25),
    'large-v2': (5.5, 0.25),
    'large-v3': (5.5, 0.25),
    'turbo': (5.0, 0.20),     # ~5GB VRAM (distilled large)
}
WHISPER_DEFAULT_PROFILE = (5.0, 0.20)  # Conservative default (medium-like)

# ============================================================
# LLM Provider Constants
# ============================================================
PROVIDER_ANTHROPIC = 'anthropic'
PROVIDER_OPENROUTER = 'openrouter'
PROVIDER_OPENAI_COMPATIBLE = 'openai-compatible'
PROVIDER_OLLAMA = 'ollama'
PROVIDERS_NON_ANTHROPIC = ('openai-compatible', 'ollama')

# ============================================================
# Default LLM Models
# ============================================================
DEFAULT_AD_DETECTION_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_CHAPTERS_MODEL = "claude-haiku-4-5-20251001"

# ============================================================
# User-Agent Strings
# ============================================================
# Browser-like UA for downloading audio from CDNs that block bots
BROWSER_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
# Application UA for RSS feeds and API requests
APP_USER_AGENT = 'PodcastAdRemover/1.0'


# ============================================================
# Model Name Normalization
# ============================================================

def normalize_model_key(name: str) -> str:
    """Normalize a model name into a match key for pricing lookups.

    Examples:
        'Claude Sonnet 4.5'           -> 'claudesonnet45'
        'claude-sonnet-4-5-20250929'  -> 'claudesonnet45'
        'anthropic/claude-sonnet-4-5' -> 'claudesonnet45'
        'gpt-4o-mini'                 -> 'gpt4omini'
        'gpt-4o-2024-05-13'          -> 'gpt4o'

    Note: normalization is intentionally lossy (strips punctuation, hyphens).
    OpenRouter variants (:free, :extended) map to the same key as the base model.
    """
    # Strip provider prefix (anything before /)
    if '/' in name:
        name = name.split('/', 1)[1]
    # Strip OpenRouter variant suffixes (:free, :extended, :beta, :nitro, etc.)
    name = re.sub(r':[a-zA-Z]+$', '', name)
    # Strip date suffixes: YYYYMMDD or YYYY-MM-DD at end (2020-2039 range)
    name = re.sub(r'-?20[2-3]\d-?\d{2}-?\d{2}$', '', name)
    # Lowercase, remove everything non-alphanumeric
    return re.sub(r'[^a-z0-9]', '', name.lower())


def get_pricing_source(provider: str, base_url: str = '') -> dict:
    """Determine pricing source for the active provider.

    Returns dict with:
      'type': 'openrouter_api' | 'pricepertoken' | 'free' | 'unknown'
      'url': full URL to fetch (for openrouter_api and pricepertoken types)
    """
    if provider == PROVIDER_OLLAMA:
        return {'type': 'free'}

    if provider == PROVIDER_OPENROUTER:
        return {
            'type': 'openrouter_api',
            'url': 'https://openrouter.ai/api/v1/models',
        }

    if provider == PROVIDER_ANTHROPIC:
        return {
            'type': 'pricepertoken',
            'url': 'https://pricepertoken.com/pricing-page/provider/anthropic',
        }

    # Parse domain from base_url for openai-compatible providers
    domain = urlparse(base_url or '').hostname or ''

    for known_domain, slug_info in PROVIDER_PRICING_SLUGS.items():
        if domain == known_domain or domain.endswith('.' + known_domain):
            if slug_info is None:
                return {
                    'type': 'openrouter_api',
                    'url': 'https://openrouter.ai/api/v1/models',
                }
            url_type, slug = slug_info
            return {
                'type': 'pricepertoken',
                'url': f'https://pricepertoken.com/{url_type}/{slug}',
            }

    # localhost, private IPs, unknown domains -> likely local/self-hosted
    if domain in ('localhost', '127.0.0.1', '::1') or domain.endswith('.local'):
        return {'type': 'free'}

    try:
        if ipaddress.ip_address(domain).is_private:
            return {'type': 'free'}
    except ValueError:
        pass

    return {'type': 'unknown', 'domain': domain}

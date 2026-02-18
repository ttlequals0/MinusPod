"""Centralized configuration constants.

All magic numbers and thresholds should be defined here
for easy tuning and consistency across the codebase.
"""

# ============================================================
# Confidence Thresholds (0.0 - 1.0 scale)
# ============================================================
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

# ============================================================
# False Positive Cross-Episode Matching
# ============================================================
FALSE_POSITIVE_SIMILARITY_THRESHOLD = 0.75  # TF-IDF similarity to match rejected content
MAX_FALSE_POSITIVE_TEXTS = 100              # Max false positives to load per podcast

# ============================================================
# Processing Limits
# ============================================================
MAX_EPISODE_RETRIES = 3         # Retries before permanent failure
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
DEFAULT_AD_DURATION_ESTIMATE = 60.0  # Assumed ad length when only intro/outro found
SPONSOR_MISMATCH_MAX_GAP = 60.0      # Max gap for sponsor mismatch extension

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
FPCALC_TIMEOUT = 60                  # Audio fingerprint generation

# ============================================================
# Chunked Transcription (OOM prevention for long episodes)
# ============================================================
CHUNK_OVERLAP_SECONDS = 30           # Overlap between chunks for boundary alignment
CHUNK_MIN_DURATION_SECONDS = 300     # Minimum chunk size (5 minutes)
CHUNK_MAX_DURATION_SECONDS = 3600    # Maximum chunk size (60 minutes)
CHUNK_DEFAULT_DURATION_SECONDS = 1800  # Default if memory detection fails (30 minutes)

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

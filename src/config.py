"""Centralized configuration constants.

All magic numbers and thresholds should be defined here
for easy tuning and consistency across the codebase.
"""

# ============================================================
# Confidence Thresholds (0.0 - 1.0 scale)
# ============================================================
HIGH_CONFIDENCE = 0.85          # Auto-accept threshold
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
MID_ROLL_1 = (0.20, 0.35)       # Common mid-roll positions
MID_ROLL_2 = (0.45, 0.55)
MID_ROLL_3 = (0.65, 0.80)
POST_ROLL = (0.95, 1.0)         # Last 5%

# ============================================================
# Ad Limits
# ============================================================
MAX_AD_PERCENTAGE = 0.30        # 30% of episode is suspicious
MAX_ADS_PER_5MIN = 1            # More than 1 ad per 5 min is suspicious
MERGE_GAP_THRESHOLD = 5.0       # Merge ads within 5s

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

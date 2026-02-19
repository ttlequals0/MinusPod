"""Shared constants for ad detection and pattern matching.

Centralizes field name sets and classification values that were previously
duplicated across ad_detector.py and text_pattern_matcher.py.
"""

# Invalid sponsor values that indicate extraction failure or garbage data.
# Used by ad_detector (validate_ads_from_response, _extract_sponsor_from_reason)
# and text_pattern_matcher (create_pattern_from_ad).
INVALID_SPONSOR_VALUES = frozenset({
    'none', 'unknown', 'null', 'n/a', 'na', '', 'no', 'yes',
    'ad', 'ads', 'sponsor', 'sponsors', 'advertisement', 'advertisements',
    'multiple', 'various', 'detected', 'advertisement detected',
    'host read', 'host-read', 'mid-roll', 'pre-roll', 'post-roll'
})

# Structural fields in LLM ad response objects that never contain sponsor info.
# Everything NOT in this set is a candidate for dynamic field scanning.
STRUCTURAL_FIELDS = frozenset({
    'start', 'end', 'start_time', 'end_time', 'start_timestamp', 'end_timestamp',
    'ad_start_timestamp', 'ad_end_timestamp', 'start_time_seconds', 'end_time_seconds',
    'confidence', 'end_text', 'is_ad', 'type', 'classification',
    'start_seconds', 'end_seconds', 'duration', 'duration_seconds',
    'music_bed', 'music_bed_confidence',
})

# Ordered list of field names to check for sponsor/advertiser name (priority order).
SPONSOR_PRIORITY_FIELDS = [
    'sponsor_name', 'advertiser', 'sponsor', 'brand', 'company', 'product', 'name'
]

# Keywords to match against any JSON key for fuzzy sponsor field detection.
SPONSOR_PATTERN_KEYWORDS = [
    'sponsor', 'brand', 'advertiser', 'company', 'product', 'ad_name', 'note'
]

# Invalid capture words - common English words that indicate regex captured garbage
# e.g., "not an advertisement" -> regex captures "not an" as sponsor
INVALID_SPONSOR_CAPTURE_WORDS = frozenset({
    'not', 'no', 'this', 'that', 'the', 'a', 'an', 'another',
    'consistent', 'possible', 'potential', 'likely', 'seems',
    'is', 'was', 'are', 'were', 'with', 'from', 'for', 'by',
    'clear', 'any', 'some', 'host', 'their', 'its', 'our',
})

# Classifications from LLM that indicate non-ad content
NOT_AD_CLASSIFICATIONS = frozenset({
    'content', 'not_ad', 'editorial', 'organic',
    'show_content', 'regular_content', 'interview',
    'conversation', 'segment', 'topic'
})

# SSRF protection: allowed URL schemes for outbound requests
ALLOWED_URL_SCHEMES = frozenset({'http', 'https'})

# SSRF protection: allowed ports for outbound requests (empty = allow all)
ALLOWED_URL_PORTS = frozenset({80, 443, 8080, 8443})

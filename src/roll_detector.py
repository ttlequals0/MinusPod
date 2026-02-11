"""Heuristic pre-roll and post-roll ad detection.

Runs after Claude detection, before validation. Uses regex patterns to find
ad content before the show intro (pre-roll) or after the show sign-off
(post-roll). Only creates markers for regions not already covered by
Claude-detected ads.

Addresses LLM nondeterminism: Claude sometimes misses pre/post-roll ads
at window boundaries, especially on repeated runs.
"""
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Sign-off patterns (search backwards from end)
SIGNOFF_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'see you next (week|time|episode)',
        r'(thanks?|thank you)\s+(for\s+)?(tuning in|listening|watching|joining)',
        r'until next (week|time)',
        r'bye[\s-]*bye',
        r"that'?s (all|it) for (today|this (week|episode)|now)",
        r'take care\b',
        r'catch you (next|later|soon)',
    ]
]

# Show start patterns (search forwards from start)
SHOW_START_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'welcome (back )?(to|everyone)',
        r"(i'm|i am)\s+\w+[\.,]\s+(and\s+)?(i'm|i am)",
        r'hello and welcome',
        r'hey (everyone|guys|folks|there)',
        r'(this is|you\'re listening to)\s+',
        r'episode\s+\d+',
    ]
]

# Ad indicator patterns -- need MIN_AD_PATTERN_MATCHES to flag
AD_INDICATOR_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\w+\.(com|org|edu|net|io)\b',
        r'\w+\.(com|org|edu|net|io)\s+slash\s+',
        r'1-\d{3}',
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        r'(visit|go to|head to|check out)\s+(us\s+at\s+)?\w+\.',
        r'(sign up|try it|get started|apply|subscribe)\s+(now|today|at|for free)',
        r'(use|with)\s+(code|promo)',
        r'free trial',
        r'(sponsored|brought to you|presented)\s+by',
        r'for the ones who',
        r'advertising inquiries',
        r'privacy\s+(&|and)\s+opt.out',
    ]
]

MIN_AD_PATTERN_MATCHES = 2
MAX_PREROLL_DURATION = 120.0   # seconds
MAX_POSTROLL_DURATION = 120.0  # seconds


def _region_covered(start: float, end: float, ads: List[Dict],
                    overlap_threshold: float = 0.5) -> bool:
    """True if >overlap_threshold of region already covered by existing ads."""
    region_duration = end - start
    if region_duration <= 0:
        return True

    covered = 0.0
    for ad in ads:
        overlap_start = max(start, ad['start'])
        overlap_end = min(end, ad['end'])
        if overlap_end > overlap_start:
            covered += overlap_end - overlap_start

    return (covered / region_duration) > overlap_threshold


def _count_ad_patterns(text: str) -> int:
    """Count distinct ad pattern matches in text."""
    count = 0
    for pattern in AD_INDICATOR_PATTERNS:
        if pattern.search(text):
            count += 1
    return count


def _segments_to_text(segments: List[Dict], start: float, end: float) -> str:
    """Extract transcript text from segments within a time range."""
    parts = []
    for seg in segments:
        if seg['end'] > start and seg['start'] < end:
            parts.append(seg.get('text', ''))
    return ' '.join(parts)


def detect_preroll(
    segments: List[Dict],
    existing_ads: List[Dict],
    podcast_name: str = '',
) -> Optional[Dict]:
    """Detect pre-roll ad before the show intro.

    Searches forward from the start for the first show-start pattern.
    If transcript content before it matches >= MIN_AD_PATTERN_MATCHES ad
    patterns, creates an ad marker.

    Args:
        segments: Transcript segments with 'start', 'end', 'text' keys
        existing_ads: Already-detected ads to avoid duplicates
        podcast_name: Podcast name (unused, reserved for future filtering)

    Returns:
        Ad marker dict if pre-roll detected, None otherwise
    """
    if not segments:
        return None

    episode_start = segments[0]['start']
    max_search_end = episode_start + MAX_PREROLL_DURATION

    # Find first show-start segment
    show_start_time = None
    for seg in segments:
        if seg['start'] > max_search_end:
            break
        text = seg.get('text', '')
        for pattern in SHOW_START_PATTERNS:
            if pattern.search(text):
                show_start_time = seg['start']
                break
        if show_start_time is not None:
            break

    if show_start_time is None or show_start_time <= episode_start + 5.0:
        return None

    # Check if region is already covered
    if _region_covered(episode_start, show_start_time, existing_ads):
        return None

    # Get text before show start and count ad patterns
    preroll_text = _segments_to_text(segments, episode_start, show_start_time)
    match_count = _count_ad_patterns(preroll_text)

    if match_count < MIN_AD_PATTERN_MATCHES:
        return None

    confidence = min(0.7 + (match_count * 0.05), 0.95)

    logger.info(
        f"Heuristic pre-roll detected: {episode_start:.1f}s-{show_start_time:.1f}s "
        f"({match_count} ad patterns, confidence={confidence:.2f})"
    )

    return {
        'start': episode_start,
        'end': show_start_time,
        'confidence': confidence,
        'reason': f'Pre-roll ad ({match_count} ad indicators before show intro)',
        'detection_stage': 'heuristic_preroll',
    }


def detect_postroll(
    segments: List[Dict],
    existing_ads: List[Dict],
    episode_duration: float = 0,
) -> Optional[Dict]:
    """Detect post-roll ad after the show sign-off.

    Searches backward from the end for the last sign-off pattern.
    If transcript content after it matches >= MIN_AD_PATTERN_MATCHES ad
    patterns, creates an ad marker.

    Args:
        segments: Transcript segments with 'start', 'end', 'text' keys
        existing_ads: Already-detected ads to avoid duplicates
        episode_duration: Total episode duration (from audio file)

    Returns:
        Ad marker dict if post-roll detected, None otherwise
    """
    if not segments:
        return None

    episode_end = episode_duration if episode_duration > 0 else segments[-1]['end']
    min_search_start = episode_end - MAX_POSTROLL_DURATION

    # Find last sign-off segment (search backwards)
    signoff_time = None
    for seg in reversed(segments):
        if seg['end'] < min_search_start:
            break
        text = seg.get('text', '')
        for pattern in SIGNOFF_PATTERNS:
            if pattern.search(text):
                signoff_time = seg['end']
                break
        if signoff_time is not None:
            break

    if signoff_time is None or signoff_time >= episode_end - 5.0:
        return None

    # Check if region is already covered
    if _region_covered(signoff_time, episode_end, existing_ads):
        return None

    # Get text after sign-off and count ad patterns
    postroll_text = _segments_to_text(segments, signoff_time, episode_end)
    match_count = _count_ad_patterns(postroll_text)

    if match_count < MIN_AD_PATTERN_MATCHES:
        return None

    confidence = min(0.7 + (match_count * 0.05), 0.95)

    logger.info(
        f"Heuristic post-roll detected: {signoff_time:.1f}s-{episode_end:.1f}s "
        f"({match_count} ad patterns, confidence={confidence:.2f})"
    )

    return {
        'start': signoff_time,
        'end': episode_end,
        'confidence': confidence,
        'reason': f'Post-roll ad ({match_count} ad indicators after sign-off)',
        'detection_stage': 'heuristic_postroll',
    }

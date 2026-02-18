"""Ad detection using Claude API with configurable prompts and model."""
import logging
import json
import re
import time
import random
from typing import List, Dict, Optional
from llm_client import (
    get_llm_client, get_api_key, LLMClient,
    is_retryable_error, is_rate_limit_error
)

from config import (
    MIN_TYPICAL_AD_DURATION, MIN_SPONSOR_READ_DURATION, SHORT_GAP_THRESHOLD,
    MAX_MERGED_DURATION, MAX_REALISTIC_SIGNAL, MIN_OVERLAP_TOLERANCE,
    MAX_AD_DURATION_WINDOW, WINDOW_SIZE_SECONDS, WINDOW_OVERLAP_SECONDS,
    BOUNDARY_EXTENSION_WINDOW, BOUNDARY_EXTENSION_MAX,
    AD_CONTENT_URL_PATTERNS, AD_CONTENT_PROMO_PHRASES,
    LOW_CONFIDENCE, CONTENT_DURATION_THRESHOLD, LOW_EVIDENCE_WARN_THRESHOLD,
    MIN_KEYWORD_LENGTH, MIN_UNCOVERED_TAIL_DURATION
)
from utils.constants import (
    INVALID_SPONSOR_VALUES, STRUCTURAL_FIELDS,
    SPONSOR_PRIORITY_FIELDS, SPONSOR_PATTERN_KEYWORDS,
    INVALID_SPONSOR_CAPTURE_WORDS, NOT_AD_CLASSIFICATIONS,
)

logger = logging.getLogger('podcast.claude')

# Default model - Claude Sonnet 4.5
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

# User prompt template (not configurable via UI - just formats the transcript)
# Description is optional - may contain sponsor lists, chapter markers, or content context
USER_PROMPT_TEMPLATE = """Podcast: {podcast_name}
Episode: {episode_title}
{description_section}
Transcript:
{transcript}"""

# Retry configuration for transient API errors
RETRY_CONFIG = {
    'max_retries': 3,
    'base_delay': 2.0,      # seconds
    'max_delay': 60.0,      # seconds
    'exponential_base': 2,
    'jitter': True          # Add random jitter to prevent thundering herd
}

# Sliding window step (derived from config values)
# WINDOW_SIZE_SECONDS and WINDOW_OVERLAP_SECONDS imported from config.py
WINDOW_STEP_SECONDS = WINDOW_SIZE_SECONDS - WINDOW_OVERLAP_SECONDS  # 7 minutes

def parse_timestamp(value) -> float:
    """Parse timestamp value to seconds.

    Handles multiple formats:
    - Float/int: 1178.5 -> 1178.5
    - String seconds: "1178.5" -> 1178.5
    - MM:SS format: "19:38" -> 1178.0
    - HH:MM:SS format: "1:19:38" -> 4778.0
    - String with 's' suffix: "1178.5s" -> 1178.5
    """
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        # Remove 's' suffix if present
        value = value.strip().rstrip('s').strip()

        # Try direct float conversion first
        try:
            return float(value)
        except ValueError:
            pass

        # Try MM:SS or HH:MM:SS format
        parts = value.split(':')
        if len(parts) == 2:
            # MM:SS
            try:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                pass
        elif len(parts) == 3:
            # HH:MM:SS
            try:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            except ValueError:
                pass

    raise ValueError(f"Cannot parse timestamp: {value}")


# Early ad snapping threshold
# If an ad starts within this many seconds of the episode start, snap it to 0:00
# Pre-roll ads often have brief intro audio before detection kicks in
EARLY_AD_SNAP_THRESHOLD = 30.0

# Transition phrases for intelligent ad boundary detection
# These are used to find precise start/end times using word timestamps

# Phrases that mark ad START (transition INTO ad)
AD_START_PHRASES = [
    "let's take a break",
    "take a quick break",
    "take a moment",
    "word from our sponsor",
    "brought to you by",
    "thanks to our sponsor",
    "thank our sponsor",
    "sponsored by",
    "a word from",
    "support comes from",
    "supported by",
    "speaking of",
    "but first",
    "first let me tell you",
    "i want to tell you about",
    "let me tell you about",
]

# Phrases that mark ad END (transition OUT of ad, back to content)
AD_END_PHRASES = [
    "anyway",
    "alright",
    "all right",
    "back to",
    "so let's",
    "okay so",
    "now let's",
    "let's get back",
    "returning to",
    "where were we",
    "as i was saying",
    "moving on",
    "now back to",
    "back to the show",
]

def refine_ad_boundaries(ads: List[Dict], segments: List[Dict]) -> List[Dict]:
    """Refine ad boundaries using word timestamps and keyword detection.

    For each ad:
    1. Look at segment before/at ad start for transition phrases
    2. Use word timestamps to find exact phrase timing
    3. Adjust ad start to phrase start time
    4. Similarly for ad end - find return-to-content phrases

    Args:
        ads: List of detected ad segments
        segments: List of transcript segments with word timestamps

    Returns:
        List of ads with refined boundaries
    """
    if not ads or not segments:
        return ads

    # Check if we have word timestamps
    if not segments[0].get('words'):
        logger.info("No word timestamps available, skipping boundary refinement")
        return ads

    # Build a lookup structure: for each segment, store its index
    # Segments are sorted by start time
    def find_segment_at_time(target_time: float) -> int:
        """Find the segment index that contains the target time."""
        for i, seg in enumerate(segments):
            if seg['start'] <= target_time <= seg['end']:
                return i
            # If target is between segments, return the earlier one
            if i > 0 and segments[i-1]['end'] < target_time < seg['start']:
                return i - 1
        # Default to last segment if past end
        return len(segments) - 1

    def find_phrase_in_words(words: List[Dict], phrases: List[str], search_start: bool = True) -> Optional[Dict]:
        """Search for transition phrases in word list.

        Args:
            words: List of word dicts with 'word', 'start', 'end'
            phrases: List of phrases to search for
            search_start: If True, search for ad START phrases (return first match)
                         If False, search for ad END phrases (return last match)

        Returns:
            Dict with 'start', 'end', 'phrase' if found, None otherwise
        """
        if not words:
            return None

        # Validate words have required timestamp fields and filter out invalid ones
        valid_words = []
        for w in words:
            word_text = w.get('word', '').strip()
            word_start = w.get('start')
            word_end = w.get('end')

            # Skip words missing timestamps
            if word_start is None or word_end is None:
                continue

            valid_words.append({
                'word': word_text,
                'start': word_start,
                'end': word_end
            })

        if not valid_words:
            logger.warning("No valid word timestamps found, skipping phrase detection")
            return None

        # Build text from validated words for phrase matching
        word_texts = [w['word'].lower() for w in valid_words]
        full_text = ' '.join(word_texts)

        matches = []
        for phrase in phrases:
            phrase_lower = phrase.lower()
            # Find phrase in the concatenated text
            idx = full_text.find(phrase_lower)
            if idx >= 0:
                # Map character position back to word index
                # Track cumulative character position including spaces
                char_count = 0
                start_word_idx = 0
                for i, wt in enumerate(word_texts):
                    # Check if phrase starts within this word
                    word_end_pos = char_count + len(wt)
                    if char_count <= idx < word_end_pos:
                        start_word_idx = i
                        break
                    # Move to next word (+1 for the space separator)
                    char_count = word_end_pos + 1

                # Find end word index based on phrase word count
                phrase_words = phrase_lower.split()
                end_word_idx = min(start_word_idx + len(phrase_words) - 1, len(valid_words) - 1)

                # Validate we have timestamps for both indices
                start_ts = valid_words[start_word_idx]['start']
                end_ts = valid_words[end_word_idx]['end']

                if start_ts is not None and end_ts is not None:
                    matches.append({
                        'start': start_ts,
                        'end': end_ts,
                        'phrase': phrase,
                        'word_idx': start_word_idx
                    })

        if not matches:
            return None

        # Return first match for start phrases, last match for end phrases
        if search_start:
            return min(matches, key=lambda m: m['word_idx'])
        else:
            return max(matches, key=lambda m: m['word_idx'])

    refined_ads = []
    for ad in ads:
        refined = ad.copy()
        original_start = ad['start']
        original_end = ad['end']

        # --- Refine START boundary ---
        # Look at the segment containing ad start AND the previous segment
        start_seg_idx = find_segment_at_time(original_start)

        # Collect words from current and previous segment
        search_words = []
        if start_seg_idx > 0:
            prev_seg = segments[start_seg_idx - 1]
            search_words.extend(prev_seg.get('words', []))
        current_seg = segments[start_seg_idx]
        search_words.extend(current_seg.get('words', []))

        # Search for start transition phrases
        start_match = find_phrase_in_words(search_words, AD_START_PHRASES, search_start=True)
        if start_match:
            new_start = start_match['start']
            # Only adjust if it moves start earlier (not later)
            if new_start < original_start:
                refined['start'] = max(0, new_start)
                refined['start_refined'] = True
                refined['start_phrase'] = start_match['phrase']
                logger.info(
                    f"Refined ad start: {original_start:.1f}s -> {refined['start']:.1f}s "
                    f"(found '{start_match['phrase']}')"
                )

        # --- Refine END boundary ---
        # Look at the segment containing ad end AND the next segment
        end_seg_idx = find_segment_at_time(original_end)

        # Collect words from current and next segment
        search_words = []
        current_seg = segments[end_seg_idx]
        search_words.extend(current_seg.get('words', []))
        if end_seg_idx < len(segments) - 1:
            next_seg = segments[end_seg_idx + 1]
            search_words.extend(next_seg.get('words', []))

        # Search for end transition phrases
        end_match = find_phrase_in_words(search_words, AD_END_PHRASES, search_start=False)
        if end_match:
            # For end phrases, we want the time AFTER the phrase (when content resumes)
            new_end = end_match['end']
            # Only adjust if it moves end later (not earlier)
            if new_end > original_end:
                # Get episode duration from last segment
                max_duration = segments[-1]['end'] if segments else float('inf')
                refined['end'] = min(new_end, max_duration)
                refined['end_refined'] = True
                refined['end_phrase'] = end_match['phrase']
                logger.info(
                    f"Refined ad end: {original_end:.1f}s -> {refined['end']:.1f}s "
                    f"(found '{end_match['phrase']}')"
                )

        refined_ads.append(refined)

    return refined_ads


def snap_early_ads_to_zero(ads: List[Dict], threshold: float = EARLY_AD_SNAP_THRESHOLD) -> List[Dict]:
    """Snap ads that start near the beginning of the episode to 0:00.

    Pre-roll ads often have a brief intro or music before the actual ad content
    is detected. If an ad starts within the threshold of the episode start,
    it's almost certainly a pre-roll ad that should start at 0:00.

    Args:
        ads: List of detected ad segments
        threshold: Maximum seconds from start to trigger snapping (default 30.0)

    Returns:
        List of ads with early ads snapped to 0:00
    """
    if not ads:
        return ads

    snapped = []
    for ad in ads:
        ad_copy = ad.copy()
        if ad_copy['start'] > 0 and ad_copy['start'] <= threshold:
            original_start = ad_copy['start']
            ad_copy['start'] = 0.0
            ad_copy['start_snapped'] = True
            ad_copy['original_start'] = original_start
            logger.info(
                f"Snapped early ad to 0:00: {original_start:.1f}s -> 0.0s "
                f"(was within {threshold:.0f}s threshold)"
            )
        snapped.append(ad_copy)

    return snapped


def extend_ad_boundaries_by_content(ads: List[Dict], segments: List[Dict]) -> List[Dict]:
    """Extend ad boundaries by checking adjacent segments for ad-like content.

    For each detected ad, examines transcript text immediately before and after
    the ad boundary. If the adjacent text contains ad indicators (sponsor names,
    URLs, promotional language), the boundary is extended to include it.

    This addresses DAI ads where detection cuts off ~5 seconds too early,
    missing the final call-to-action or URL mention.

    Args:
        ads: List of detected ad segments
        segments: List of transcript segments with 'start', 'end', 'text'

    Returns:
        List of ads with boundaries extended where ad content continues
    """
    if not ads or not segments:
        return ads

    extended = []
    for ad in ads:
        ad_copy = ad.copy()
        ad_start = ad['start']
        ad_end = ad['end']

        # Get the ad's own text to extract sponsor names
        ad_text = get_transcript_text_for_range(segments, ad_start, ad_end).lower()
        ad_sponsors = extract_sponsor_names(ad_text, ad.get('reason'))

        # Check text AFTER ad end for continuation
        after_text = get_transcript_text_for_range(
            segments, ad_end, ad_end + BOUNDARY_EXTENSION_WINDOW
        ).lower()

        if after_text and _text_has_ad_content(after_text, ad_sponsors):
            # Find the last segment in the extension window
            new_end = ad_end
            for seg in segments:
                if seg['start'] >= ad_end and seg['start'] < ad_end + BOUNDARY_EXTENSION_MAX:
                    seg_text = seg.get('text', '').lower()
                    if _text_has_ad_content(seg_text, ad_sponsors):
                        new_end = seg['end']
                    else:
                        break  # Stop at first non-ad segment

            if new_end > ad_end:
                logger.info(
                    f"Extended ad end by content: {ad_end:.1f}s -> {new_end:.1f}s "
                    f"(+{new_end - ad_end:.1f}s, sponsors: {ad_sponsors})"
                )
                ad_copy['end'] = new_end
                ad_copy['end_extended_by_content'] = True

        # Check text BEFORE ad start for continuation
        before_text = get_transcript_text_for_range(
            segments, max(0, ad_start - BOUNDARY_EXTENSION_WINDOW), ad_start
        ).lower()

        if before_text and _text_has_ad_content(before_text, ad_sponsors):
            new_start = ad_start
            # Walk backwards through segments
            for seg in reversed(segments):
                if seg['end'] <= ad_start and seg['end'] > ad_start - BOUNDARY_EXTENSION_MAX:
                    seg_text = seg.get('text', '').lower()
                    if _text_has_ad_content(seg_text, ad_sponsors):
                        new_start = seg['start']
                    else:
                        break

            if new_start < ad_start:
                logger.info(
                    f"Extended ad start by content: {ad_start:.1f}s -> {new_start:.1f}s "
                    f"(-{ad_start - new_start:.1f}s, sponsors: {ad_sponsors})"
                )
                ad_copy['start'] = new_start
                ad_copy['start_extended_by_content'] = True

        extended.append(ad_copy)

    return extended


def _text_has_ad_content(text: str, sponsor_names: set = None) -> bool:
    """Check if text contains ad-like content indicators.

    Args:
        text: Lowercase text to check
        sponsor_names: Set of known sponsor names from the parent ad

    Returns:
        True if text contains ad content indicators
    """
    if not text:
        return False

    # Check for sponsor name mentions
    if sponsor_names:
        for sponsor in sponsor_names:
            if sponsor in text:
                return True

    # Check for URL patterns
    for pattern in AD_CONTENT_URL_PATTERNS:
        if pattern in text:
            return True

    # Check for promotional phrases
    for phrase in AD_CONTENT_PROMO_PHRASES:
        if phrase in text:
            return True

    return False


def extract_sponsor_names(text: str, ad_reason: str = None) -> set:
    """Extract potential sponsor names from transcript text and ad reason.

    Looks for:
    - URLs/domains (e.g., vention, zapier from URLs)
    - Brand names mentioned in ad reason (e.g., "Vention sponsor read")
    - Known sponsor patterns

    Args:
        text: Transcript text to analyze
        ad_reason: Optional reason field from ad detection

    Returns:
        Set of potential sponsor name strings (lowercase)
    """
    sponsors = set()
    text_lower = text.lower()

    # Extract domain names from URLs (e.g., "vention" from "ventionteams.com")
    url_pattern = r'(?:https?://)?(?:www\.)?([a-z0-9]+)(?:teams|\.com|\.tv|\.io|\.co|\.org)'
    for match in re.finditer(url_pattern, text_lower):
        sponsor = match.group(1)
        if len(sponsor) > 2:  # Skip very short matches
            sponsors.add(sponsor)

    # Also look for explicit "dot com" mentions
    dotcom_pattern = r'([a-z]+)\s*(?:dot\s*com|\.com)'
    for match in re.finditer(dotcom_pattern, text_lower):
        sponsor = match.group(1)
        if len(sponsor) > 2:
            sponsors.add(sponsor)

    # Extract brand name from ad reason (e.g., "Vention sponsor read" -> "vention")
    if ad_reason:
        reason_lower = ad_reason.lower()
        # Look for patterns like "X sponsor read", "X ad", "ad for X"
        reason_patterns = [
            r'^([a-z]+)\s+(?:sponsor|ad\b)',  # "Vention sponsor read"
            r'(?:ad for|sponsor(?:ed by)?)\s+([a-z]+)',  # "ad for Vention"
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, reason_lower)
            if match:
                brand = match.group(1)
                # Exclude common non-brand words that appear after "sponsor" or "ad"
                excluded_words = {
                    'the', 'and', 'for', 'with',  # articles/prepositions
                    'read', 'segment', 'content', 'break',  # "sponsor read", "ad segment"
                    'complete', 'partial', 'full',  # "complete ad segment"
                    'spot', 'mention', 'plug', 'insert',  # "sponsor mention"
                    'message', 'promo', 'promotion',  # "ad promo"
                }
                if len(brand) > 2 and brand not in excluded_words:
                    sponsors.add(brand)

    return sponsors


def get_transcript_text_for_range(segments: List[Dict], start_time: float, end_time: float) -> str:
    """Get concatenated transcript text for a time range.

    Args:
        segments: List of transcript segments
        start_time: Start of range in seconds
        end_time: End of range in seconds

    Returns:
        Concatenated text from all segments in range
    """
    texts = []
    for seg in segments:
        # Include segment if it overlaps with the range
        if seg['end'] >= start_time and seg['start'] <= end_time:
            texts.append(seg.get('text', ''))
    return ' '.join(texts)


# --- Timestamp validation (Fix 1: Claude hallucination correction) ---

# Common words that appear in ad reasons but are not brand names
_NON_BRAND_WORDS = {
    'ad', 'ads', 'sponsor', 'sponsored', 'advertisement', 'commercial',
    'host', 'read', 'segment', 'content', 'break', 'detected', 'detection',
    'network', 'inserted', 'dynamically', 'transition', 'promotional',
    'promo', 'promotion', 'mention', 'mentioned', 'plug', 'spot',
    'the', 'and', 'for', 'with', 'from', 'this', 'that', 'into',
    'brand', 'tagline', 'product', 'pitch', 'marketing', 'copy',
    'complete', 'partial', 'full', 'brief', 'short', 'long',
    'message', 'insert', 'mid', 'roll', 'pre', 'post',
}


def _extract_ad_keywords(ad: Dict) -> List[str]:
    """Extract searchable brand/sponsor keywords from an ad's metadata.

    Uses the sponsor field as primary signal, then extracts capitalized words
    from reason and end_text fields.

    Args:
        ad: Ad dict with optional 'sponsor', 'reason', 'end_text' fields

    Returns:
        Lowercase deduplicated list of keywords (length >= MIN_KEYWORD_LENGTH)
    """
    keywords = set()

    # Primary: sponsor field
    sponsor = ad.get('sponsor', '')
    if sponsor and sponsor.lower() not in {'unknown', 'none', ''}:
        keywords.add(sponsor.lower())

    # Secondary: capitalized words from reason and end_text
    for field in ('reason', 'end_text'):
        text = ad.get(field, '')
        if not text:
            continue
        # Find capitalized words (likely brand names)
        caps = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', text)
        for word in caps:
            low = word.lower()
            if low not in _NON_BRAND_WORDS and len(low) >= MIN_KEYWORD_LENGTH:
                keywords.add(low)

    return list(keywords)


def _find_keyword_region(segments: List[Dict], keywords: List[str],
                         window_start: float, window_end: float) -> Optional[Dict]:
    """Search window segments for keyword occurrences and return the best cluster.

    Finds segments containing any keyword, clusters them (merge if gap < 30s),
    and returns the cluster with the most keyword hits.

    Args:
        segments: Transcript segments within the window
        keywords: Lowercase keywords to search for
        window_start: Window start time in seconds
        window_end: Window end time in seconds

    Returns:
        Dict with 'start' and 'end' of best cluster, or None if no matches
    """
    if not keywords or not segments:
        return None

    # Find all segments containing any keyword
    matching_segments = []
    for seg in segments:
        if seg['start'] < window_start or seg['start'] > window_end:
            continue
        text_lower = seg.get('text', '').lower()
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            matching_segments.append({
                'start': seg['start'],
                'end': seg['end'],
                'hits': hits
            })

    if not matching_segments:
        return None

    # Cluster matching segments (merge if gap < 30s)
    matching_segments.sort(key=lambda x: x['start'])
    clusters = [{'start': matching_segments[0]['start'],
                 'end': matching_segments[0]['end'],
                 'hits': matching_segments[0]['hits']}]

    for seg in matching_segments[1:]:
        last = clusters[-1]
        if seg['start'] - last['end'] < 30.0:
            last['end'] = max(last['end'], seg['end'])
            last['hits'] += seg['hits']
        else:
            clusters.append({'start': seg['start'], 'end': seg['end'],
                             'hits': seg['hits']})

    # Return cluster with most keyword hits
    best = max(clusters, key=lambda c: c['hits'])
    return {'start': best['start'], 'end': best['end']}


def validate_ad_timestamps(ads: List[Dict], segments: List[Dict],
                           window_start: float, window_end: float) -> List[Dict]:
    """Validate and correct ad timestamps against actual transcript content.

    For each ad, checks whether the keywords (sponsor, brand names) actually
    appear at the reported position in the transcript. If not, searches the
    window for where they actually appear and corrects the timestamps.

    Args:
        ads: List of ad dicts from Claude
        segments: Transcript segments for the window
        window_start: Window start time in seconds
        window_end: Window end time in seconds

    Returns:
        List of ads with corrected timestamps where needed
    """
    if not ads:
        return []

    validated = []
    for ad in ads:
        keywords = _extract_ad_keywords(ad)

        # No extractable keywords -- can't validate, pass through
        if not keywords:
            validated.append(ad)
            continue

        # Check if keywords exist at the reported position
        reported_text = get_transcript_text_for_range(
            segments, ad['start'], ad['end']
        ).lower()

        found_at_position = any(kw in reported_text for kw in keywords)

        if found_at_position:
            # Timestamps look correct
            validated.append(ad)
            continue

        # Keywords not found at reported position -- search the window
        region = _find_keyword_region(segments, keywords, window_start, window_end)

        if region is None:
            # Keywords not found anywhere in window -- pass through unchanged
            # (let downstream filtering handle it)
            validated.append(ad)
            continue

        # Correct the timestamps
        original_duration = ad['end'] - ad['start']
        corrected = ad.copy()
        corrected['start'] = region['start']
        corrected['end'] = min(region['start'] + original_duration, window_end)
        logger.info(
            f"Timestamp correction: ad '{ad.get('reason', '')[:50]}' "
            f"moved from {ad['start']:.1f}-{ad['end']:.1f}s "
            f"to {corrected['start']:.1f}-{corrected['end']:.1f}s "
            f"(keywords: {keywords})"
        )
        validated.append(corrected)

    return validated


# --- Uncovered tail preservation (Fix 2) ---

def get_uncovered_portions(ad: Dict, covered_regions: List[tuple],
                           min_duration: float = None) -> List[Dict]:
    """Find portions of an ad not covered by pattern-matched regions.

    Instead of binary "covered or not", this identifies uncovered gaps
    (head, middle, tail) and returns them as separate ad segments.

    Args:
        ad: Ad dict with 'start' and 'end'
        covered_regions: List of (start, end) tuples from pattern matches
        min_duration: Minimum duration for an uncovered portion to keep
                     (defaults to MIN_UNCOVERED_TAIL_DURATION)

    Returns:
        List of ad copies with adjusted start/end for uncovered portions.
        Empty list if fully covered. Original ad unchanged if >50% uncovered.
    """
    if min_duration is None:
        min_duration = MIN_UNCOVERED_TAIL_DURATION

    ad_start = ad['start']
    ad_end = ad['end']
    ad_duration = ad_end - ad_start

    if ad_duration <= 0:
        return []

    # Clip covered regions to ad boundaries and collect
    clipped = []
    for cov_start, cov_end in covered_regions:
        c_start = max(cov_start, ad_start)
        c_end = min(cov_end, ad_end)
        if c_start < c_end:
            clipped.append((c_start, c_end))

    if not clipped:
        # No overlap at all -- return original ad
        return [ad]

    # Merge overlapping coverage regions
    clipped.sort()
    merged_coverage = [clipped[0]]
    for start, end in clipped[1:]:
        last_start, last_end = merged_coverage[-1]
        if start <= last_end:
            merged_coverage[-1] = (last_start, max(last_end, end))
        else:
            merged_coverage.append((start, end))

    # Calculate total covered duration
    total_covered = sum(end - start for start, end in merged_coverage)

    # If >50% uncovered, overlap is incidental -- return original ad
    if total_covered / ad_duration <= 0.5:
        return [ad]

    # Identify uncovered gaps
    uncovered = []
    cursor = ad_start

    for cov_start, cov_end in merged_coverage:
        if cursor < cov_start:
            uncovered.append((cursor, cov_start))
        cursor = max(cursor, cov_end)

    # Trailing tail
    if cursor < ad_end:
        uncovered.append((cursor, ad_end))

    # Filter by minimum duration
    uncovered = [(s, e) for s, e in uncovered if (e - s) >= min_duration]

    if not uncovered:
        # Fully covered (no significant gaps)
        return []

    # Build ad copies for each uncovered portion
    portions = []
    for start, end in uncovered:
        portion = ad.copy()
        portion['start'] = start
        portion['end'] = end
        portions.append(portion)

    return portions


def merge_same_sponsor_ads(ads: List[Dict], segments: List[Dict], max_gap: float = 300.0) -> List[Dict]:
    """Merge ads that mention the same sponsor.

    This handles cases where Claude fragments a long ad into multiple pieces
    or mislabels part of an ad as a different sponsor.

    Merge logic:
    - If two ads share a sponsor AND gap < 120s: merge unconditionally (likely same ad break)
    - If two ads share a sponsor AND gap content mentions sponsor: merge (confirmed same sponsor)
    - If gap > max_gap: never merge

    Args:
        ads: List of detected ad segments (sorted by start time)
        segments: List of transcript segments
        max_gap: Maximum gap in seconds to consider for merging (default 5 minutes)

    Returns:
        List of ads with same-sponsor segments merged
    """
    if not ads or len(ads) < 2 or not segments:
        return ads

    # SHORT_GAP_THRESHOLD imported from config.py

    # Sort ads by start time
    ads = sorted(ads, key=lambda x: x['start'])

    # Extract sponsor names for each ad (from transcript AND reason field)
    ad_sponsors = []
    for ad in ads:
        ad_text = get_transcript_text_for_range(segments, ad['start'], ad['end'])
        sponsors = extract_sponsor_names(ad_text, ad.get('reason'))
        ad_sponsors.append(sponsors)
        if sponsors:
            logger.debug(f"Ad {ad['start']:.1f}s-{ad['end']:.1f}s sponsors: {sponsors}")

    # Merge ads that share sponsors
    merged = []
    i = 0
    while i < len(ads):
        current_ad = ads[i].copy()
        current_sponsors = ad_sponsors[i].copy()

        # Look ahead for ads to merge
        j = i + 1
        while j < len(ads):
            next_ad = ads[j]
            next_sponsors = ad_sponsors[j]

            gap_start = current_ad['end']
            gap_end = next_ad['start']
            gap_duration = gap_end - gap_start

            # Skip if gap is too large
            if gap_duration > max_gap:
                break

            # Find common sponsors
            common_sponsors = current_sponsors & next_sponsors

            if common_sponsors:
                should_merge = False
                merge_reason = ""

                # Short gap - merge unconditionally if same sponsor
                if gap_duration <= SHORT_GAP_THRESHOLD:
                    should_merge = True
                    merge_reason = f"short gap ({gap_duration:.0f}s)"
                else:
                    # Longer gap - check if gap content mentions the sponsor
                    gap_text = get_transcript_text_for_range(segments, gap_start, gap_end)
                    gap_sponsors = extract_sponsor_names(gap_text)

                    if common_sponsors & gap_sponsors:
                        should_merge = True
                        merge_reason = "sponsor in gap"

                if should_merge:
                    # Safety check: don't merge if result would be too long
                    # MAX_MERGED_DURATION imported from config.py
                    merged_duration = next_ad['end'] - current_ad['start']
                    if merged_duration > MAX_MERGED_DURATION:
                        logger.info(
                            f"Skipping merge: {current_ad['start']:.1f}s-{current_ad['end']:.1f}s + "
                            f"{next_ad['start']:.1f}s-{next_ad['end']:.1f}s would be {merged_duration:.0f}s "
                            f"(>{MAX_MERGED_DURATION:.0f}s max)"
                        )
                        break  # Don't merge, would create too-long ad

                    logger.info(
                        f"Merging same-sponsor ads: {current_ad['start']:.1f}s-{current_ad['end']:.1f}s + "
                        f"{next_ad['start']:.1f}s-{next_ad['end']:.1f}s "
                        f"(sponsor: {common_sponsors}, reason: {merge_reason})"
                    )
                    # Extend current ad to include next ad
                    current_ad['end'] = next_ad['end']
                    current_ad['merged_sponsor'] = True
                    current_ad['sponsor_names'] = list(common_sponsors)
                    # Combine reason
                    if current_ad.get('reason') and next_ad.get('reason'):
                        current_ad['reason'] = f"{current_ad['reason']} (merged with: {next_ad['reason']})"
                    # Update end_text from later ad
                    if next_ad.get('end_text'):
                        current_ad['end_text'] = next_ad['end_text']
                    # Add sponsors from merged ad
                    current_sponsors = current_sponsors | next_sponsors
                    j += 1
                    continue

            # No merge possible, stop looking
            break

        merged.append(current_ad)
        i = j if j > i + 1 else i + 1

    if len(merged) < len(ads):
        logger.info(f"Sponsor-based merge: {len(ads)} ads -> {len(merged)} ads")

    return merged


def create_windows(segments: List[Dict], window_size: float = WINDOW_SIZE_SECONDS,
                   overlap: float = WINDOW_OVERLAP_SECONDS) -> List[Dict]:
    """Create overlapping windows from transcript segments.

    Args:
        segments: List of transcript segments with 'start', 'end', 'text'
        window_size: Duration of each window in seconds
        overlap: Overlap between consecutive windows in seconds

    Returns:
        List of window dicts with:
            - 'start': window start time (absolute)
            - 'end': window end time (absolute)
            - 'segments': list of segments in this window
    """
    if not segments:
        return []

    # Get total transcript duration
    total_duration = segments[-1]['end']
    step_size = window_size - overlap

    windows = []
    window_start = 0.0

    while window_start < total_duration:
        window_end = min(window_start + window_size, total_duration)

        # Find segments that overlap with this window
        window_segments = []
        for seg in segments:
            # Segment overlaps if it starts before window ends AND ends after window starts
            if seg['start'] < window_end and seg['end'] > window_start:
                window_segments.append(seg)

        if window_segments:
            windows.append({
                'start': window_start,
                'end': window_end,
                'segments': window_segments
            })

        window_start += step_size

    logger.debug(f"Created {len(windows)} windows from {total_duration/60:.1f} min transcript")
    return windows


def deduplicate_window_ads(all_ads: List[Dict], merge_threshold: float = 5.0) -> List[Dict]:
    """Deduplicate and merge ads detected across multiple windows.

    When the same ad spans two windows, both windows may detect it.
    This function merges overlapping detections.

    Args:
        all_ads: Combined list of ads from all windows
        merge_threshold: Seconds within which ads are considered overlapping

    Returns:
        Deduplicated list with overlapping ads merged
    """
    if not all_ads:
        return []

    # Sort by start time
    all_ads = sorted(all_ads, key=lambda x: x['start'])

    # Merge overlapping ads
    merged = [all_ads[0].copy()]

    for current in all_ads[1:]:
        last = merged[-1]

        # Check for overlap (ads within threshold seconds are considered overlapping)
        if current['start'] <= last['end'] + merge_threshold:
            # Merge: extend end time if current goes further
            if current['end'] > last['end']:
                last['end'] = current['end']
                if current.get('end_text'):
                    last['end_text'] = current['end_text']
            # Keep higher confidence
            if current.get('confidence', 0) > last.get('confidence', 0):
                last['confidence'] = current['confidence']
            # Prefer the more descriptive reason regardless of confidence
            current_reason = current.get('reason', '')
            last_reason = last.get('reason', '')
            if len(current_reason) > len(last_reason):
                last['reason'] = current_reason
            # Preserve sponsor field
            current_sponsor = current.get('sponsor', '')
            last_sponsor = last.get('sponsor', '')
            if current_sponsor and not last_sponsor:
                last['sponsor'] = current_sponsor
            # Mark as merged from windows
            last['merged_windows'] = True
        else:
            merged.append(current.copy())

    if len(merged) < len(all_ads):
        logger.info(f"Window deduplication: {len(all_ads)} -> {len(merged)} ads")

    return merged


def _first_not_none(*values):
    """Return the first value that is not None. Unlike `or`, treats 0 and 0.0 as valid."""
    for v in values:
        if v is not None:
            return v
    return None


class AdDetector:
    """Detect advertisements in podcast transcripts using Claude API.

    Detection pipeline (3-stage):
    1. Audio fingerprint matching - identifies identical DAI-inserted ads
    2. Text pattern matching - identifies repeated sponsor reads via TF-IDF
    3. Claude API - analyzes remaining content for unknown ads

    The first two stages are essentially free (no API costs) and can detect
    ads that have been seen before across episodes.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_api_key()
        if not self.api_key:
            logger.warning("No LLM API key found")
        self._llm_client: Optional[LLMClient] = None
        self._db = None
        self._audio_fingerprinter = None
        self._text_pattern_matcher = None
        self._pattern_service = None
        self._sponsor_service = None

    @property
    def client(self) -> Optional[LLMClient]:
        """Backward compatibility property for accessing the LLM client."""
        return self._llm_client

    @property
    def db(self):
        """Lazy load database connection."""
        if self._db is None:
            from database import Database
            self._db = Database()
        return self._db

    @property
    def audio_fingerprinter(self):
        """Lazy load audio fingerprinter."""
        if self._audio_fingerprinter is None:
            try:
                from audio_fingerprinter import AudioFingerprinter
                self._audio_fingerprinter = AudioFingerprinter(db=self.db)
            except ImportError:
                logger.warning("Audio fingerprinting not available")
                self._audio_fingerprinter = None
        return self._audio_fingerprinter

    @property
    def text_pattern_matcher(self):
        """Lazy load text pattern matcher."""
        if self._text_pattern_matcher is None:
            try:
                from text_pattern_matcher import TextPatternMatcher
                self._text_pattern_matcher = TextPatternMatcher(db=self.db)
            except ImportError:
                logger.warning("Text pattern matching not available")
                self._text_pattern_matcher = None
        return self._text_pattern_matcher

    @property
    def pattern_service(self):
        """Lazy load pattern service for match recording."""
        if self._pattern_service is None:
            try:
                from pattern_service import PatternService
                self._pattern_service = PatternService(db=self.db)
            except ImportError:
                logger.warning("Pattern service not available")
                self._pattern_service = None
        return self._pattern_service

    @property
    def sponsor_service(self):
        """Lazy load sponsor service for sponsor lookup."""
        if self._sponsor_service is None:
            try:
                from sponsor_service import SponsorService
                self._sponsor_service = SponsorService(db=self.db)
            except ImportError:
                logger.warning("Sponsor service not available")
                self._sponsor_service = None
        return self._sponsor_service

    def initialize_client(self):
        """Initialize LLM client."""
        if self._llm_client is None and self.api_key:
            try:
                self._llm_client = get_llm_client()
                logger.info(f"LLM client initialized: {self._llm_client.get_provider_name()}")
            except Exception as e:
                logger.error(f"Failed to initialize LLM client: {e}")
                raise

    def get_available_models(self) -> List[Dict]:
        """Get list of available models from LLM provider.

        Ensures currently configured models always appear in the list,
        even if the API doesn't advertise them.
        """
        try:
            self.initialize_client()
            if not self._llm_client:
                return []

            models = self._llm_client.list_models()
            model_list = [
                {'id': m.id, 'name': m.name, 'created': m.created}
                for m in models
            ]
            return self._ensure_configured_models_present(model_list)
        except Exception as e:
            logger.warning(f"Could not fetch models from API: {e}")
            # Return known models as fallback
            return [
                {'id': 'claude-opus-4-6', 'name': 'Claude Opus 4.6'},
                {'id': 'claude-sonnet-4-5-20250929', 'name': 'Claude Sonnet 4.5'},
                {'id': 'claude-haiku-4-5-20251001', 'name': 'Claude Haiku 4.5'},
                {'id': 'claude-opus-4-5-20251101', 'name': 'Claude Opus 4.5'},
                {'id': 'claude-opus-4-1-20250805', 'name': 'Claude Opus 4.1'},
                {'id': 'claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4'},
                {'id': 'claude-opus-4-20250514', 'name': 'Claude Opus 4'},
            ]

    def _ensure_configured_models_present(self, models_list: List[Dict]) -> List[Dict]:
        """Ensure currently-configured models always appear in the model list.

        If the API/wrapper doesn't advertise a model that's actively configured
        (e.g., set as first pass or verification model), inject it so the settings UI
        shows it and doesn't lose the selection.
        """
        existing_ids = {m['id'] for m in models_list}
        configured_models = []
        try:
            configured_models.append(self.get_model())
            configured_models.append(self.get_verification_model())
        except Exception:
            pass

        for model_id in configured_models:
            if model_id and model_id not in existing_ids:
                logger.info(f"Added configured model '{model_id}' to model list")
                models_list.insert(0, {
                    'id': model_id,
                    'name': model_id,
                    'created': None
                })
                existing_ids.add(model_id)

        return models_list

    def get_model(self) -> str:
        """Get configured model from database or default."""
        try:
            model = self.db.get_setting('claude_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not load model from DB: {e}")
        return DEFAULT_MODEL

    def get_verification_model(self) -> str:
        """Get verification pass model from database or fall back to first pass model."""
        try:
            model = self.db.get_setting('verification_model')
            if model:
                return model
        except Exception:
            pass
        return self.get_model()

    def get_system_prompt(self) -> str:
        """Get system prompt from database or default, with dynamic sponsors appended."""
        try:
            prompt = self.db.get_setting('system_prompt')
            if prompt:
                return self._inject_dynamic_sponsors(prompt)
        except Exception as e:
            logger.warning(f"Could not load system prompt from DB: {e}")

        # Default fallback
        from database import DEFAULT_SYSTEM_PROMPT
        return self._inject_dynamic_sponsors(DEFAULT_SYSTEM_PROMPT)

    def get_verification_prompt(self) -> str:
        """Get verification prompt from database or default, with dynamic sponsors appended."""
        try:
            prompt = self.db.get_setting('verification_prompt')
            if prompt:
                return self._inject_dynamic_sponsors(prompt)
        except Exception:
            pass
        from database import DEFAULT_VERIFICATION_PROMPT
        return self._inject_dynamic_sponsors(DEFAULT_VERIFICATION_PROMPT)

    def _inject_dynamic_sponsors(self, prompt: str) -> str:
        """Append dynamic sponsor database to a prompt at detection time.

        This supplements the hardcoded sponsor list in the stored prompt with
        any sponsors added via API or discovered during processing.
        """
        try:
            if not self.sponsor_service:
                return prompt
            sponsor_list = self.sponsor_service.get_claude_sponsor_list()
            if not sponsor_list:
                return prompt
            return (
                prompt
                + "\n\nDYNAMIC SPONSOR DATABASE (current known sponsors - treat as high confidence):\n"
                + sponsor_list
            )
        except Exception as e:
            logger.warning(f"Could not inject dynamic sponsors into prompt: {e}")
            return prompt

    def _get_podcast_sponsor_history(self, podcast_slug: str) -> str:
        """Get previously detected sponsor names for a podcast from ad_patterns.

        Returns a formatted string for inclusion in the description section,
        or empty string if no sponsors found.
        """
        if not podcast_slug:
            return ""
        try:
            patterns = self.db.get_ad_patterns(podcast_id=podcast_slug)
            sponsors = set()
            for p in patterns:
                sponsor = p.get('sponsor')
                if sponsor and sponsor.lower() not in ('unknown', 'advertisement detected', ''):
                    sponsors.add(sponsor)
            if sponsors:
                sponsor_list = ', '.join(sorted(sponsors))
                return f"Previously detected sponsors for this podcast: {sponsor_list}\n"
        except Exception as e:
            logger.warning(f"Could not fetch sponsor history for {podcast_slug}: {e}")
        return ""

    def get_user_prompt_template(self) -> str:
        """Get user prompt template (hardcoded, not configurable)."""
        return USER_PROMPT_TEMPLATE

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried."""
        return is_retryable_error(error)

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay with optional jitter."""
        delay = min(
            RETRY_CONFIG['base_delay'] * (RETRY_CONFIG['exponential_base'] ** attempt),
            RETRY_CONFIG['max_delay']
        )
        if RETRY_CONFIG['jitter']:
            delay = delay * (0.5 + random.random())  # 50-150% of delay
        return delay

    def _parse_ads_from_response(self, response_text: str, slug: str = None,
                                  episode_id: str = None) -> List[Dict]:
        """Parse ad segments from Claude's JSON response.

        Args:
            response_text: Raw text response from Claude
            slug: Podcast slug for logging
            episode_id: Episode ID for logging

        Returns:
            List of validated ad dicts with start, end, confidence, reason, end_text
        """
        # Helper to filter out invalid sponsor values like literal "None", "unknown", etc.
        def get_valid_value(value):
            if not value:
                return None
            str_value = str(value).strip()
            # Reject empty or too-short values
            if len(str_value) < 2:
                return None
            # Reject known invalid values
            if str_value.lower() in INVALID_SPONSOR_VALUES:
                return None
            return str_value

        def _text_is_duplicate(a: str, b: str) -> bool:
            """Check if two strings are essentially the same text (one starts with the other or >80% word overlap)."""
            a_lower = a.lower().strip()
            b_lower = b.lower().strip()
            # One starts with the other
            if a_lower.startswith(b_lower) or b_lower.startswith(a_lower):
                return True
            # High word overlap
            a_words = set(a_lower.split())
            b_words = set(b_lower.split())
            if not a_words or not b_words:
                return False
            overlap = len(a_words & b_words)
            smaller = min(len(a_words), len(b_words))
            return overlap / smaller > 0.8 if smaller > 0 else False

        def extract_sponsor_from_text(text: str) -> str | None:
            """Extract sponsor name from descriptive text like 'This is a BetterHelp advertisement'."""
            if not text:
                return None
            # Patterns to match sponsor mentions in text
            patterns = [
                # "X sponsor read" or "X ad read"
                r'^(\w+(?:\s+\w+)?)\s+(?:sponsor|ad)\s+read',
                # "This is a X advertisement"
                r'(?:this is (?:a|an) )?(\w+(?:\s+\w+)?)\s+(?:ad|advertisement|sponsor)',
                # "Ad/sponsor for/by/from X"
                r'(?:ad|advertisement|sponsor)(?:ship)?\s+(?:for|by|from)\s+(\w+(?:\s+\w+)?)',
                # "promoting X"
                r'promoting\s+(\w+(?:\s+\w+)?)',
                # "brought to you by X"
                r'brought to you by\s+(\w+(?:\s+\w+)?)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    sponsor = match.group(1).strip()
                    # Validate extracted sponsor
                    if len(sponsor) < 2:
                        continue
                    if sponsor.lower() in INVALID_SPONSOR_VALUES:
                        continue
                    # Filter out common words that aren't sponsors
                    if sponsor.lower() in ('a', 'an', 'the', 'this', 'that', 'another', 'host'):
                        continue
                    # Reject if first word is a common English word (indicates garbage capture)
                    # e.g., "not an" from "This is not an advertisement"
                    first_word = sponsor.split()[0].lower() if sponsor.split() else ''
                    if first_word in INVALID_SPONSOR_CAPTURE_WORDS:
                        continue
                    # Reject all-lowercase multi-word phrases (proper nouns are capitalized)
                    # e.g., "consistent with" is not a sponsor name
                    if ' ' in sponsor and sponsor == sponsor.lower():
                        continue
                    return sponsor
            return None

        def extract_sponsor_name(ad: dict) -> str:
            """Extract sponsor/advertiser name from ad dict using priority fields, pattern matching, and dynamic scanning."""
            # Phase 1: Check priority fields in order
            for field in SPONSOR_PRIORITY_FIELDS:
                value = get_valid_value(ad.get(field))
                if value:
                    return value

            # Phase 2: Pattern match any key containing sponsor/brand/advertiser keywords
            for key in ad.keys():
                key_lower = key.lower()
                for keyword in SPONSOR_PATTERN_KEYWORDS:
                    if keyword in key_lower:
                        value = get_valid_value(ad.get(key))
                        if value:
                            return value

            # Phase 3: Dynamic scan - check ALL non-structural string fields for short values
            # that could be sponsor names (< 80 chars). This catches whatever field names
            # Claude invents (topics, label, sponsor_or_topic, etc.)
            priority_lower = {f.lower() for f in SPONSOR_PRIORITY_FIELDS}
            for key, val in ad.items():
                key_lower = key.lower()
                if key_lower in STRUCTURAL_FIELDS or key_lower in priority_lower:
                    continue
                if isinstance(val, str) and len(val) < 80:
                    value = get_valid_value(val)
                    if value:
                        return value

            # Phase 4: Dynamic scan - check ALL non-structural string fields for longer text
            # and try to extract sponsor names using regex patterns
            for key, val in ad.items():
                if key.lower() in STRUCTURAL_FIELDS:
                    continue
                if isinstance(val, str) and len(val) > 10:
                    sponsor = extract_sponsor_from_text(val)
                    if sponsor:
                        return sponsor

            return 'Advertisement detected'

        try:
            ads = None
            extraction_method = None

            # Pre-process: Remove common preamble patterns that break JSON parsing
            cleaned_text = response_text.strip()
            preamble_patterns = [
                r'^(?:Here (?:are|is) (?:the )?(?:detected )?ads?[:\s]*)',
                r'^(?:I (?:found|detected|identified)[^:]*[:\s]*)',
                r'^(?:The following (?:ads|advertisements)[^:]*[:\s]*)',
                r'^(?:Based on (?:my|the) analysis[^:]*[:\s]*)',
                r'^(?:After (?:reviewing|analyzing)[^:]*[:\s]*)',
            ]
            for pattern in preamble_patterns:
                match = re.match(pattern, cleaned_text, re.IGNORECASE)
                if match:
                    cleaned_text = cleaned_text[match.end():].strip()
                    logger.debug(f"[{slug}:{episode_id}] Removed preamble: '{match.group()[:50]}'")
                    break

            # Strategy 0: Try to parse as JSON object and extract ads from various structures
            # Handles responses like {"ads": [...]}, {"segments": [...]}, {"advertisement_segments": [...]}
            # Also handles nested structures like {"window": {"ads_detected": [...]}}
            try:
                parsed = json.loads(cleaned_text)
                if isinstance(parsed, dict):
                    # Check for nested window structure first (e.g., {"window": {"ads_detected": [...]}})
                    if 'window' in parsed and isinstance(parsed['window'], dict):
                        window = parsed['window']
                        for key in ['ads_detected', 'ads', 'advertisement_segments', 'ads_and_sponsorships', 'segments']:
                            if key in window and isinstance(window[key], list):
                                if key == 'segments':
                                    ads = [s for s in window[key]
                                           if isinstance(s, dict) and s.get('type') == 'advertisement']
                                else:
                                    ads = window[key]
                                extraction_method = f"json_object_window_{key}"
                                break
                    # Check top-level keys if window didn't match
                    if ads is None:
                        if 'ads' in parsed and isinstance(parsed['ads'], list):
                            ads = parsed['ads']
                            extraction_method = "json_object_ads_key"
                        elif 'ads_detected' in parsed and isinstance(parsed['ads_detected'], list):
                            ads = parsed['ads_detected']
                            extraction_method = "json_object_ads_detected_key"
                        elif 'advertisement_segments' in parsed and isinstance(parsed['advertisement_segments'], list):
                            ads = parsed['advertisement_segments']
                            extraction_method = "json_object_advertisement_segments_key"
                        elif 'ads_and_sponsorships' in parsed and isinstance(parsed['ads_and_sponsorships'], list):
                            ads = parsed['ads_and_sponsorships']
                            extraction_method = "json_object_ads_and_sponsorships_key"
                        elif 'segments' in parsed and isinstance(parsed['segments'], list):
                            # Filter to only advertisement type segments
                            ads = [s for s in parsed['segments']
                                   if isinstance(s, dict) and s.get('type') == 'advertisement']
                            extraction_method = "json_object_segments_key"
                        else:
                            # No recognizable ad structure - check for other possible keys
                            ads = []
                            extraction_method = "json_object_no_ads"
                elif isinstance(parsed, list):
                    ads = parsed
                    extraction_method = "json_array_direct"
            except json.JSONDecodeError:
                pass

            # Strategy 1: Try to extract from markdown code block first
            if ads is None:
                code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
                if code_block_match:
                    try:
                        ads = json.loads(code_block_match.group(1))
                        extraction_method = "markdown_code_block"
                    except json.JSONDecodeError:
                        pass

            # Strategy 2: Find all potential JSON arrays and use the last valid one
            if ads is None:
                last_valid_ads = None
                for match in re.finditer(r'\[(?:[^\[\]]*|\[(?:[^\[\]]*|\[[^\[\]]*\])*\])*\]', response_text):
                    try:
                        potential_ads = json.loads(match.group())
                        if isinstance(potential_ads, list):
                            if not potential_ads or (potential_ads and isinstance(potential_ads[0], dict) and 'start' in potential_ads[0]):
                                last_valid_ads = potential_ads
                    except json.JSONDecodeError:
                        continue

                if last_valid_ads is not None:
                    ads = last_valid_ads
                    extraction_method = "regex_json_array"

            # Strategy 3: Fallback to original first-to-last bracket logic
            if ads is None:
                clean_response = re.sub(r'```json\s*', '', response_text)
                clean_response = re.sub(r'```\s*', '', clean_response)

                start_idx = clean_response.find('[')
                end_idx = clean_response.rfind(']') + 1

                if start_idx >= 0 and end_idx > start_idx:
                    json_str = clean_response[start_idx:end_idx]
                    try:
                        ads = json.loads(json_str)
                        extraction_method = "bracket_fallback"
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"[{slug}:{episode_id}] Strategy 3 JSON parse failed: {e} "
                            f"(length={len(json_str)}, start={json_str[:50]!r}, end={json_str[-50:]!r})"
                        )

            if ads is None or not isinstance(ads, list):
                logger.warning(f"[{slug}:{episode_id}] No valid JSON array found in response")
                return []

            # Validate and normalize ads - handle various field name patterns
            valid_ads = []
            for ad in ads:
                if isinstance(ad, dict):
                    # Log raw ad object for debugging
                    logger.debug(f"[{slug}:{episode_id}] Raw ad from LLM: {json.dumps(ad, default=str)[:500]}")
                    # Try various field name patterns for start/end times
                    # Use _first_not_none instead of `or` to avoid dropping 0.0 (pre-roll ads)
                    start_val = _first_not_none(
                        ad.get('start'), ad.get('start_time'), ad.get('start_timestamp'),
                        ad.get('ad_start_timestamp'), ad.get('start_time_seconds')
                    )
                    end_val = _first_not_none(
                        ad.get('end'), ad.get('end_time'), ad.get('end_timestamp'),
                        ad.get('ad_end_timestamp'), ad.get('end_time_seconds')
                    )

                    if start_val is not None and end_val is not None:
                        try:
                            start = parse_timestamp(start_val)
                            end = parse_timestamp(end_val)
                            if end > start:  # Skip invalid segments
                                # Filter out explicitly marked non-ads
                                is_ad_val = ad.get('is_ad')
                                if is_ad_val is not None:
                                    if str(is_ad_val).lower() in ('false', 'no', '0', 'none'):
                                        logger.info(f"[{slug}:{episode_id}] Skipping non-ad: "
                                                    f"{start:.1f}s-{end:.1f}s (is_ad={is_ad_val})")
                                        continue

                                # Filter by classification/type field
                                classification = str(ad.get('classification') or ad.get('type') or '').lower()
                                if classification in NOT_AD_CLASSIFICATIONS:
                                    logger.info(f"[{slug}:{episode_id}] Skipping non-ad: "
                                                f"{start:.1f}s-{end:.1f}s (classification={classification})")
                                    continue

                                # Extract sponsor/advertiser name using priority fields + pattern matching
                                # Try extract_sponsor_name first for a real sponsor name.
                                # If it returns the default, fall back to Claude's raw reason.
                                reason = extract_sponsor_name(ad)
                                existing_reason = ad.get('reason')
                                if reason == 'Advertisement detected':
                                    if existing_reason and isinstance(existing_reason, str) and len(existing_reason) > 3:
                                        reason = existing_reason
                                elif existing_reason and isinstance(existing_reason, str) and len(existing_reason) > len(reason) + 5:
                                    # Claude's reason is substantially more descriptive than the bare sponsor name
                                    reason = existing_reason

                                # Extract description from Claude's response to enrich the reason
                                # Dynamic scan: check ALL non-structural string fields > 10 chars
                                # Skip 'reason' (already used above); duplication with sponsor handled at combine time
                                description = None
                                for key, val in ad.items():
                                    if key.lower() in STRUCTURAL_FIELDS:
                                        continue
                                    if key == 'reason':
                                        continue  # Already handled as primary reason
                                    if isinstance(val, str) and len(val) > 10:
                                        # Prefer longer descriptive text over short values
                                        if description is None or len(val) > len(description):
                                            description = val
                                # Truncate if very long (will be further truncated below)
                                if description and len(description) > 300:
                                    description = description[:297] + "..."

                                # Combine sponsor + description in reason field
                                if description:
                                    if reason and reason != 'Advertisement detected':
                                        # Avoid duplication: check if description is essentially the same text
                                        if not _text_is_duplicate(reason, description):
                                            if len(description) > 150:
                                                description = description[:147] + "..."
                                            reason = f"{reason}: {description}"
                                    elif not reason or reason == 'Advertisement detected':
                                        reason = description

                                # Normalize confidence to 0-1 range
                                # Claude sometimes returns percentage (0-100) instead of fraction (0-1)
                                raw_conf = float(ad.get('confidence', 0.8))
                                norm_conf = raw_conf / 100.0 if raw_conf > 1.0 else raw_conf
                                norm_conf = min(1.0, max(0.0, norm_conf))

                                # Dynamic validation: require positive evidence this is an ad
                                # instead of blocklisting content indicators (which keeps growing)
                                duration = end - start
                                has_sponsor_field = any(
                                    get_valid_value(ad.get(f))
                                    for f in SPONSOR_PRIORITY_FIELDS
                                )
                                has_known_sponsor = (
                                    self.sponsor_service and
                                    self.sponsor_service.find_sponsor_in_text(reason)
                                ) if reason else False
                                has_ad_language = bool(extract_sponsor_from_text(reason)) if reason else False

                                if not has_sponsor_field and not has_known_sponsor and not has_ad_language:
                                    # Low confidence + no evidence = reject regardless of duration
                                    if norm_conf < LOW_CONFIDENCE:
                                        logger.info(
                                            f"[{slug}:{episode_id}] Rejecting low-confidence non-sponsor: "
                                            f"{start:.1f}s-{end:.1f}s ({duration:.0f}s, conf={norm_conf:.0%}) - "
                                            f"reason: {reason[:100] if reason else 'None'}"
                                        )
                                        continue
                                    # No positive ad evidence -- apply duration gate
                                    # Short segments (<CONTENT_DURATION_THRESHOLD) get benefit of doubt
                                    # Long segments are almost certainly content descriptions
                                    if duration >= CONTENT_DURATION_THRESHOLD:
                                        logger.info(
                                            f"[{slug}:{episode_id}] Rejecting suspected content: "
                                            f"{start:.1f}s-{end:.1f}s ({duration:.0f}s) - "
                                            f"no sponsor identified in reason: {reason[:100] if reason else 'None'}"
                                        )
                                        continue
                                    # For shorter segments without evidence, log warning but allow through
                                    elif duration >= LOW_EVIDENCE_WARN_THRESHOLD:
                                        logger.warning(
                                            f"[{slug}:{episode_id}] Low-confidence ad (no sponsor found): "
                                            f"{start:.1f}s-{end:.1f}s ({duration:.0f}s) - "
                                            f"reason: {reason[:100] if reason else 'None'}"
                                        )

                                # Log extracted ad details for production visibility
                                logger.info(f"[{slug}:{episode_id}] Extracted ad: {start:.1f}s-{end:.1f}s, reason='{reason}', fields={list(ad.keys())}")
                                ad_entry = {
                                    'start': start,
                                    'end': end,
                                    'confidence': norm_conf,
                                    'reason': reason,
                                    'end_text': ad.get('end_text') or ''
                                }
                                # Store sponsor name separately for UI display
                                sponsor_name = extract_sponsor_name(ad)
                                if sponsor_name and sponsor_name != 'Advertisement detected':
                                    ad_entry['sponsor'] = sponsor_name
                                valid_ads.append(ad_entry)
                        except ValueError as e:
                            logger.warning(f"[{slug}:{episode_id}] Skipping ad with invalid timestamp: {e}")
                            continue

            return valid_ads

        except json.JSONDecodeError as e:
            logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
            return []

    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None, episode_description: str = None,
                   podcast_description: str = None,
                   progress_callback=None,
                   audio_analysis=None) -> Optional[Dict]:
        """Detect ad segments using Claude API with sliding window approach.

        Processes transcript in overlapping windows to ensure ads at chunk
        boundaries are not missed. Windows are 10 minutes with 3 minute overlap.

        Args:
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress
        """
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Pre-detect non-English segments as automatic ads (DAI in other languages)
            foreign_language_ads = self._detect_foreign_language_ads(segments, slug, episode_id)
            if foreign_language_ads:
                logger.info(f"[{slug}:{episode_id}] Auto-detected {len(foreign_language_ads)} "
                           f"non-English segments as ads")

            # Create overlapping windows from transcript
            windows = create_windows(segments)
            total_duration = segments[-1]['end'] if segments else 0

            logger.info(f"[{slug}:{episode_id}] Processing {len(windows)} windows "
                       f"({WINDOW_SIZE_SECONDS/60:.0f}min size, {WINDOW_OVERLAP_SECONDS/60:.0f}min overlap) "
                       f"for {total_duration/60:.1f}min episode")

            # Get prompts and model
            system_prompt = self.get_system_prompt()
            user_prompt_template = self.get_user_prompt_template()
            model = self.get_model()

            logger.info(f"[{slug}:{episode_id}] Using model: {model}")
            logger.debug(f"[{slug}:{episode_id}] System prompt ({len(system_prompt)} chars)")

            # Prepare description section (shared across windows)
            description_section = ""
            if podcast_description:
                description_section = f"Podcast Description:\n{podcast_description}\n\n"
                logger.info(f"[{slug}:{episode_id}] Including podcast description ({len(podcast_description)} chars)")
            if episode_description:
                description_section += f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Including episode description ({len(episode_description)} chars)")

            # Add podcast-specific sponsor history from ad_patterns
            sponsor_history = self._get_podcast_sponsor_history(slug)
            if sponsor_history:
                description_section += sponsor_history
                logger.info(f"[{slug}:{episode_id}] Including sponsor history: {sponsor_history.strip()}")

            all_window_ads = []
            all_raw_responses = []
            max_retries = RETRY_CONFIG['max_retries']

            # Instantiate audio signal formatter if audio analysis available
            audio_enforcer = None
            if audio_analysis:
                from audio_enforcer import AudioEnforcer
                audio_enforcer = AudioEnforcer()

            # Process each window
            for i, window in enumerate(windows):
                # Report progress for each window (keeps UI indicator alive)
                if progress_callback:
                    # First pass: 50-80% range (detecting phase)
                    progress = 50 + int((i / max(len(windows), 1)) * 30)
                    progress_callback(f"detecting:{i+1}/{len(windows)}", progress)

                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                # Build transcript for this window (segments have absolute timestamps)
                transcript_lines = []
                for seg in window_segments:
                    transcript_lines.append(f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")
                transcript = "\n".join(transcript_lines)

                # Add audio context if available for this window
                audio_context = ""
                if audio_enforcer:
                    audio_context = audio_enforcer.format_for_window(
                        audio_analysis, window_start, window_end
                    )

                # Add window context to prompt
                window_context = f"""

=== WINDOW {i+1}/{len(windows)}: {window_start/60:.1f}-{window_end/60:.1f} minutes ===
- Use absolute timestamps from transcript (as shown in brackets)
- If an ad starts before this window, use the first timestamp with note "continues from previous"
- If an ad extends past this window, use {window_end:.1f} with note "continues in next"
"""

                prompt = user_prompt_template.format(
                    podcast_name=podcast_name,
                    episode_title=episode_title,
                    description_section=description_section,
                    transcript=transcript
                ) + audio_context + window_context

                logger.info(f"[{slug}:{episode_id}] Window {i+1}/{len(windows)}: "
                           f"{window_start/60:.1f}-{window_end/60:.1f}min, {len(window_segments)} segments")

                # Call Claude API with retry logic
                response = None
                last_error = None

                for attempt in range(max_retries + 1):
                    try:
                        response = self._llm_client.messages_create(
                            model=model,
                            max_tokens=2000,
                            temperature=0.0,
                            system=system_prompt,
                            messages=[{"role": "user", "content": prompt}],
                            timeout=120.0,
                            response_format={"type": "json_object"}
                        )
                        break
                    except Exception as e:
                        last_error = e
                        if self._is_retryable_error(e) and attempt < max_retries:
                            if is_rate_limit_error(e):
                                delay = 60.0
                                logger.warning(f"[{slug}:{episode_id}] Window {i+1} rate limit, waiting {delay:.0f}s")
                            else:
                                delay = self._calculate_backoff(attempt)
                                logger.warning(f"[{slug}:{episode_id}] Window {i+1} API error: {e}. Retrying in {delay:.1f}s")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"[{slug}:{episode_id}] Window {i+1} failed: {e}")
                            return {
                                "ads": [],
                                "status": "failed",
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "retryable": self._is_retryable_error(e),
                                "prompt": f"Failed at window {i+1}"
                            }

                if response is None:
                    logger.error(f"[{slug}:{episode_id}] Window {i+1} - no response after retries")
                    continue

                # Parse response (LLMResponse.content is already extracted text)
                response_text = response.content
                all_raw_responses.append(f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}")

                # Parse ads from response
                window_ads = self._parse_ads_from_response(response_text, slug, episode_id)

                # Validate timestamps against actual transcript content
                # (catches Claude hallucinating ad positions)
                window_ads = validate_ad_timestamps(
                    window_ads, window_segments, window_start, window_end
                )

                # Filter ads to window bounds - Claude sometimes hallucinates start=0.0
                # when no ads found, speculating about "beginning of episode"
                # MIN_OVERLAP_TOLERANCE, MAX_AD_DURATION_WINDOW imported from config.py

                valid_window_ads = []
                for ad in window_ads:
                    duration = ad['end'] - ad['start']
                    in_window = (ad['start'] >= window_start - MIN_OVERLAP_TOLERANCE and
                                 ad['start'] <= window_end + MIN_OVERLAP_TOLERANCE)
                    reasonable_length = duration <= MAX_AD_DURATION_WINDOW

                    if in_window and reasonable_length:
                        valid_window_ads.append(ad)
                    else:
                        logger.warning(
                            f"[{slug}:{episode_id}] Window {i+1} rejected ad: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({duration:.0f}s) - "
                            f"{'outside window' if not in_window else 'too long'}"
                        )

                window_ads = valid_window_ads
                logger.info(f"[{slug}:{episode_id}] Window {i+1} found {len(window_ads)} ads")

                all_window_ads.extend(window_ads)

            # Deduplicate ads across windows
            final_ads = deduplicate_window_ads(all_window_ads)

            # Merge in foreign language ads (auto-detected non-English segments)
            if foreign_language_ads:
                final_ads = self._merge_detection_results(final_ads + foreign_language_ads)
                logger.info(f"[{slug}:{episode_id}] Merged {len(foreign_language_ads)} foreign language ads")

            total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
            logger.info(f"[{slug}:{episode_id}] Total after dedup: {len(final_ads)} ads ({total_ad_time/60:.1f} min)")

            for ad in final_ads:
                logger.info(f"[{slug}:{episode_id}] Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                           f"({ad['end']-ad['start']:.0f}s) end_text='{(ad.get('end_text') or '')[:50]}'")

            return {
                "ads": final_ads,
                "status": "success",
                "raw_response": "\n\n".join(all_raw_responses),
                "prompt": f"Processed {len(windows)} windows",
                "model": model
            }

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Ad detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}

    def process_transcript(self, segments: List[Dict], podcast_name: str = "Unknown",
                          episode_title: str = "Unknown", slug: str = None,
                          episode_id: str = None, episode_description: str = None,
                          audio_path: str = None,
                          podcast_id: str = None, network_id: str = None,
                          skip_patterns: bool = False,
                          podcast_description: str = None,
                          progress_callback=None,
                          audio_analysis=None) -> Dict:
        """Process transcript for ad detection using three-stage pipeline.

        Pipeline stages:
        1. Audio fingerprint matching (if audio_path provided)
        2. Text pattern matching
        3. Claude API for remaining segments

        Args:
            segments: Transcript segments
            podcast_name: Name of podcast
            episode_title: Title of episode
            slug: Podcast slug
            episode_id: Episode ID
            episode_description: Episode description
            audio_path: Path to audio file for fingerprinting
            podcast_id: Podcast ID for pattern scoping
            network_id: Network ID for pattern scoping
            skip_patterns: If True, skip stages 1 & 2 (pattern DB), go directly to Claude
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress

        Returns:
            Dict with ads, status, and detection metadata
        """
        all_ads = []
        pattern_matched_regions = []  # Regions covered by pattern matching
        detection_stats = {
            'fingerprint_matches': 0,
            'text_pattern_matches': 0,
            'claude_matches': 0,
            'skip_patterns': skip_patterns
        }

        if skip_patterns:
            logger.info(f"[{slug}:{episode_id}] Full analysis mode: Skipping pattern DB (stages 1 & 2)")

        # Get false positive corrections for this episode to prevent re-proposing rejected ads
        false_positive_regions = []
        false_positive_texts = []
        if not skip_patterns and self.db:
            try:
                false_positive_regions = self.db.get_false_positive_corrections(episode_id)
                if false_positive_regions:
                    logger.debug(f"[{slug}:{episode_id}] Found {len(false_positive_regions)} false positive regions to exclude")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to get false positive corrections: {e}")

            # Get cross-episode false positive texts for content matching
            try:
                fp_entries = self.db.get_podcast_false_positive_texts(slug)
                false_positive_texts = [e['text'] for e in fp_entries if e.get('text')]
                if false_positive_texts:
                    logger.debug(f"[{slug}:{episode_id}] Loaded {len(false_positive_texts)} cross-episode false positive texts")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to get cross-episode false positives: {e}")

        # Stage 1: Audio Fingerprint Matching (skip if skip_patterns=True)
        if not skip_patterns and audio_path and self.audio_fingerprinter and self.audio_fingerprinter.is_available():
            try:
                logger.info(f"[{slug}:{episode_id}] Stage 1: Audio fingerprint matching")
                fp_matches = self.audio_fingerprinter.find_matches(audio_path)

                fp_added = 0
                for match in fp_matches:
                    # Skip if this region was previously marked as false positive
                    if self._is_region_covered(match.start, match.end, [(fp['start'], fp['end']) for fp in false_positive_regions]):
                        logger.debug(f"[{slug}:{episode_id}] Skipping fingerprint match {match.start:.1f}s-{match.end:.1f}s (false positive)")
                        continue

                    # Build reason with pattern reference
                    if match.sponsor:
                        reason = f"{match.sponsor} (pattern #{match.pattern_id})"
                    else:
                        reason = f"Pattern #{match.pattern_id} (fingerprint)"

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': reason,
                        'sponsor': match.sponsor,
                        'detection_stage': 'fingerprint',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append((match.start, match.end))
                    fp_added += 1

                    # Record pattern match for metrics and promotion
                    if self.pattern_service and match.pattern_id:
                        self.pattern_service.record_pattern_match(match.pattern_id, episode_id)

                detection_stats['fingerprint_matches'] = fp_added
                if fp_matches:
                    logger.info(f"[{slug}:{episode_id}] Fingerprint stage found {len(fp_matches)} ads")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Fingerprint matching failed: {e}")

        # Stage 2: Text Pattern Matching (skip if skip_patterns=True)
        if not skip_patterns and self.text_pattern_matcher and self.text_pattern_matcher.is_available():
            try:
                logger.info(f"[{slug}:{episode_id}] Stage 2: Text pattern matching")
                text_matches = self.text_pattern_matcher.find_matches(
                    segments,
                    podcast_id=podcast_id,
                    network_id=network_id
                )

                tp_added = 0
                for match in text_matches:
                    # Skip if already covered by fingerprint match
                    if self._is_region_covered(match.start, match.end, pattern_matched_regions):
                        continue

                    # Skip if this region was previously marked as false positive
                    if self._is_region_covered(match.start, match.end, [(fp['start'], fp['end']) for fp in false_positive_regions]):
                        logger.debug(f"[{slug}:{episode_id}] Skipping text pattern match {match.start:.1f}s-{match.end:.1f}s (false positive)")
                        continue

                    # Build reason with pattern reference
                    if match.sponsor:
                        reason = f"{match.sponsor} (pattern #{match.pattern_id})"
                    else:
                        reason = f"Pattern #{match.pattern_id} ({match.match_type})"

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': reason,
                        'sponsor': match.sponsor,
                        'detection_stage': 'text_pattern',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append((match.start, match.end))
                    tp_added += 1

                    # Record pattern match for metrics and promotion
                    if self.pattern_service and match.pattern_id:
                        self.pattern_service.record_pattern_match(match.pattern_id, episode_id)

                detection_stats['text_pattern_matches'] = tp_added
                if text_matches:
                    logger.info(f"[{slug}:{episode_id}] Text pattern stage found {len(text_matches)} ads")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Text pattern matching failed: {e}")

        # Stage 3: Claude API for remaining content
        logger.info(f"[{slug}:{episode_id}] Stage 3: Claude API detection")

        # If we found pattern matches, we can potentially skip Claude for covered regions
        # For now, we still run Claude on full transcript but mark pattern-detected regions
        result = self.detect_ads(
            segments, podcast_name, episode_title, slug, episode_id, episode_description,
            podcast_description=podcast_description,
            progress_callback=progress_callback,
            audio_analysis=audio_analysis
        )

        if result is None:
            result = {"ads": [], "status": "failed", "error": "Detection failed", "retryable": True}

        # Merge Claude detections with pattern matches
        claude_ads = result.get('ads', [])
        cross_episode_skipped = 0
        for ad in claude_ads:
            uncovered_portions = get_uncovered_portions(ad, pattern_matched_regions)

            if not uncovered_portions:
                logger.debug(f"[{slug}:{episode_id}] Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s "
                             f"fully covered by patterns")
                continue

            # Log if ad was trimmed (not returned as-is)
            if not (len(uncovered_portions) == 1
                    and uncovered_portions[0]['start'] == ad['start']
                    and uncovered_portions[0]['end'] == ad['end']):
                for portion in uncovered_portions:
                    logger.info(f"[{slug}:{episode_id}] Preserved uncovered portion: "
                                f"{portion['start']:.1f}s-{portion['end']:.1f}s "
                                f"(from Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s)")

            for portion in uncovered_portions:
                # Existing false positive check (applied per-portion now)
                if false_positive_texts and self.text_pattern_matcher:
                    ad_text = self._get_segment_text(segments, portion['start'], portion['end'])
                    if ad_text and len(ad_text) >= 50:
                        is_fp, similarity = self.text_pattern_matcher.matches_false_positive(
                            ad_text, false_positive_texts
                        )
                        if is_fp:
                            logger.info(f"[{slug}:{episode_id}] Skipping portion "
                                        f"{portion['start']:.1f}s-{portion['end']:.1f}s "
                                        f"(cross-episode false positive, similarity={similarity:.2f})")
                            cross_episode_skipped += 1
                            continue

                portion['detection_stage'] = 'claude'
                all_ads.append(portion)

        if cross_episode_skipped > 0:
            logger.info(f"[{slug}:{episode_id}] Skipped {cross_episode_skipped} detections due to cross-episode false positives")

        detection_stats['claude_matches'] = len([a for a in all_ads if a.get('detection_stage') == 'claude'])

        # Sort by start time
        all_ads.sort(key=lambda x: x['start'])

        # Merge overlapping ads
        all_ads = self._merge_detection_results(all_ads)

        # Log detection summary
        total = len(all_ads)
        fp_count = detection_stats['fingerprint_matches']
        tp_count = detection_stats['text_pattern_matches']
        cl_count = detection_stats['claude_matches']
        logger.info(
            f"[{slug}:{episode_id}] Detection complete: {total} ads "
            f"(fingerprint: {fp_count}, text: {tp_count}, claude: {cl_count})"
        )

        # Pattern learning moved to main.py (after validation sets was_cut)

        result['ads'] = all_ads
        result['detection_stats'] = detection_stats
        return result

    def _is_region_covered(self, start: float, end: float,
                           covered_regions: List[tuple]) -> bool:
        """Check if a time region is substantially covered by existing detections."""
        for cov_start, cov_end in covered_regions:
            # Check for significant overlap (>50%)
            overlap_start = max(start, cov_start)
            overlap_end = min(end, cov_end)
            overlap = max(0, overlap_end - overlap_start)

            duration = end - start
            if duration > 0 and overlap / duration > 0.5:
                return True
        return False

    def _get_segment_text(self, segments: List[Dict], start: float, end: float) -> str:
        """Extract transcript text within a time range."""
        text_parts = []
        for seg in segments:
            # Include segment if it overlaps with the requested range
            if seg.get('end', 0) >= start and seg.get('start', 0) <= end:
                text_parts.append(seg.get('text', ''))
        return ' '.join(text_parts).strip()

    # Reuse centralized constant (superset of the old INVALID_SPONSOR_REASONS)
    INVALID_SPONSOR_REASONS = INVALID_SPONSOR_VALUES

    def _extract_sponsor_from_reason(self, reason: str) -> Optional[str]:
        """Extract sponsor name from ad detection reason using known sponsors DB.

        Args:
            reason: Ad detection reason text (e.g., "ZipRecruiter host-read sponsor segment")

        Returns:
            Extracted sponsor name (normalized) or None
        """
        if not reason or not self.sponsor_service:
            return None

        # Reject garbage reason values before extraction
        reason_lower = reason.lower().strip()
        if reason_lower in self.INVALID_SPONSOR_REASONS or len(reason_lower) < 2:
            logger.debug(f"Rejecting invalid reason for sponsor extraction: '{reason}'")
            return None

        # Use sponsor service to find canonical sponsor name from DB
        sponsor = self.sponsor_service.find_sponsor_in_text(reason)
        if sponsor:
            # Validate extracted sponsor
            sponsor_lower = sponsor.lower().strip()
            if sponsor_lower in self.INVALID_SPONSOR_REASONS or len(sponsor_lower) < 2:
                logger.debug(f"Rejecting invalid extracted sponsor: '{sponsor}'")
                return None
            return sponsor
        return None

    def _learn_from_detections(
        self, ads: List[Dict], segments: List[Dict], podcast_id: str,
        episode_id: str = None, audio_path: str = None
    ) -> int:
        """Create patterns from high-confidence Claude detections.

        This enables automatic pattern learning so the system improves over time.
        Only learns from Claude detections with high confidence and sponsor info.

        Args:
            ads: List of detected ads with confidence and detection_stage
            segments: Transcript segments for text extraction
            podcast_id: Podcast slug for scoping patterns
            episode_id: Episode ID for tracking pattern origin
            audio_path: Path to audio file for fingerprint storage

        Returns:
            Number of patterns created
        """
        if not self.text_pattern_matcher:
            return 0

        patterns_created = 0
        min_confidence = 0.85  # Only learn from high-confidence detections

        for ad in ads:
            # Only learn from ads that were actually removed
            if not ad.get('was_cut', False):
                logger.debug(f"Skipping pattern for uncut ad: {ad['start']:.1f}s-{ad['end']:.1f}s")
                continue

            # Only learn from Claude detections (not fingerprint/text pattern)
            if ad.get('detection_stage') != 'claude':
                continue

            # Require high confidence
            confidence = ad.get('confidence', 0)
            if confidence < min_confidence:
                continue

            # For longer detections, require higher confidence to avoid learning
            # from merged multi-ad spans which contaminate patterns
            duration = ad['end'] - ad['start']
            if duration > 90:  # > 90 seconds
                if confidence < 0.92:  # Require very high confidence for long ads
                    logger.debug(
                        f"Skipping pattern for long ad ({duration:.0f}s) with "
                        f"confidence {confidence:.2f} (threshold 0.92 for >90s ads)"
                    )
                    continue

            # 4-tier sponsor resolution
            sponsor = None
            raw_sponsor = ad.get('sponsor')
            reason_text = ad.get('reason', '')

            # Tier 1: sponsor DB lookup on raw sponsor field
            if raw_sponsor and self.sponsor_service:
                sponsor = self.sponsor_service.find_sponsor_in_text(raw_sponsor)

            # Tier 2: sponsor DB lookup on reason text
            if not sponsor and reason_text and self.sponsor_service:
                sponsor = self.sponsor_service.find_sponsor_in_text(reason_text)

            # Tier 3: extract from reason via regex patterns
            if not sponsor:
                sponsor = self._extract_sponsor_from_reason(reason_text)

            # Tier 4: use raw sponsor if it looks valid
            if not sponsor and raw_sponsor:
                raw_lower = raw_sponsor.lower().strip()
                if raw_lower not in INVALID_SPONSOR_VALUES and len(raw_lower) >= 2:
                    sponsor = raw_sponsor

            if not sponsor:
                continue

            # Gate A: reject sponsors that are strict prefixes of known sponsors
            if self.sponsor_service:
                sponsor_lower = sponsor.lower()
                all_sponsors = self.sponsor_service.get_sponsors()
                is_prefix = False
                for s in all_sponsors:
                    known = s['name'].lower()
                    if known != sponsor_lower and known.startswith(sponsor_lower + ' '):
                        logger.info(f"Skipping pattern: '{sponsor}' is prefix of '{s['name']}'")
                        is_prefix = True
                        break
                if is_prefix:
                    continue

            # Gate B: reject single short words for unknown sponsors
            if self.sponsor_service and not self.sponsor_service.find_sponsor_in_text(sponsor):
                words = sponsor.strip().split()
                if len(words) == 1 and len(sponsor.strip()) < 6:
                    logger.info(f"Skipping pattern for unknown short sponsor: '{sponsor}'")
                    continue

            try:
                pattern_id = self.text_pattern_matcher.create_pattern_from_ad(
                    segments=segments,
                    start=ad['start'],
                    end=ad['end'],
                    sponsor=sponsor,
                    scope='podcast',
                    podcast_id=podcast_id,
                    episode_id=episode_id
                )

                if pattern_id:
                    patterns_created += 1
                    logger.info(
                        f"Created pattern {pattern_id} from Claude detection: "
                        f"{ad['start']:.1f}s-{ad['end']:.1f}s, sponsor={sponsor}"
                    )

                    # Store audio fingerprint alongside the text pattern
                    if audio_path and self.audio_fingerprinter and self.audio_fingerprinter.is_available():
                        try:
                            self.audio_fingerprinter.store_fingerprint(
                                pattern_id=pattern_id,
                                audio_path=audio_path,
                                start=ad['start'],
                                end=ad['end']
                            )
                        except Exception as fp_e:
                            logger.debug(f"Could not store fingerprint for pattern {pattern_id}: {fp_e}")
            except Exception as e:
                logger.warning(f"Failed to create pattern from detection: {e}")

        if patterns_created > 0:
            logger.info(f"Learned {patterns_created} new patterns from detections")

        return patterns_created

    def _detect_foreign_language_ads(
        self, segments: List[Dict], slug: str = None, episode_id: str = None
    ) -> List[Dict]:
        """Auto-detect non-English segments as ads (DAI in other languages).

        Non-English segments (Spanish, etc.) are almost always dynamically inserted
        ads from ad networks targeting specific demographics. These should be
        automatically flagged as ads.

        Args:
            segments: Transcript segments with optional is_foreign_language flag
            slug: Podcast slug for logging
            episode_id: Episode ID for logging

        Returns:
            List of ad markers for foreign language segments
        """
        foreign_ads = []

        # Find consecutive foreign language segments and merge them
        current_ad_start = None
        current_ad_end = None

        for seg in segments:
            if seg.get('is_foreign_language'):
                if current_ad_start is None:
                    # Start new foreign language region
                    current_ad_start = seg['start']
                # Extend region
                current_ad_end = seg['end']
            else:
                # Not foreign language - close any open region
                if current_ad_start is not None:
                    duration = current_ad_end - current_ad_start
                    # Only flag regions longer than 5 seconds
                    if duration >= 5.0:
                        foreign_ads.append({
                            'start': current_ad_start,
                            'end': current_ad_end,
                            'confidence': 0.95,  # High confidence for language detection
                            'reason': 'Non-English language segment (likely DAI ad)',
                            'detection_stage': 'language',
                            'end_text': '[Foreign language content]'
                        })
                        logger.info(
                            f"[{slug}:{episode_id}] Foreign language ad: "
                            f"{current_ad_start:.1f}s-{current_ad_end:.1f}s ({duration:.1f}s)"
                        )
                    current_ad_start = None
                    current_ad_end = None

        # Close final region if needed
        if current_ad_start is not None:
            duration = current_ad_end - current_ad_start
            if duration >= 5.0:
                foreign_ads.append({
                    'start': current_ad_start,
                    'end': current_ad_end,
                    'confidence': 0.95,
                    'reason': 'Non-English language segment (likely DAI ad)',
                    'detection_stage': 'language',
                    'end_text': '[Foreign language content]'
                })

        return foreign_ads

    def _merge_detection_results(self, ads: List[Dict]) -> List[Dict]:
        """Merge overlapping ads from different detection stages."""
        if not ads:
            return []

        # Sort by start time
        ads = sorted(ads, key=lambda x: x['start'])

        merged = [ads[0].copy()]
        for current in ads[1:]:
            last = merged[-1]

            # Check for overlap (within 3 seconds)
            if current['start'] <= last['end'] + 3.0:
                # Merge - prefer pattern-detected metadata
                if current['end'] > last['end']:
                    last['end'] = current['end']

                # Keep higher confidence
                if current.get('confidence', 0) > last.get('confidence', 0):
                    last['confidence'] = current['confidence']

                # Prefer pattern detection stage over claude
                stage_priority = {'fingerprint': 0, 'text_pattern': 1, 'claude': 2}
                if stage_priority.get(current.get('detection_stage'), 2) < stage_priority.get(last.get('detection_stage'), 2):
                    last['detection_stage'] = current['detection_stage']
                    last['pattern_id'] = current.get('pattern_id')
                    if current.get('sponsor'):
                        last['sponsor'] = current['sponsor']
                # Prefer the more descriptive reason
                current_reason = current.get('reason', '')
                last_reason = last.get('reason', '')
                if len(current_reason) > len(last_reason):
                    last['reason'] = current_reason
            else:
                merged.append(current.copy())

        return merged

    def run_verification_detection(self, segments: List[Dict],
                                    podcast_name: str = "Unknown",
                                    episode_title: str = "Unknown",
                                    slug: str = None, episode_id: str = None,
                                    episode_description: str = None,
                                    podcast_description: str = None,
                                    progress_callback=None,
                                    audio_analysis=None) -> Dict:
        """Run ad detection with the verification prompt on processed audio.

        Uses the same sliding window approach as detect_ads() but with the
        verification system prompt and verification model setting.

        Args:
            segments: Transcript segments from re-transcribed processed audio
            podcast_name: Name of podcast
            episode_title: Title of episode
            slug: Podcast slug
            episode_id: Episode ID
            episode_description: Episode description
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress
        """
        if not self.api_key:
            logger.warning("Skipping verification detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            windows = create_windows(segments)
            total_duration = segments[-1]['end'] if segments else 0

            logger.info(f"[{slug}:{episode_id}] Verification: Processing {len(windows)} windows "
                       f"for {total_duration/60:.1f}min processed audio")

            system_prompt = self.get_verification_prompt()
            model = self.get_verification_model()

            logger.info(f"[{slug}:{episode_id}] Verification using model: {model}")

            # Prepare description section
            description_section = ""
            if podcast_description:
                description_section = f"Podcast Description:\n{podcast_description}\n\n"
            if episode_description:
                description_section += (
                    f"Episode Description (this describes the actual content topics discussed; "
                    f"it may also list episode sponsors):\n{episode_description}\n"
                )

            sponsor_history = self._get_podcast_sponsor_history(slug)
            if sponsor_history:
                description_section += sponsor_history

            all_window_ads = []
            all_raw_responses = []
            max_retries = RETRY_CONFIG['max_retries']

            # Instantiate audio signal formatter if audio analysis available
            audio_enforcer = None
            if audio_analysis:
                from audio_enforcer import AudioEnforcer
                audio_enforcer = AudioEnforcer()

            for i, window in enumerate(windows):
                if progress_callback:
                    progress = 85 + int((i / max(len(windows), 1)) * 10)
                    progress_callback(f"detecting:{i+1}/{len(windows)}", progress)

                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                transcript_lines = []
                for seg in window_segments:
                    transcript_lines.append(f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")
                transcript = "\n".join(transcript_lines)

                # Add audio context if available for this window
                audio_context = ""
                if audio_enforcer:
                    audio_context = audio_enforcer.format_for_window(
                        audio_analysis, window_start, window_end
                    )

                window_context = f"""

=== WINDOW {i+1}/{len(windows)}: {window_start/60:.1f}-{window_end/60:.1f} minutes ===
- Use absolute timestamps from transcript (as shown in brackets)
- If an ad starts before this window, use the first timestamp with note "continues from previous"
- If an ad extends past this window, use {window_end:.1f} with note "continues in next"
"""

                prompt = USER_PROMPT_TEMPLATE.format(
                    podcast_name=podcast_name,
                    episode_title=episode_title,
                    description_section=description_section,
                    transcript=transcript
                ) + audio_context + window_context

                logger.info(f"[{slug}:{episode_id}] Verification Window {i+1}/{len(windows)}: "
                           f"{window_start/60:.1f}-{window_end/60:.1f}min")

                response = None
                for attempt in range(max_retries + 1):
                    try:
                        response = self._llm_client.messages_create(
                            model=model,
                            max_tokens=2000,
                            temperature=0.0,
                            system=system_prompt,
                            messages=[{"role": "user", "content": prompt}],
                            timeout=120.0,
                            response_format={"type": "json_object"}
                        )
                        break
                    except Exception as e:
                        if self._is_retryable_error(e) and attempt < max_retries:
                            if is_rate_limit_error(e):
                                delay = 60.0
                            else:
                                delay = self._calculate_backoff(attempt)
                            logger.warning(f"[{slug}:{episode_id}] Verification Window {i+1} "
                                         f"API error: {e}. Retrying in {delay:.1f}s")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"[{slug}:{episode_id}] Verification Window {i+1} failed: {e}")
                            return {
                                "ads": [],
                                "status": "failed",
                                "error": str(e),
                                "retryable": self._is_retryable_error(e)
                            }

                if response is None:
                    logger.error(f"[{slug}:{episode_id}] Verification Window {i+1} - no response after retries")
                    continue

                response_text = response.content
                all_raw_responses.append(
                    f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}"
                )

                window_ads = self._parse_ads_from_response(response_text, slug, episode_id)

                # Filter to window bounds
                valid_window_ads = []
                for ad in window_ads:
                    duration = ad['end'] - ad['start']
                    in_window = (ad['start'] >= window_start - MIN_OVERLAP_TOLERANCE and
                                 ad['start'] <= window_end + MIN_OVERLAP_TOLERANCE)
                    reasonable_length = duration <= MAX_AD_DURATION_WINDOW

                    if in_window and reasonable_length:
                        valid_window_ads.append(ad)
                    else:
                        logger.warning(
                            f"[{slug}:{episode_id}] Verification Window {i+1} rejected ad: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({duration:.0f}s)"
                        )

                for ad in valid_window_ads:
                    ad['detection_stage'] = 'verification'

                logger.info(f"[{slug}:{episode_id}] Verification Window {i+1} found {len(valid_window_ads)} ads")
                all_window_ads.extend(valid_window_ads)

            final_ads = deduplicate_window_ads(all_window_ads)

            for ad in final_ads:
                ad['detection_stage'] = 'verification'

            if final_ads:
                total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
                logger.info(f"[{slug}:{episode_id}] Verification total: {len(final_ads)} ads "
                           f"({total_ad_time/60:.1f} min)")
            else:
                logger.info(f"[{slug}:{episode_id}] Verification: No additional ads found")

            return {
                "ads": final_ads,
                "status": "success",
                "raw_response": "\n\n".join(all_raw_responses),
                "prompt": f"Verification: Processed {len(windows)} windows",
                "model": model
            }

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Verification detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}


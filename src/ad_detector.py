"""Ad detection using Claude API with configurable prompts and model."""
import logging
import json
import os
import re
import time
import random
import hashlib
from typing import List, Dict, Optional
from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError, InternalServerError

from config import (
    MIN_TYPICAL_AD_DURATION, MIN_SPONSOR_READ_DURATION, SHORT_GAP_THRESHOLD,
    MAX_MERGED_DURATION, MAX_REALISTIC_SIGNAL, MIN_OVERLAP_TOLERANCE,
    MAX_AD_DURATION_WINDOW
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

# Sliding window configuration for ad detection
# Windows overlap to ensure ads at chunk boundaries are not missed
WINDOW_SIZE_SECONDS = 600.0   # 10 minutes per window
WINDOW_OVERLAP_SECONDS = 180.0  # 3 minutes overlap between windows
WINDOW_STEP_SECONDS = WINDOW_SIZE_SECONDS - WINDOW_OVERLAP_SECONDS  # 7 minutes

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

def merge_and_deduplicate(first_pass: List[Dict], second_pass: List[Dict]) -> List[Dict]:
    """Merge ads from both passes, combining overlapping segments.

    Strategy:
    - If segments overlap: merge them (earliest start, latest end)
    - If no overlap: keep both
    - Preserves the longer/merged segment's metadata

    Args:
        first_pass: List of ad segments from first pass
        second_pass: List of ad segments from second pass

    Returns:
        Merged and sorted list of ad segments
    """
    # Mark passes
    for ad in first_pass:
        if 'pass' not in ad:
            ad['pass'] = 1
    for ad in second_pass:
        if 'pass' not in ad:
            ad['pass'] = 2

    # Combine all ads into one list
    all_ads = list(first_pass) + list(second_pass)

    if not all_ads:
        return []

    # Sort by start time
    all_ads.sort(key=lambda x: x['start'])

    # Merge overlapping segments
    merged = [all_ads[0].copy()]

    for current in all_ads[1:]:
        last = merged[-1]

        # Check if current overlaps with last (or is adjacent within 2 seconds)
        if current['start'] <= last['end'] + 2.0:
            # Merge: extend end time if current goes further
            if current['end'] > last['end']:
                original_end = last['end']
                last['end'] = current['end']
                # Update end_text from the segment that defines the new end
                if current.get('end_text'):
                    last['end_text'] = current['end_text']
                logger.info(f"Merged overlapping ads: {last['start']:.1f}s-{original_end:.1f}s + {current['start']:.1f}s-{current['end']:.1f}s -> {last['start']:.1f}s-{last['end']:.1f}s")

            # Keep higher confidence
            if current.get('confidence', 0) > last.get('confidence', 0):
                last['confidence'] = current['confidence']

            # Mark as merged from both passes if different
            if current.get('pass') != last.get('pass'):
                last['pass'] = 'merged'
        else:
            # No overlap - add as new segment
            merged.append(current.copy())
            if current.get('pass') == 2:
                logger.info(f"Second pass found new ad: {current['start']:.1f}s - {current['end']:.1f}s ({current.get('reason', 'unknown')})")

    # Validate ad durations and extend short ads that likely ended too early
    # Constants imported from config.py: MIN_TYPICAL_AD_DURATION, MIN_SPONSOR_READ_DURATION
    URL_EXTENSION_SECONDS = 45.0  # Extension when URL detected in end_text

    for ad in merged:
        duration = ad['end'] - ad['start']
        end_text = ad.get('end_text', '').lower()

        # Check if likely incomplete - short duration with URL in end_text
        has_url = '.com' in end_text or '.tv' in end_text or 'http' in end_text

        if duration < MIN_TYPICAL_AD_DURATION:
            logger.warning(
                f"Short ad detected ({duration:.1f}s): {ad['start']:.1f}s - {ad['end']:.1f}s - "
                f"may have incomplete end time. Reason: {ad.get('reason', 'unknown')}"
            )

        # Extend ads that are suspiciously short and ended on a URL
        if duration < MIN_SPONSOR_READ_DURATION and has_url:
            original_end = ad['end']
            ad['end'] += URL_EXTENSION_SECONDS
            ad['extended'] = True
            logger.info(
                f"Extended short ad with URL in end_text: {ad['start']:.1f}s-{original_end:.1f}s -> "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s (+{URL_EXTENSION_SECONDS:.0f}s)"
            )

    return merged


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

        # Build text from words for phrase matching
        word_texts = [w.get('word', '').strip().lower() for w in words]
        full_text = ' '.join(word_texts)

        matches = []
        for phrase in phrases:
            phrase_lower = phrase.lower()
            # Find phrase in the concatenated text
            idx = full_text.find(phrase_lower)
            if idx >= 0:
                # Map character position back to word index
                char_count = 0
                start_word_idx = 0
                for i, wt in enumerate(word_texts):
                    if char_count >= idx:
                        start_word_idx = i
                        break
                    char_count += len(wt) + 1  # +1 for space

                # Find end word index
                phrase_words = phrase_lower.split()
                end_word_idx = min(start_word_idx + len(phrase_words) - 1, len(words) - 1)

                matches.append({
                    'start': words[start_word_idx].get('start', 0),
                    'end': words[end_word_idx].get('end', 0),
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
                last['reason'] = current.get('reason', last.get('reason', ''))
            # Mark as merged from windows
            last['merged_windows'] = True
        else:
            merged.append(current.copy())

    if len(merged) < len(all_ads):
        logger.info(f"Window deduplication: {len(all_ads)} -> {len(merged)} ads")

    return merged


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
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("No Anthropic API key found")
        self.client = None
        self._db = None
        self._audio_fingerprinter = None
        self._text_pattern_matcher = None

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

    def initialize_client(self):
        """Initialize Anthropic client."""
        if self.client is None and self.api_key:
            try:
                self.client = Anthropic(api_key=self.api_key)
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
                raise

    def get_available_models(self) -> List[Dict]:
        """Get list of available Claude models from API."""
        try:
            self.initialize_client()
            if not self.client:
                return []

            # Anthropic API models endpoint
            response = self.client.models.list()
            models = []
            for model in response.data:
                # Filter to only include claude models suitable for this task
                if 'claude' in model.id.lower():
                    models.append({
                        'id': model.id,
                        'name': model.display_name if hasattr(model, 'display_name') else model.id,
                        'created': model.created if hasattr(model, 'created') else None
                    })
            return models
        except Exception as e:
            logger.warning(f"Could not fetch models from API: {e}")
            # Return known models as fallback
            return [
                {'id': 'claude-sonnet-4-5-20250929', 'name': 'Claude Sonnet 4.5'},
                {'id': 'claude-opus-4-5-20251101', 'name': 'Claude Opus 4.5'},
                {'id': 'claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4'},
                {'id': 'claude-opus-4-1-20250414', 'name': 'Claude Opus 4.1'},
                {'id': 'claude-3-5-sonnet-20241022', 'name': 'Claude 3.5 Sonnet'},
            ]

    def get_model(self) -> str:
        """Get configured model from database or default."""
        try:
            model = self.db.get_setting('claude_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not load model from DB: {e}")
        return DEFAULT_MODEL

    def get_second_pass_model(self) -> str:
        """Get configured second pass model from database or default."""
        try:
            model = self.db.get_setting('second_pass_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not load second pass model from DB: {e}")
        return DEFAULT_MODEL

    def _format_audio_context(self, audio_analysis, window_start: float, window_end: float) -> str:
        """Format audio analysis signals for a specific window as prompt context.

        Args:
            audio_analysis: AudioAnalysisResult object
            window_start: Start time of current window in seconds
            window_end: End time of current window in seconds

        Returns:
            Formatted string to include in Claude prompt
        """
        if not audio_analysis or not audio_analysis.signals:
            return ""

        # MAX_REALISTIC_SIGNAL imported from config.py

        # Get signals that overlap with this window AND are realistic length
        window_signals = [
            s for s in audio_analysis.signals
            if s.start < window_end and s.end > window_start
            and (s.end - s.start) <= MAX_REALISTIC_SIGNAL
        ]

        if not window_signals:
            return ""

        lines = []
        lines.append("\n" + "=" * 50)
        lines.append("AUDIO ANALYSIS SIGNALS (supplementary context)")
        lines.append("=" * 50)

        # Conversation type
        if audio_analysis.conversation_metrics:
            metrics = audio_analysis.conversation_metrics
            if metrics.is_conversational:
                lines.append(f"Episode Type: CONVERSATIONAL ({metrics.num_speakers} speakers)")
            else:
                lines.append(f"Episode Type: SOLO/INTERVIEW ({metrics.num_speakers} speakers)")

        # Volume changes
        volume_signals = [
            s for s in window_signals
            if 'volume' in s.signal_type.lower()
        ]
        if volume_signals:
            lines.append("\nVolume Changes:")
            for s in volume_signals:
                direction = "+" if "increase" in s.signal_type else "-"
                deviation = s.details.get('deviation_db', 0)
                time_str = self._format_time(s.start)
                lines.append(f"  [{time_str}] {direction}{deviation:.1f}dB (confidence: {s.confidence:.0%})")

        # Music beds
        music_signals = [
            s for s in window_signals
            if s.signal_type == 'music_bed'
        ]
        if music_signals:
            lines.append("\nMusic Beds Detected:")
            for s in music_signals:
                start_str = self._format_time(s.start)
                end_str = self._format_time(s.end)
                lines.append(f"  [{start_str} - {end_str}] (confidence: {s.confidence:.0%})")

        # Monologues
        mono_signals = [
            s for s in window_signals
            if s.signal_type == 'monologue'
        ]
        if mono_signals:
            lines.append("\nExtended Monologues (potential ad reads):")
            for s in mono_signals:
                start_str = self._format_time(s.start)
                end_str = self._format_time(s.end)
                speaker = s.details.get('speaker', 'unknown')
                is_host = s.details.get('is_host', False)
                has_ad_lang = s.details.get('has_ad_language', False)
                notes = []
                if is_host:
                    notes.append("HOST")
                if has_ad_lang:
                    notes.append("AD LANGUAGE")
                note_str = f" [{', '.join(notes)}]" if notes else ""
                lines.append(f"  [{start_str} - {end_str}] {s.duration:.0f}s by {speaker}{note_str}")

        lines.append("-" * 50)
        lines.append("NOTE: Correlate these signals with transcript content.")
        lines.append("")

        return '\n'.join(lines)

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"

    def get_system_prompt(self) -> str:
        """Get system prompt from database or default."""
        try:
            prompt = self.db.get_setting('system_prompt')
            if prompt:
                return prompt
        except Exception as e:
            logger.warning(f"Could not load system prompt from DB: {e}")

        # Default fallback
        from database import DEFAULT_SYSTEM_PROMPT
        return DEFAULT_SYSTEM_PROMPT

    def get_second_pass_prompt(self) -> str:
        """Get second pass prompt from database or default."""
        try:
            prompt = self.db.get_setting('second_pass_prompt')
            if prompt:
                return prompt
        except Exception as e:
            logger.warning(f"Could not load second pass prompt from DB: {e}")

        # Default fallback - import from database module
        from database import DEFAULT_SECOND_PASS_PROMPT
        return DEFAULT_SECOND_PASS_PROMPT

    def get_user_prompt_template(self) -> str:
        """Get user prompt template (hardcoded, not configurable)."""
        return USER_PROMPT_TEMPLATE

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried."""
        # Rate limit and connection errors are retryable
        if isinstance(error, (APIConnectionError, RateLimitError)):
            return True
        # Internal server errors (500, 503, 529 overloaded) are retryable
        if isinstance(error, InternalServerError):
            return True
        # Check for specific status codes in generic APIError
        if isinstance(error, APIError):
            status = getattr(error, 'status_code', None)
            if status in (429, 500, 502, 503, 529):
                return True
        return False

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
        try:
            ads = None

            # Strategy 1: Try to extract from markdown code block first
            code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
            if code_block_match:
                try:
                    ads = json.loads(code_block_match.group(1))
                    logger.debug(f"[{slug}:{episode_id}] Extracted JSON from code block")
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
                    logger.debug(f"[{slug}:{episode_id}] Found valid JSON array in response")

            # Strategy 3: Fallback to original first-to-last bracket logic
            if ads is None:
                clean_response = re.sub(r'```json\s*', '', response_text)
                clean_response = re.sub(r'```\s*', '', clean_response)

                start_idx = clean_response.find('[')
                end_idx = clean_response.rfind(']') + 1

                if start_idx >= 0 and end_idx > start_idx:
                    json_str = clean_response[start_idx:end_idx]
                    ads = json.loads(json_str)

            if ads is None or not isinstance(ads, list):
                logger.warning(f"[{slug}:{episode_id}] No valid JSON array found in response")
                return []

            # Validate and normalize ads
            valid_ads = []
            for ad in ads:
                if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                    start = float(ad['start'])
                    end = float(ad['end'])
                    if end > start:  # Skip invalid segments
                        valid_ads.append({
                            'start': start,
                            'end': end,
                            'confidence': float(ad.get('confidence', 1.0)),
                            'reason': ad.get('reason', 'Advertisement detected'),
                            'end_text': ad.get('end_text', '')
                        })

            return valid_ads

        except json.JSONDecodeError as e:
            logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
            return []

    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None, episode_description: str = None,
                   audio_analysis=None) -> Optional[Dict]:
        """Detect ad segments using Claude API with sliding window approach.

        Processes transcript in overlapping windows to ensure ads at chunk
        boundaries are not missed. Windows are 10 minutes with 3 minute overlap.

        Args:
            audio_analysis: Optional AudioAnalysisResult with audio signals
        """
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

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
            if episode_description:
                description_section = f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Including episode description ({len(episode_description)} chars)")

            all_window_ads = []
            all_raw_responses = []
            max_retries = RETRY_CONFIG['max_retries']

            # Process each window
            for i, window in enumerate(windows):
                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                # Build transcript for this window (segments have absolute timestamps)
                transcript_lines = []
                for seg in window_segments:
                    transcript_lines.append(f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")
                transcript = "\n".join(transcript_lines)

                # Add window context to prompt
                window_context = f"""

=== WINDOW {i+1}/{len(windows)}: {window_start/60:.1f}-{window_end/60:.1f} minutes ===
- Use absolute timestamps from transcript (as shown in brackets)
- If an ad starts before this window, use the first timestamp with note "continues from previous"
- If an ad extends past this window, use {window_end:.1f} with note "continues in next"
"""

                # Format audio analysis context for this window
                audio_context = ""
                if audio_analysis and hasattr(audio_analysis, 'signals') and audio_analysis.signals:
                    audio_context = self._format_audio_context(audio_analysis, window_start, window_end)

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
                        response = self.client.messages.create(
                            model=model,
                            max_tokens=2000,
                            temperature=0.0,
                            system=system_prompt,
                            messages=[{"role": "user", "content": prompt}],
                            timeout=120.0  # 2 minute timeout
                        )
                        break
                    except Exception as e:
                        last_error = e
                        if self._is_retryable_error(e) and attempt < max_retries:
                            if isinstance(e, RateLimitError):
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

                # Parse response
                response_text = response.content[0].text if response.content else ""
                all_raw_responses.append(f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}")

                # Parse ads from response
                window_ads = self._parse_ads_from_response(response_text, slug, episode_id)

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

            total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
            logger.info(f"[{slug}:{episode_id}] Total after dedup: {len(final_ads)} ads ({total_ad_time/60:.1f} min)")

            for ad in final_ads:
                logger.info(f"[{slug}:{episode_id}] Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                           f"({ad['end']-ad['start']:.0f}s) end_text='{ad.get('end_text', '')[:50]}'")

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
                          audio_analysis=None, audio_path: str = None,
                          podcast_id: str = None, network_id: str = None,
                          skip_patterns: bool = False) -> Dict:
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
            audio_analysis: Audio analysis results
            audio_path: Path to audio file for fingerprinting
            podcast_id: Podcast ID for pattern scoping
            network_id: Network ID for pattern scoping
            skip_patterns: If True, skip stages 1 & 2 (pattern DB), go directly to Claude

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

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': f"Audio fingerprint match (pattern {match.pattern_id})",
                        'sponsor': match.sponsor,
                        'detection_stage': 'fingerprint',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append((match.start, match.end))
                    fp_added += 1

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

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': f"Text pattern match ({match.match_type}, pattern {match.pattern_id})",
                        'sponsor': match.sponsor,
                        'detection_stage': 'text_pattern',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append((match.start, match.end))
                    tp_added += 1

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
            audio_analysis=audio_analysis
        )

        if result is None:
            result = {"ads": [], "status": "failed", "error": "Detection failed", "retryable": True}

        # Merge Claude detections with pattern matches
        claude_ads = result.get('ads', [])
        cross_episode_skipped = 0
        for ad in claude_ads:
            # Skip if already detected by pattern matching
            if self._is_region_covered(ad['start'], ad['end'], pattern_matched_regions):
                logger.debug(f"[{slug}:{episode_id}] Skipping Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s (covered by pattern)")
                continue

            # Skip if matches a cross-episode false positive
            if false_positive_texts and self.text_pattern_matcher:
                ad_text = self._get_segment_text(segments, ad['start'], ad['end'])
                if ad_text and len(ad_text) >= 50:
                    is_fp, similarity = self.text_pattern_matcher.matches_false_positive(
                        ad_text, false_positive_texts
                    )
                    if is_fp:
                        logger.info(
                            f"[{slug}:{episode_id}] Skipping Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s "
                            f"(matches cross-episode false positive, similarity={similarity:.2f})"
                        )
                        cross_episode_skipped += 1
                        continue

            ad['detection_stage'] = 'claude'
            all_ads.append(ad)

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
            else:
                merged.append(current.copy())

        return merged

    def is_multi_pass_enabled(self) -> bool:
        """Check if multi-pass detection is enabled in settings."""
        try:
            setting = self.db.get_setting('multi_pass_enabled')
            return setting and setting.lower() in ('true', '1', 'yes')
        except Exception as e:
            logger.warning(f"Could not check multi_pass_enabled setting: {e}")
            return False

    def detect_ads_second_pass(self, segments: List[Dict],
                               podcast_name: str = "Unknown", episode_title: str = "Unknown",
                               slug: str = None, episode_id: str = None,
                               episode_description: str = None,
                               audio_analysis=None,
                               skip_patterns: bool = False) -> Optional[Dict]:
        """Blind second pass ad detection with sliding window approach.

        Focuses on subtle/baked-in ads using a separate model and prompt.
        Uses the same sliding window approach as first pass for consistency.

        Args:
            audio_analysis: Optional AudioAnalysisResult with audio signals
            skip_patterns: Unused in second pass (always Claude-only), kept for API consistency
        """
        if not self.api_key:
            logger.warning("Skipping second pass - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Create overlapping windows from transcript
            windows = create_windows(segments)
            total_duration = segments[-1]['end'] if segments else 0

            logger.info(f"[{slug}:{episode_id}] Second pass: Processing {len(windows)} windows "
                       f"for {total_duration/60:.1f}min episode")

            # Get second pass prompt and model (can be different from first pass)
            system_prompt = self.get_second_pass_prompt()
            model = self.get_second_pass_model()

            logger.info(f"[{slug}:{episode_id}] Second pass using model: {model}")

            # Prepare description section (shared across windows)
            description_section = ""
            if episode_description:
                description_section = f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Second pass: Including episode description ({len(episode_description)} chars)")

            all_window_ads = []
            all_raw_responses = []
            max_retries = RETRY_CONFIG['max_retries']

            # Process each window
            for i, window in enumerate(windows):
                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                # Build transcript for this window
                transcript_lines = []
                for seg in window_segments:
                    transcript_lines.append(f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")
                transcript = "\n".join(transcript_lines)

                # Add window context to prompt
                window_context = f"""

=== WINDOW {i+1}/{len(windows)}: {window_start/60:.1f}-{window_end/60:.1f} minutes ===
- Use absolute timestamps from transcript (as shown in brackets)
- If an ad starts before this window, use the first timestamp with note "continues from previous"
- If an ad extends past this window, use {window_end:.1f} with note "continues in next"
"""

                # Format audio analysis context for this window
                audio_context = ""
                if audio_analysis and hasattr(audio_analysis, 'signals') and audio_analysis.signals:
                    audio_context = self._format_audio_context(audio_analysis, window_start, window_end)

                prompt = USER_PROMPT_TEMPLATE.format(
                    podcast_name=podcast_name,
                    episode_title=episode_title,
                    description_section=description_section,
                    transcript=transcript
                ) + audio_context + window_context

                logger.info(f"[{slug}:{episode_id}] Second pass Window {i+1}/{len(windows)}: "
                           f"{window_start/60:.1f}-{window_end/60:.1f}min")

                # Call Claude API with retry logic
                response = None
                last_error = None

                for attempt in range(max_retries + 1):
                    try:
                        response = self.client.messages.create(
                            model=model,
                            max_tokens=2000,
                            temperature=0.0,
                            system=system_prompt,
                            messages=[{"role": "user", "content": prompt}],
                            timeout=120.0  # 2 minute timeout
                        )
                        break
                    except Exception as e:
                        last_error = e
                        if self._is_retryable_error(e) and attempt < max_retries:
                            if isinstance(e, RateLimitError):
                                delay = 60.0
                                logger.warning(f"[{slug}:{episode_id}] Second pass Window {i+1} rate limit, waiting {delay:.0f}s")
                            else:
                                delay = self._calculate_backoff(attempt)
                                logger.warning(f"[{slug}:{episode_id}] Second pass Window {i+1} API error: {e}. Retrying in {delay:.1f}s")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"[{slug}:{episode_id}] Second pass Window {i+1} failed: {e}")
                            return {
                                "ads": [],
                                "status": "failed",
                                "error": str(e),
                                "retryable": self._is_retryable_error(e)
                            }

                if response is None:
                    logger.error(f"[{slug}:{episode_id}] Second pass Window {i+1} - no response after retries")
                    continue

                # Parse response
                response_text = response.content[0].text if response.content else ""
                all_raw_responses.append(f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}")

                # Parse ads from response
                window_ads = self._parse_ads_from_response(response_text, slug, episode_id)

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
                            f"[{slug}:{episode_id}] Second pass Window {i+1} rejected ad: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({duration:.0f}s) - "
                            f"{'outside window' if not in_window else 'too long'}"
                        )

                window_ads = valid_window_ads

                # Mark all ads as second pass
                for ad in window_ads:
                    ad['pass'] = 2

                logger.info(f"[{slug}:{episode_id}] Second pass Window {i+1} found {len(window_ads)} ads")
                all_window_ads.extend(window_ads)

            # Deduplicate ads across windows
            final_ads = deduplicate_window_ads(all_window_ads)

            # Ensure pass=2 is preserved after dedup
            for ad in final_ads:
                ad['pass'] = 2

            if final_ads:
                total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
                logger.info(f"[{slug}:{episode_id}] Second pass total: {len(final_ads)} ads ({total_ad_time/60:.1f} min)")
                for ad in final_ads:
                    logger.info(f"[{slug}:{episode_id}] Second pass Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                               f"({ad['end']-ad['start']:.0f}s) end_text='{ad.get('end_text', '')[:50]}'")
            else:
                logger.info(f"[{slug}:{episode_id}] Second pass: No additional ads found")

            return {
                "ads": final_ads,
                "status": "success",
                "raw_response": "\n\n".join(all_raw_responses),
                "prompt": f"Second pass: Processed {len(windows)} windows",
                "model": model
            }

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Second pass failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}

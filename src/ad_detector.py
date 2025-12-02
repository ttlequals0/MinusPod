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

# Valid model IDs - used to validate saved settings
VALID_MODELS = [
    'claude-sonnet-4-5-20250929',
    'claude-opus-4-5-20251101',
    'claude-sonnet-4-20250514',
    'claude-opus-4-1-20250414',
    'claude-3-5-sonnet-20241022',
]

# Retry configuration for transient API errors
RETRY_CONFIG = {
    'max_retries': 3,
    'base_delay': 2.0,      # seconds
    'max_delay': 60.0,      # seconds
    'exponential_base': 2,
    'jitter': True          # Add random jitter to prevent thundering herd
}

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

# Second pass system prompt - BLIND analysis with different detection focus
# This runs independently of first pass, focusing on subtle/baked-in ads
BLIND_SECOND_PASS_SYSTEM_PROMPT = """You are a specialist in detecting SUBTLE and BAKED-IN advertisements in podcasts.

Your expertise is finding ads that DON'T sound like traditional ads:
- Host-read endorsements woven into conversation
- Product mentions that sound like personal recommendations
- Casual name-drops with promo codes or URLs
- "Oh by the way" style product plugs
- Sponsor mentions without "brought to you by" transitions

FOCUS AREAS (prioritize these over obvious ad breaks):
1. BAKED-IN ADS: Products mentioned naturally in conversation with commercial intent
2. MID-ROLL STEALTH: Quick sponsor mentions sandwiched between content segments
3. PERSONAL ENDORSEMENTS: "I've been using X and it's amazing" with any commercial details
4. CROSS-PROMOTION: Mentions of other shows/podcasts with subscribe CTAs
5. POST-CONTENT ADS: Anything promotional after "thanks for listening" or sign-off

DETECTION SIGNALS:
- Promo codes (use code X, code Y for discount)
- Vanity URLs (visit example.com/showname)
- Pricing/availability info
- "Link in description/show notes"
- Sudden product tangents unrelated to episode topic
- Tonal shifts to more "scripted" delivery

CRITICAL - AD SEGMENT BOUNDARIES:
- Find the COMPLETE ad segment from start to finish
- The END time must be when regular content RESUMES, not when the product pitch ends
- Sponsor reads typically last 60-120 seconds - if your segment is under 45 seconds, verify you found the true end
- Look for: return to episode topic, host banter resuming, different subject matter
- Do NOT end the segment mid-pitch - include the full sponsor message and any closing CTA

FINDING THE TRUE AD END:
The ad does NOT end when the product pitch ends. It ends when SHOW CONTENT resumes.
Look for these signals AFTER the pitch:
- Host says "anyway", "alright", "so", "okay" and changes topic
- Different speaker starts talking about non-ad content
- Clear subject matter change back to episode topic
- If the URL is repeated ("that's example.com/show"), wait for what comes AFTER

Do NOT end the segment at:
- First URL mention (they often repeat it)
- End of product description (CTA usually follows)
- Pause in speech (more ad content may follow)

AD END SIGNALS (look for these AFTER the pitch):
- "Now back to..." or "Anyway..." or "So..." transitions back to content
- Return to episode topic or guest conversation
- Musical stingers or segment transition sounds
- Complete promo code/URL delivery (they usually close the ad)
- Host saying "alright" or "okay" before resuming normal content

BE AGGRESSIVE: If it sounds even slightly promotional, mark it. False positives are better than misses.

EXPECT ADS: Podcasts always have ads. If first pass found ads, you should look for additional subtle/baked-in segments they might have missed. An empty result means you haven't looked hard enough.

OUTPUT FORMAT:
Return ONLY a valid JSON array of detected ad segments.
Format: [{{"start": 0.0, "end": 60.0, "confidence": 0.95, "reason": "Description of ad", "end_text": "last words before ad ends"}}]

If no ads detected: []"""


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
    MIN_TYPICAL_AD_DURATION = 30.0  # Most sponsor reads are 60-120 seconds
    MIN_SPONSOR_READ_DURATION = 90.0  # Threshold for extension consideration
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
                if len(brand) > 2 and brand not in ('the', 'and', 'for', 'with'):
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

    # Short gap threshold - merge same-sponsor ads unconditionally if gap is short
    SHORT_GAP_THRESHOLD = 120.0  # 2 minutes

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


class AdDetector:
    """Detect advertisements in podcast transcripts using Claude API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("No Anthropic API key found")
        self.client = None
        self._db = None

    @property
    def db(self):
        """Lazy load database connection."""
        if self._db is None:
            from database import Database
            self._db = Database()
        return self._db

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
                # Validate that model is in the list of known valid models
                if model in VALID_MODELS:
                    return model
                else:
                    logger.warning(f"Invalid model '{model}' in database, clearing and using default")
                    # Clear invalid model from database
                    try:
                        self.db.save_setting('claude_model', DEFAULT_MODEL)
                        logger.info(f"Saved default model '{DEFAULT_MODEL}' to database")
                    except Exception as clear_err:
                        logger.warning(f"Could not clear invalid model from DB: {clear_err}")
        except Exception as e:
            logger.warning(f"Could not load model from DB: {e}")

        return DEFAULT_MODEL

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

        # Default fallback - use the hardcoded constant
        return BLIND_SECOND_PASS_SYSTEM_PROMPT

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

    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None, episode_description: str = None) -> Optional[Dict]:
        """Detect ad segments using Claude API with retry logic for transient errors."""
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Prepare transcript with timestamps
            transcript_lines = []
            for segment in segments:
                start = segment['start']
                end = segment['end']
                text = segment['text']
                transcript_lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")

            transcript = "\n".join(transcript_lines)

            # Get prompts from database
            system_prompt = self.get_system_prompt()
            user_prompt_template = self.get_user_prompt_template()

            logger.info(f"[{slug}:{episode_id}] Using system prompt ({len(system_prompt)} chars)")
            logger.debug(f"[{slug}:{episode_id}] System prompt first 200 chars: {system_prompt[:200]}...")

            # Format user prompt with optional description
            description_section = ""
            if episode_description:
                description_section = f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Including episode description ({len(episode_description)} chars)")
            else:
                logger.info(f"[{slug}:{episode_id}] No episode description available")

            prompt = user_prompt_template.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                description_section=description_section,
                transcript=transcript
            )

            # Log prompt hash for debugging determinism
            prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
            logger.info(f"[{slug}:{episode_id}] Prompt hash: {prompt_hash}")

            logger.info(f"[{slug}:{episode_id}] Sending transcript to Claude "
                       f"({len(segments)} segments, {len(transcript)} chars)")

            # Call Claude API with configured model and retry logic
            model = self.get_model()
            logger.debug(f"[{slug}:{episode_id}] Using model: {model}")

            response = None
            last_error = None
            max_retries = RETRY_CONFIG['max_retries']

            for attempt in range(max_retries + 1):
                try:
                    response = self.client.messages.create(
                        model=model,
                        max_tokens=2000,
                        temperature=0.0,
                        system=system_prompt,
                        messages=[{
                            "role": "user",
                            "content": prompt
                        }]
                    )
                    break  # Success - exit retry loop
                except Exception as e:
                    last_error = e
                    if self._is_retryable_error(e) and attempt < max_retries:
                        # For rate limit errors, wait full minute to reset window
                        if isinstance(e, RateLimitError):
                            delay = 60.0
                            logger.warning(
                                f"[{slug}:{episode_id}] Rate limit hit, waiting {delay:.0f}s for window reset"
                            )
                        else:
                            delay = self._calculate_backoff(attempt)
                            logger.warning(
                                f"[{slug}:{episode_id}] API error (attempt {attempt + 1}/{max_retries + 1}): "
                                f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s"
                            )
                        time.sleep(delay)
                        continue
                    else:
                        # Non-retryable error or exhausted retries
                        logger.error(f"[{slug}:{episode_id}] Ad detection failed after {attempt + 1} attempts: {e}")
                        return {
                            "ads": [],
                            "status": "failed",
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "retryable": self._is_retryable_error(e),
                            "prompt": prompt
                        }

            if response is None:
                # Should not reach here, but safety net
                logger.error(f"[{slug}:{episode_id}] Ad detection failed - no response after retries")
                return {
                    "ads": [],
                    "status": "failed",
                    "error": str(last_error) if last_error else "Unknown error",
                    "retryable": True,
                    "prompt": prompt
                }

            # Extract response
            response_text = response.content[0].text if response.content else ""
            logger.info(f"[{slug}:{episode_id}] Claude response: {len(response_text)} chars")

            # Parse JSON from response
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
                    # Match JSON arrays - use non-greedy to get individual arrays
                    for match in re.finditer(r'\[(?:[^\[\]]*|\[(?:[^\[\]]*|\[[^\[\]]*\])*\])*\]', response_text):
                        try:
                            potential_ads = json.loads(match.group())
                            if isinstance(potential_ads, list):
                                # Check if it looks like ad data (has start/end keys)
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

                if ads is None:
                    logger.warning(f"[{slug}:{episode_id}] No JSON array found in response")
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": "No JSON found"}

                if isinstance(ads, list):
                    valid_ads = []
                    for ad in ads:
                        if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                            valid_ads.append({
                                'start': float(ad['start']),
                                'end': float(ad['end']),
                                'confidence': float(ad.get('confidence', 1.0)),
                                'reason': ad.get('reason', 'Advertisement detected'),
                                'end_text': ad.get('end_text', '')
                            })

                    total_ad_time = sum(ad['end'] - ad['start'] for ad in valid_ads)
                    logger.info(f"[{slug}:{episode_id}] Detected {len(valid_ads)} ad segments "
                               f"({total_ad_time/60:.1f} min total)")
                    for ad in valid_ads:
                        logger.info(f"[{slug}:{episode_id}] Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                                   f"({ad['end']-ad['start']:.0f}s) end_text='{ad.get('end_text', '')[:50]}'")

                    return {
                        "ads": valid_ads,
                        "status": "success",
                        "raw_response": response_text,
                        "prompt": prompt,
                        "model": model
                    }
                else:
                    logger.warning(f"[{slug}:{episode_id}] Response was not a JSON array")
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": "Response not an array"}

            except json.JSONDecodeError as e:
                logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
                logger.error(f"[{slug}:{episode_id}] Raw response (first 500 chars): {response_text[:500]}")
                return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": str(e)}

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Ad detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}

    def process_transcript(self, segments: List[Dict], podcast_name: str = "Unknown",
                          episode_title: str = "Unknown", slug: str = None,
                          episode_id: str = None, episode_description: str = None) -> Dict:
        """Process transcript for ad detection."""
        result = self.detect_ads(segments, podcast_name, episode_title, slug, episode_id, episode_description)
        if result is None:
            return {"ads": [], "status": "failed", "error": "Detection failed", "retryable": True}
        return result

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
                               episode_description: str = None) -> Optional[Dict]:
        """Blind second pass ad detection with different focus (subtle/baked-in ads)."""
        if not self.api_key:
            logger.warning("Skipping second pass - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Prepare transcript with timestamps
            transcript_lines = []
            for segment in segments:
                start = segment['start']
                end = segment['end']
                text = segment['text']
                transcript_lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")

            transcript = "\n".join(transcript_lines)

            # Use blind second pass prompt from database (or default)
            system_prompt = self.get_second_pass_prompt()

            # Format user prompt with optional description
            description_section = ""
            if episode_description:
                description_section = f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Second pass: Including episode description ({len(episode_description)} chars)")
            else:
                logger.info(f"[{slug}:{episode_id}] Second pass: No episode description available")

            prompt = USER_PROMPT_TEMPLATE.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                description_section=description_section,
                transcript=transcript
            )

            # Log prompt hash for debugging determinism
            prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
            logger.info(f"[{slug}:{episode_id}] Second pass prompt hash: {prompt_hash}")

            logger.info(f"[{slug}:{episode_id}] Second pass: Sending transcript to Claude "
                       f"({len(segments)} segments, {len(transcript)} chars)")

            # Call Claude API with retry logic
            model = self.get_model()
            response = None
            last_error = None
            max_retries = RETRY_CONFIG['max_retries']

            for attempt in range(max_retries + 1):
                try:
                    response = self.client.messages.create(
                        model=model,
                        max_tokens=2000,
                        temperature=0.0,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    break
                except Exception as e:
                    last_error = e
                    if self._is_retryable_error(e) and attempt < max_retries:
                        # For rate limit errors, wait full minute to reset window
                        if isinstance(e, RateLimitError):
                            delay = 60.0
                            logger.warning(
                                f"[{slug}:{episode_id}] Rate limit hit, waiting {delay:.0f}s for window reset"
                            )
                        else:
                            delay = self._calculate_backoff(attempt)
                            logger.warning(
                                f"[{slug}:{episode_id}] Second pass API error (attempt {attempt + 1}): {e}. Retrying in {delay:.1f}s"
                            )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"[{slug}:{episode_id}] Second pass failed: {e}")
                        return {
                            "ads": [],
                            "status": "failed",
                            "error": str(e),
                            "retryable": self._is_retryable_error(e)
                        }

            if response is None:
                return {"ads": [], "status": "failed", "error": str(last_error), "retryable": True}

            # Extract and parse response
            response_text = response.content[0].text if response.content else ""
            logger.info(f"[{slug}:{episode_id}] Second pass response: {len(response_text)} chars")

            # Parse JSON - same logic as first pass
            try:
                ads = None
                code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
                if code_block_match:
                    try:
                        ads = json.loads(code_block_match.group(1))
                    except json.JSONDecodeError:
                        pass

                if ads is None:
                    for match in re.finditer(r'\[(?:[^\[\]]*|\[(?:[^\[\]]*|\[[^\[\]]*\])*\])*\]', response_text):
                        try:
                            potential_ads = json.loads(match.group())
                            if isinstance(potential_ads, list):
                                if not potential_ads or (isinstance(potential_ads[0], dict) and 'start' in potential_ads[0]):
                                    ads = potential_ads
                        except json.JSONDecodeError:
                            continue

                if ads is None:
                    clean_response = re.sub(r'```json\s*', '', response_text)
                    clean_response = re.sub(r'```\s*', '', clean_response)
                    start_idx = clean_response.find('[')
                    end_idx = clean_response.rfind(']') + 1
                    if start_idx >= 0 and end_idx > start_idx:
                        ads = json.loads(clean_response[start_idx:end_idx])

                if ads is None:
                    logger.warning(f"[{slug}:{episode_id}] Second pass: No JSON array found")
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt}

                if isinstance(ads, list):
                    valid_ads = []
                    for ad in ads:
                        if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                            valid_ads.append({
                                'start': float(ad['start']),
                                'end': float(ad['end']),
                                'confidence': float(ad.get('confidence', 1.0)),
                                'reason': ad.get('reason', 'Second pass detection'),
                                'end_text': ad.get('end_text', ''),
                                'pass': 2  # Mark as second pass detection
                            })

                    if valid_ads:
                        total_ad_time = sum(ad['end'] - ad['start'] for ad in valid_ads)
                        logger.info(f"[{slug}:{episode_id}] Second pass found {len(valid_ads)} additional ads "
                                   f"({total_ad_time/60:.1f} min)")
                        for ad in valid_ads:
                            logger.info(f"[{slug}:{episode_id}] Second pass Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                                       f"({ad['end']-ad['start']:.0f}s) end_text='{ad.get('end_text', '')[:50]}'")
                    else:
                        logger.info(f"[{slug}:{episode_id}] Second pass: No additional ads found")

                    return {
                        "ads": valid_ads,
                        "status": "success",
                        "raw_response": response_text,
                        "prompt": prompt,
                        "model": model
                    }
                else:
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt}

            except json.JSONDecodeError as e:
                logger.error(f"[{slug}:{episode_id}] Second pass JSON parse error: {e}")
                return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": str(e)}

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Second pass failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}

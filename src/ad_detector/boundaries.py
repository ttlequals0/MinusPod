"""Boundary-refinement helpers for detected ads.

Pure functions over ad dicts and transcript segments. No DB, no LLM client.
Split out of ``ad_detector/__init__.py`` for readability; behavior is
unchanged from the pre-split module.
"""
import logging
import re
from typing import List, Dict, Optional

from utils.text import get_transcript_text_for_range
from sponsor_service import SponsorService
from utils.constants import NON_BRAND_WORDS

from config import (
    SHORT_GAP_THRESHOLD,
    MAX_MERGED_DURATION,
    BOUNDARY_EXTENSION_WINDOW, BOUNDARY_EXTENSION_MAX,
    AD_CONTENT_URL_PATTERNS, AD_CONTENT_PROMO_PHRASES,
    MIN_KEYWORD_LENGTH, MIN_UNCOVERED_TAIL_DURATION,
)

logger = logging.getLogger('podcast.claude')


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


# Common words that appear in ad reasons but are not brand names.
# Module-level alias preserved so existing in-file references and any
# external test introspection continue to work.
_NON_BRAND_WORDS = NON_BRAND_WORDS


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

    Thin delegation to SponsorService.extract_sponsors_from_transcript
    (canonical implementation). Retained as a module-level alias because
    several call sites and tests import this name directly.
    """
    return SponsorService.extract_sponsors_from_transcript(text, ad_reason)


# --- Timestamp validation (Fix 1: Claude hallucination correction) ---


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


# --- Region unpacking helper ---

def _unpack_region(region) -> tuple:
    """Extract (start, end) from a region dict or tuple."""
    if isinstance(region, dict):
        return region['start'], region['end']
    return region[0], region[1]


# --- Uncovered tail preservation (Fix 2) ---

def get_uncovered_portions(ad: Dict, covered_regions: list,
                           min_duration: float = None) -> List[Dict]:
    """Find portions of an ad not covered by pattern-matched regions.

    Instead of binary "covered or not", this identifies uncovered gaps
    (head, middle, tail) and returns them as separate ad segments.

    Args:
        ad: Ad dict with 'start' and 'end'
        covered_regions: List of region dicts or (start, end) tuples
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
    for region in covered_regions:
        cov_start, cov_end = _unpack_region(region)
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

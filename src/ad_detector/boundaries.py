"""Boundary-refinement helpers for detected ads.

Pure functions over ad dicts and transcript segments. No DB, no LLM client.
Split out of ``ad_detector/__init__.py`` for readability; behavior is
unchanged from the pre-split module.
"""
import logging
import re
from typing import List, Dict, Optional

from utils.markers import mark_distinct_merge, note_merged_members
from utils.text import get_transcript_text_for_range
from utils.time import overlap_seconds, ranges_overlap
from sponsor_service import SponsorService
from utils.constants import NON_BRAND_WORDS

from config import (
    SHORT_GAP_THRESHOLD,
    MAX_MERGED_DURATION,
    MIN_CONTENT_BETWEEN_ADS_SECONDS,
    BOUNDARY_EXTENSION_WINDOW, BOUNDARY_EXTENSION_MAX,
    BOUNDARY_EXTENSION_CONNECTOR_SKIP, BOUNDARY_EXTENSION_SKIP_MAX,
    AD_CONTENT_URL_PATTERNS, AD_CONTENT_PROMO_PHRASES,
    AD_CONTENT_PHONE_PATTERNS,
    MIN_KEYWORD_LENGTH, MIN_UNCOVERED_TAIL_DURATION,
    TERMINAL_SNAP_EOF_TOLERANCE_SECONDS,
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


def extend_ad_boundaries_by_content(ads: List[Dict], segments: List[Dict],
                                    extend_start: bool = True) -> List[Dict]:
    """Extend ad boundaries by checking adjacent segments for ad-like content.

    For each detected ad, examines transcript text immediately before and after
    the ad boundary. If the adjacent text contains ad indicators (sponsor names,
    URLs, promotional language), the boundary is extended to include it.

    This addresses DAI ads where detection cuts off ~5 seconds too early,
    missing the final call-to-action or URL mention.

    Args:
        ads: List of detected ad segments
        segments: List of transcript segments with 'start', 'end', 'text'
        extend_start: Also extend ad starts backwards (the post-reviewer tail
            pass sets this False so it only sweeps trailing CTAs)

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
            # Walk forward, skipping up to BOUNDARY_EXTENSION_CONNECTOR_SKIP
            # consecutive non-qualifying segments: CTA tails often sandwich a
            # connector line ("Thank you for the job you do") between sponsor
            # mentions, but a long run of plain content means the ad is over.
            # The skip is also bounded in seconds: two long story segments
            # should end the walk just like three short ones.
            new_end = ad_end
            skipped = 0
            skipped_time = 0.0
            for seg in segments:
                if seg['end'] <= ad_end:
                    continue  # fully inside the ad; a straddler still counts
                if seg['start'] >= ad_end + BOUNDARY_EXTENSION_MAX:
                    break  # segments are time-sorted
                if _text_has_ad_content(seg.get('text', '').lower(), ad_sponsors):
                    # Cap at the window bound: a long qualifying segment
                    # (straddler or merged transcription) must not pull the
                    # end past the documented max extension.
                    new_end = min(seg['end'], ad_end + BOUNDARY_EXTENSION_MAX)
                    skipped = 0
                    skipped_time = 0.0
                else:
                    skipped += 1
                    # Only the portion past the ad end counts (first segment
                    # can straddle the boundary).
                    skipped_time += seg['end'] - max(seg['start'], ad_end)
                    if (skipped > BOUNDARY_EXTENSION_CONNECTOR_SKIP
                            or skipped_time > BOUNDARY_EXTENSION_SKIP_MAX):
                        break

            if new_end > ad_end:
                logger.info(
                    f"Extended ad end by content: {ad_end:.1f}s -> {new_end:.1f}s "
                    f"(+{new_end - ad_end:.1f}s, sponsors: {ad_sponsors})"
                )
                ad_copy['end'] = new_end
                ad_copy['end_extended_by_content'] = True

        # Check text BEFORE ad start for continuation
        if extend_start:
            before_text = get_transcript_text_for_range(
                segments, max(0, ad_start - BOUNDARY_EXTENSION_WINDOW), ad_start
            ).lower()

            if before_text and _text_has_ad_content(before_text, ad_sponsors):
                new_start = ad_start
                # Walk backwards through segments
                for seg in reversed(segments):
                    if seg['end'] <= ad_start - BOUNDARY_EXTENSION_MAX:
                        break  # reversed walk: everything earlier is further out
                    if seg['end'] <= ad_start:
                        seg_text = seg.get('text', '').lower()
                        if _text_has_ad_content(seg_text, ad_sponsors):
                            # Same window cap as the end walk.
                            new_start = max(seg['start'], ad_start - BOUNDARY_EXTENSION_MAX)
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

    # Check for URL, phone, and promotional patterns
    for pattern_list in (AD_CONTENT_URL_PATTERNS, AD_CONTENT_PHONE_PATTERNS,
                         AD_CONTENT_PROMO_PHRASES):
        for pattern in pattern_list:
            if pattern in text:
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

    # Primary: sponsor field (normalize stray/internal whitespace)
    sponsor = ' '.join((ad.get('sponsor') or '').split())
    if sponsor and sponsor.lower() not in {'unknown', 'none'}:
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

    # For a multi-word sponsor, drop the individual words it decomposes into
    # (e.g. 'capital'/'one' from 'Capital One'); a lone common word like 'one'
    # otherwise relocates an ad onto unrelated editorial text.
    if ' ' in sponsor:
        constituents = set(sponsor.lower().split())
        keywords = {k for k in keywords if ' ' in k or k not in constituents}

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


def _merge_ad_pair(current_ad: Dict, next_ad: Dict, gap_desc: str = "") -> None:
    """Fold ``next_ad`` into ``current_ad`` in place. Shared by both merge passes
    so their bookkeeping (end extension, confidence, reason, sponsor, cue
    evidence) stays consistent. Pass-specific fields (sponsor_names, gap text)
    are set by the caller.

    ``gap_desc`` is appended to the merged reason when set (filler-gap pass).
    """
    mark_distinct_merge(current_ad, next_ad)
    current_ad['end'] = next_ad['end']
    current_ad['confidence'] = max(current_ad.get('confidence', 0.0),
                                   next_ad.get('confidence', 0.0))

    # Reason concat: keep both fragments' reasons plus any gap annotation.
    gap_note = f" ({gap_desc})" if gap_desc else ""
    if current_ad.get('reason') and next_ad.get('reason'):
        inner = f"; {gap_desc}" if gap_desc else ""
        current_ad['reason'] = f"{current_ad['reason']} (merged with: {next_ad['reason']}{inner})"
    elif next_ad.get('reason'):
        current_ad['reason'] = f"{next_ad['reason']}{gap_note}"

    # Sponsor field: do not let None overwrite a real value.
    if current_ad.get('sponsor') is None and next_ad.get('sponsor') is not None:
        current_ad['sponsor'] = next_ad['sponsor']

    # end_text comes from the later ad.
    if next_ad.get('end_text'):
        current_ad['end_text'] = next_ad['end_text']

    # Preserve cue-backedness so cue-gated feeds still recognize the merged span.
    # current.end == next.end, so next's cue_snap end-edge record stays meaningful.
    # Applies to both merge passes: a break where either fragment is cue-backed
    # is treated as cue-backed (folding adjacent ads into one break), so the
    # merged span is auto-cut rather than held on a cue-gated feed.
    if next_ad.get('cue_snap') and not current_ad.get('cue_snap'):
        current_ad['cue_snap'] = next_ad['cue_snap']
    if next_ad.get('detection_stage') == 'cue_pair' or current_ad.get('detection_stage') == 'cue_pair':
        current_ad['detection_stage'] = 'cue_pair'


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
                    _merge_ad_pair(current_ad, next_ad)
                    # Same-sponsor-specific: record the shared sponsors.
                    current_ad['sponsor_names'] = list(common_sponsors)
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


def merge_ads_across_short_content_gaps(
    ads: List[Dict],
    segments: List[Dict],
    min_content_seconds: float = MIN_CONTENT_BETWEEN_ADS_SECONDS,
    max_merged_seconds: float = MAX_MERGED_DURATION,
    false_positive_corrections: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Merge consecutive ads whose gap contains less than min_content_seconds of speech.

    Within a single ad-break, individual ads are sometimes separated by
    ad-transition music or silence (~10s filler). This pass collapses those
    filler gaps so the whole break is cut as one contiguous span.

    Discriminator: actual show content (speech segments) in the gap, NOT
    wall-clock duration. Music and silence are untranscribed and contribute ~0.
    Two ads separated by >= min_content_seconds of speech are left separate.

    Args:
        ads: Detected ad segments (any order; sorted internally).
        segments: Transcript segments for the episode.
        min_content_seconds: Gap with less than this much speech -> merge.
            <= 0 disables the pass entirely (no merging).
        max_merged_seconds: Safety cap; skip merge if result would exceed this.
        false_positive_corrections: User FP ranges ({'start','end'}). Merging
            dilutes the validator's per-ad overlap ratio, so any merge whose
            component ad or resulting span intersects an FP range is skipped
            (conservative: the validator decides those spans un-merged).

    Returns:
        Sorted list of ads with filler-gap pairs collapsed.
    """
    fp_ranges = [(c['start'], c['end']) for c in (false_positive_corrections or [])]

    def _overlaps_fp(start, end):
        return any(ranges_overlap(start, end, fs, fe) for fs, fe in fp_ranges)
    if not ads or len(ads) < 2:
        return sorted(ads, key=lambda x: x['start']) if ads else ads
    # Disabled, or no transcript to measure content with: never merge.
    # Without segments every gap would measure 0 content and over-merge.
    if min_content_seconds <= 0 or not segments:
        return sorted(ads, key=lambda x: x['start'])

    ads = sorted(ads, key=lambda x: x['start'])

    merged = []
    i = 0
    while i < len(ads):
        current_ad = ads[i].copy()

        j = i + 1
        while j < len(ads):
            next_ad = ads[j]

            gap_start = current_ad['end']
            gap_end = next_ad['start']

            if gap_end <= gap_start:
                # Overlapping or touching; let deduplicate_window_ads handle these.
                break

            # Measure actual show-content duration in the gap.
            content_seconds = _content_duration_in_range(segments, gap_start, gap_end)
            if content_seconds >= min_content_seconds:
                # Real show content between ads -- do not merge.
                break

            # Safety: skip if result would be too long.
            merged_duration = next_ad['end'] - current_ad['start']
            if merged_duration > max_merged_seconds:
                logger.info(
                    f"Skipping filler-gap merge: {current_ad['start']:.1f}s-{current_ad['end']:.1f}s + "
                    f"{next_ad['start']:.1f}s-{next_ad['end']:.1f}s would be {merged_duration:.0f}s "
                    f"(>{max_merged_seconds:.0f}s max)"
                )
                break

            # FP-correction guard: never merge if a component ad or the merged
            # span touches a user FP range -- merging would dilute the
            # validator's overlap ratio and cut/keep the wrong span.
            if (_overlaps_fp(current_ad['start'], current_ad['end'])
                    or _overlaps_fp(next_ad['start'], next_ad['end'])
                    or _overlaps_fp(current_ad['start'], next_ad['end'])):
                logger.info(
                    f"Skipping filler-gap merge near FP correction: "
                    f"{current_ad['start']:.1f}s-{next_ad['end']:.1f}s"
                )
                break

            logger.info(
                f"Merging across filler gap ({content_seconds:.1f}s content): "
                f"{current_ad['start']:.1f}s-{current_ad['end']:.1f}s + "
                f"{next_ad['start']:.1f}s-{next_ad['end']:.1f}s"
            )

            # Inner-edge silence_snap records are dropped with next_ad; the
            # shared helper carries next's cue_snap/cue_pair evidence forward.
            gap_desc = f"merged across {content_seconds:.0f}s filler gap"
            _merge_ad_pair(current_ad, next_ad, gap_desc=gap_desc)

            j += 1

        merged.append(current_ad)
        i = j if j > i + 1 else i + 1

    if len(merged) < len(ads):
        logger.info(f"Filler-gap merge: {len(ads)} ads -> {len(merged)} ads")

    return merged


def _content_duration_in_range(segments: List[Dict], range_start: float, range_end: float) -> float:
    """Return total speech duration (seconds) for segments overlapping [range_start, range_end).

    Segments that fall entirely outside the range contribute 0. Partial overlaps
    are clipped to the range boundary.
    """
    total = 0.0
    for seg in segments:
        seg_start = seg.get('start', 0.0)
        seg_end = seg.get('end', 0.0)
        text = seg.get('text', '').strip()
        if not text:
            # Untranscribed gap (music/silence) -- contributes 0.
            continue
        total += overlap_seconds(seg_start, seg_end, range_start, range_end)
    return total


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
            # Non-overlapping spans (touching or gapped) are distinct ads
            # chained together, not the same ad re-detected across an
            # overlapping window. LLM ad breaks are often exactly contiguous
            # (end == next start), so touch must count too. Keep these
            # expand-only in the reviewer; a true overlap (start < end) is the
            # same ad and stays tightenable.
            if current['start'] >= last['end']:
                mark_distinct_merge(last, current)
            elif 'merged_protected_start' in last:
                # True overlap extending a tracked merge: fold the member in
                # so the protected union covers audio it adds past the
                # recorded end (else a later trim could sever it).
                note_merged_members(last, current)
            # Merge: extend end time if current goes further
            if current['end'] > last['end']:
                last['end'] = current['end']
                if current.get('end_text'):
                    last['end_text'] = current['end_text']
            # Keep higher confidence
            if current.get('confidence', 0) > last.get('confidence', 0):
                last['confidence'] = current['confidence']
            # Keep sponsor and reason as a consistent pair from the SAME member
            # (mirrors _merge_detection_results): a merged marker must never show
            # one ad's sponsor with another ad's description. The longer reason is
            # the content-aware one, so take its sponsor with it.
            current_reason = current.get('reason', '')
            last_reason = last.get('reason', '')
            if len(current_reason) > len(last_reason):
                last['reason'] = current_reason
                last['sponsor'] = current.get('sponsor')
        else:
            merged.append(current.copy())

    if len(merged) < len(all_ads):
        logger.info(f"Window deduplication: {len(all_ads)} -> {len(merged)} ads")

    return merged


# --- Terminal boundary snap to splice evidence (spec 2.3b) ---

def snap_terminal_ad_to_splice(ads: List[Dict], segments: List[Dict],
                               splice_events: List[Dict],
                               episode_duration: float,
                               window_s: float,
                               coverage_ads: Optional[List[Dict]] = None,
                               eof_tolerance_s: float = TERMINAL_SNAP_EOF_TOLERANCE_SECONDS
                               ) -> List[Dict]:
    """Snap a terminal ad's start back to the strongest deep-silence splice.

    DAI post-roll blocks often begin at an encoded silence a few seconds
    before where the LLM or reviewer placed the marker start. For a marker
    whose end is within eof_tolerance_s of EOF, scan back from its start up
    to window_s for digital_silence/deep_silence events and snap the start
    to the deepest one whose extension span is safe to cut: every
    transcribed segment in [event_time, old_start) must either overlap a
    detected marker (ad-classified / pattern-matched) or read as ad content
    (sponsor names, URLs, promo phrases). Untranscribed audio always passes;
    a content-classified sentence blocks that candidate.

    Returns a new list with snapped copies; other ads pass through.
    """
    if not ads or not splice_events or episode_duration <= 0:
        return ads
    coverage = coverage_ads if coverage_ads is not None else ads
    out = []
    for ad in ads:
        ad_copy = ad.copy()
        if episode_duration - ad_copy['end'] <= eof_tolerance_s:
            candidates = [
                e for e in splice_events
                if e.get('type') in ('digital_silence', 'deep_silence')
                and e.get('time') is not None
                and ad_copy['start'] - window_s <= e['time'] < ad_copy['start']
            ]
            # Deepest first; the first candidate with a safe span wins.
            candidates.sort(key=lambda e: e['depth_dbfs']
                            if e.get('depth_dbfs') is not None else 0.0)
            ad_text = get_transcript_text_for_range(
                segments, ad_copy['start'], ad_copy['end']).lower()
            ad_sponsors = extract_sponsor_names(ad_text, ad_copy.get('reason'))
            for event in candidates:
                if _span_blocked_by_content(segments, coverage, ad_sponsors,
                                            event['time'], ad_copy['start']):
                    continue
                original_start = ad_copy['start']
                ad_copy['start'] = event['time']
                ad_copy['terminal_snap'] = {
                    'original_start': original_start,
                    'event_time': event['time'],
                    'event_type': event['type'],
                    'depth_dbfs': event.get('depth_dbfs'),
                }
                logger.info(
                    f"Terminal splice snap: ad start {original_start:.1f}s -> "
                    f"{event['time']:.1f}s ({event['type']}, "
                    f"depth {event.get('depth_dbfs')} dBFS)"
                )
                break
        out.append(ad_copy)
    return out


def _span_blocked_by_content(segments: List[Dict], ads: List[Dict],
                             ad_sponsors: set,
                             span_start: float, span_end: float) -> bool:
    """True when the span holds transcribed speech that is neither covered
    by a detected marker nor ad-like content."""
    for seg in segments:
        seg_start = seg.get('start', 0.0)
        seg_end = seg.get('end', 0.0)
        if seg_end <= span_start or seg_start >= span_end:
            continue
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        covered = any(
            ranges_overlap(seg_start, seg_end, m['start'], m['end'])
            for m in ads
            if m.get('start') is not None and m.get('end') is not None
        )
        if covered or _text_has_ad_content(text.lower(), ad_sponsors):
            continue
        return True
    return False

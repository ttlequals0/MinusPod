"""Prompt templates, window framing, and LLM-response parsing.

Pure functions / module-level constants for prompt assembly and response
parsing. No DB, no LLM client. Split out of ``ad_detector/__init__.py``
for readability; behavior is unchanged from the pre-split module.
"""
import logging
import json
from typing import List, Dict

from sponsor_service import SponsorService
from utils.prompt import format_sponsor_block, render_prompt
from utils.time import parse_timestamp
from utils.llm_response import (
    extract_json_ads_array,
    extract_json_object,
    find_json_array_candidates as _find_json_array_candidates,
)
from utils.constants import (
    INVALID_SPONSOR_VALUES, STRUCTURAL_FIELDS,
    SPONSOR_PRIORITY_FIELDS, SPONSOR_PATTERN_KEYWORDS,
    NOT_AD_CLASSIFICATIONS,
)
from config import (
    WINDOW_SIZE_SECONDS, WINDOW_OVERLAP_SECONDS,
    LOW_CONFIDENCE, CONFIDENCE_STRING_MAP,
    CONTENT_DURATION_THRESHOLD, LOW_EVIDENCE_WARN_THRESHOLD,
)

logger = logging.getLogger('podcast.claude')


# User prompt template (not configurable via UI - just formats the transcript)
# Description is optional - may contain sponsor lists, chapter markers, or content context
USER_PROMPT_TEMPLATE = """Podcast: {podcast_name}
Episode: {episode_title}
{description_section}
Transcript:
{transcript}"""

# Sliding window step (derived from config values)
# WINDOW_SIZE_SECONDS and WINDOW_OVERLAP_SECONDS imported from config.py
WINDOW_STEP_SECONDS = WINDOW_SIZE_SECONDS - WINDOW_OVERLAP_SECONDS  # 7 minutes


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


def format_window_prompt(
    podcast_name: str,
    episode_title: str,
    description_section: str,
    transcript_lines: List[str],
    window_index: int,
    total_windows: int,
    window_start: float,
    window_end: float,
    audio_context: str = "",
) -> str:
    """Build the user prompt for a single ad-detection window.

    `description_section` and `audio_context` are pre-built strings so the
    benchmark can call this without DB or audio-analysis state. Production
    callers assemble both then pass them in.
    """
    transcript = "\n".join(transcript_lines)
    window_context = f"""

=== WINDOW {window_index + 1}/{total_windows}: {window_start/60:.1f}-{window_end/60:.1f} minutes ===
- Use absolute timestamps from transcript (as shown in brackets)
- If an ad starts before this window, use the first timestamp with note "continues from previous"
- If an ad extends past this window, use {window_end:.1f} with note "continues in next"
"""
    return USER_PROMPT_TEMPLATE.format(
        podcast_name=podcast_name,
        episode_title=episode_title,
        description_section=description_section,
        transcript=transcript,
    ) + audio_context + window_context


def get_static_system_prompt() -> str:
    """Return DEFAULT_SYSTEM_PROMPT with the static SEED_SPONSORS list substituted.

    Reproducible from source code -- no DB, env, or wallclock dependency.
    Used by the offline LLM benchmark. Production reads stored prompts and
    merges DB-derived sponsors via ``AdDetector.get_system_prompt`` instead.
    """
    from utils.constants import DEFAULT_SYSTEM_PROMPT
    from utils.constants import SEED_SPONSORS
    sponsor_list = ', '.join(s['name'] for s in SEED_SPONSORS)
    return render_prompt(
        DEFAULT_SYSTEM_PROMPT,
        sponsor_database=format_sponsor_block(sponsor_list),
    )


def parse_ads_from_response(response_text: str, slug: str = None,
                              episode_id: str = None,
                              sponsor_service=None) -> List[Dict]:
    """Parse ad segments from Claude's JSON response.

    Returns:
        List of validated ad dicts with start, end, confidence, reason, end_text
    """
    def get_valid_value(value):
        if not value:
            return None
        str_value = str(value).strip()
        if len(str_value) < 2:
            return None
        if str_value.lower() in INVALID_SPONSOR_VALUES:
            return None
        return str_value

    def _text_is_duplicate(a: str, b: str) -> bool:
        """Check if two strings are essentially the same text."""
        a_lower = a.lower().strip()
        b_lower = b.lower().strip()
        if a_lower.startswith(b_lower) or b_lower.startswith(a_lower):
            return True
        a_words = set(a_lower.split())
        b_words = set(b_lower.split())
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words)
        smaller = min(len(a_words), len(b_words))
        return overlap / smaller > 0.8 if smaller > 0 else False

    # Local alias for the SponsorService method - keeps call sites below short
    # and avoids re-importing inside the closure.
    extract_sponsor_from_text = SponsorService.extract_sponsor_from_reason

    def extract_sponsor_name(ad: dict) -> str:
        """Extract sponsor/advertiser name using priority fields, keywords, and dynamic scanning."""
        for field in SPONSOR_PRIORITY_FIELDS:
            value = get_valid_value(ad.get(field))
            if value:
                return value

        for key in ad.keys():
            key_lower = key.lower()
            for keyword in SPONSOR_PATTERN_KEYWORDS:
                if keyword in key_lower:
                    value = get_valid_value(ad.get(key))
                    if value:
                        return value

        priority_lower = {f.lower() for f in SPONSOR_PRIORITY_FIELDS}
        for key, val in ad.items():
            key_lower = key.lower()
            if key_lower in STRUCTURAL_FIELDS or key_lower in priority_lower:
                continue
            if isinstance(val, str) and len(val) < 80:
                value = get_valid_value(val)
                if value:
                    return value

        for key, val in ad.items():
            if key.lower() in STRUCTURAL_FIELDS:
                continue
            if isinstance(val, str) and len(val) > 10:
                sponsor = extract_sponsor_from_text(val)
                if sponsor:
                    return sponsor

        return 'Advertisement detected'

    try:
        ads, extraction_method = extract_json_ads_array(response_text, slug, episode_id)

        if ads is None or not isinstance(ads, list):
            logger.warning(f"[{slug}:{episode_id}] No valid JSON array found in response")
            return []

        # Validate and normalize ads - handle various field name patterns
        valid_ads = []
        for ad in ads:
            if isinstance(ad, dict):
                # Log raw ad object for debugging
                logger.debug(f"[{slug}:{episode_id}] Raw ad from LLM: {json.dumps(ad, default=str)[:500]}")
                # Fuzzy-match start/end timestamp fields from LLM response.
                # The LLM uses inconsistent field names across runs (start_time,
                # ad_start, timestamp_start, start_time_seconds, etc). Instead of
                # maintaining an ever-growing allowlist, match any key containing
                # 'start'/'end' that isn't a known text field.
                _SKIP_SUFFIXES = ('_note', '_text', '_snip', '_quote', '_description')
                _SKIP_KEYS = {'endorser', 'endorsed', 'price_starting', 'starting_point'}
                start_val = None
                end_val = None
                for k, v in ad.items():
                    kl = k.lower()
                    if kl in _SKIP_KEYS or any(kl.endswith(s) for s in _SKIP_SUFFIXES):
                        continue
                    if v is None:
                        continue
                    if start_val is None and 'start' in kl:
                        start_val = v
                    elif end_val is None and 'end' in kl and kl != 'endorser':
                        end_val = v

                if start_val is None or end_val is None:
                    logger.warning(
                        f"[{slug}:{episode_id}] Discarding ad candidate: "
                        f"missing timestamps (start={start_val}, end={end_val}) - "
                        f"fields={list(ad.keys())}, reason={str(ad.get('reason', ad.get('sponsor', '')))[:80]}"
                    )
                    continue

                try:
                    start = parse_timestamp(start_val)
                    end = parse_timestamp(end_val)
                    if end <= start:
                        logger.warning(
                            f"[{slug}:{episode_id}] Discarding ad candidate: "
                            f"invalid range (start={start:.1f}s >= end={end:.1f}s) - "
                            f"reason={str(ad.get('reason', ad.get('sponsor', '')))[:80]}"
                        )
                        continue
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
                            continue
                        if isinstance(val, str) and len(val) > 10:
                            # Prefer longer descriptive text over short values
                            if description is None or len(val) > len(description):
                                description = val
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
                    raw_conf = ad.get('confidence', 0.8)
                    if isinstance(raw_conf, str):
                        mapped = CONFIDENCE_STRING_MAP.get(raw_conf.lower().strip())
                        if mapped is not None:
                            logger.debug(f"[{slug}:{episode_id}] Mapped string confidence '{raw_conf}' -> {mapped}")
                            raw_conf = mapped
                        else:
                            raw_conf = raw_conf.rstrip('%')
                    raw_conf = float(raw_conf)
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
                        sponsor_service and
                        sponsor_service.find_sponsor_in_text(reason)
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

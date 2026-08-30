"""Prompt templates, window framing, and LLM-response parsing.

Pure functions / module-level constants for prompt assembly and response
parsing. No DB, no LLM client. Split out of ``ad_detector/__init__.py``
for readability; behavior is unchanged from the pre-split module.
"""
import logging
import json
import re

from sponsor_service import SponsorService
from utils.prompt import format_sponsor_block, render_prompt
from utils.text import truncate
from utils.time import parse_timestamp
from utils.llm_response import extract_json_ads_array
from utils.constants import (
    INVALID_SPONSOR_VALUES, STRUCTURAL_FIELDS,
    SPONSOR_PRIORITY_FIELDS, SPONSOR_PATTERN_KEYWORDS,
    SPONSOR_MAX_NAME_CHARS, REASON_DESCRIPTION_MAX,
    is_sponsor_reasoning_rationale,
    mentions_advertising,
    NOT_AD_CLASSIFICATIONS,
)
from config import (
    LOW_CONFIDENCE, CONFIDENCE_STRING_MAP,
    CONTENT_DURATION_THRESHOLD, LOW_EVIDENCE_WARN_THRESHOLD,
    get_stage_tunable, SEGMENT_CATEGORIES,
)

logger = logging.getLogger('podcast.claude')

# Two texts can only be duplicates when their lengths are comparable; below
# this ratio the shorter one is a fragment of the longer, which is worth
# keeping rather than discarding.
DUPLICATE_MIN_LENGTH_RATIO = 0.8

def _singular(key: str) -> str:
    """Drop one trailing plural. rstrip('s') stemmed 'names' to 'name' but also
    'address' to 'addre'."""
    lowered = key.lower()
    return lowered[:-1] if lowered.endswith('s') else lowered


# Sponsor field names with a trailing plural dropped, so the evidence gate
# accepts the "sponsors" key extract_sponsor_name already reads.
_SPONSOR_FIELD_STEMS = frozenset(_singular(f) for f in SPONSOR_PRIORITY_FIELDS)


# User prompt template (not configurable via UI - just formats the transcript)
# Description is optional - may contain sponsor lists, chapter markers, or content context
USER_PROMPT_TEMPLATE = """Podcast: {podcast_name}
Episode: {episode_title}
{description_section}
Transcript:
{transcript}"""

def create_windows(segments: list[dict], window_size: float = None,
                   overlap: float = None) -> list[dict]:
    """Create overlapping windows from transcript segments.

    Args:
        segments: List of transcript segments with 'start', 'end', 'text'
        window_size: Duration of each window in seconds. None resolves the
            user-configurable 'window_size_seconds' tunable at call time so
            Settings UI changes take effect without restart.
        overlap: Overlap between consecutive windows in seconds. None resolves
            the user-configurable 'window_overlap_seconds' tunable at call time.

    Returns:
        List of window dicts with:
            - 'start': window start time (absolute)
            - 'end': window end time (absolute)
            - 'segments': list of segments in this window
    """
    if not segments:
        return []

    if window_size is None:
        window_size = get_stage_tunable('window_size_seconds')
    if overlap is None:
        overlap = get_stage_tunable('window_overlap_seconds')

    # Get total transcript duration
    total_duration = segments[-1]['end']
    step_size = window_size - overlap
    if step_size <= 0:
        # overlap >= window_size never advances window_start and hangs the
        # detection worker forever. The Settings API cross-field check can be
        # bypassed via env vars or a direct DB write, so guard here too: fall
        # back to non-overlapping windows rather than wedge (config-1 /
        # ad-detection-1).
        logger.warning(
            "create_windows: window_overlap_seconds (%s) >= window_size_seconds "
            "(%s); falling back to non-overlapping windows to avoid a "
            "non-terminating loop.", overlap, window_size,
        )
        step_size = max(window_size, 1.0)

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
    transcript_lines: list[str],
    window_index: int,
    total_windows: int,
    window_start: float,
    window_end: float,
    audio_context: str = "",
    addressing_mode: str = "timestamps",
) -> str:
    """Build the user prompt for a single ad-detection window.

    `description_section` and `audio_context` are pre-built strings so the
    benchmark can call this without DB or audio-analysis state. Production
    callers assemble both then pass them in.

    `addressing_mode` selects the window-context rules appended after the
    header: 'timestamps' (default) emits the original three bullets telling
    the model to use absolute timestamps, byte-identical to before this
    parameter existed; 'segment_ids' (issue: hushpod adoption) emits
    SEGMENT_ID_WINDOW_RULES instead of those bullets -- never both, so the
    prompt never tells the model to use and never use timestamps in the same
    message. Callers that never pass it (benchmark, keep-content windows)
    get 'timestamps' behavior unchanged.
    """
    transcript = "\n".join(transcript_lines)
    header = (
        f"\n\n=== WINDOW {window_index + 1}/{total_windows}: "
        f"{window_start/60:.1f}-{window_end/60:.1f} minutes ==="
    )
    if addressing_mode == "segment_ids":
        rules = SEGMENT_ID_WINDOW_RULES
    else:
        rules = (
            "\n- Use absolute timestamps from transcript (as shown in brackets)"
            "\n- If an ad starts before this window, use the first timestamp with note \"continues from previous\""
            f"\n- If an ad extends past this window, use {window_end:.1f} with note \"continues in next\"\n"
        )
    window_context = header + rules
    return USER_PROMPT_TEMPLATE.format(
        podcast_name=podcast_name,
        episode_title=episode_title,
        description_section=description_section,
        transcript=transcript,
    ) + audio_context + window_context


SEGMENT_ID_SYSTEM_SECTION = """

ADDRESSING MODE: SEGMENT IDS
The transcript is a numbered list; each line starts with its [id]. For every
detection you report, replace the "start" and "end" timestamp fields with
integer "start_id" and "end_id" fields: the ids of the FIRST and LAST
transcript lines of the ad, inclusive. Refer to lines ONLY by the ids shown.
Never output timestamps and never invent ids that do not appear in the
transcript. All other rules (categories, confidence, reason) are unchanged.

Ignore any earlier instruction to read [Xs] timestamp markers or to output
numeric "start"/"end" seconds: in this mode the transcript lines carry [id]
numbers only, and the JSON fields "start"/"end" are replaced by integer
"start_id"/"end_id". All other rules (categories, confidence, reason) still
apply."""


SEGMENT_ID_WINDOW_RULES = (
    "\n- Report start_id/end_id integers from the [id] brackets, "
    "never timestamps"
    "\n- If an ad starts before this window, use this window's first id "
    "with note \"continues from previous\""
    "\n- If an ad extends past this window, use this window's last id "
    "with note \"continues in next\"\n"
)


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


def _flatten_ad_envelopes(ads: list) -> list:
    """Flatten ad-break envelopes the model intermittently emits.

    Instead of a flat list of ad objects, the LLM sometimes wraps each break in
    an envelope like ``{"ad_break_index": N, "ads": [ {ad}, {ad} ]}``. Such an
    envelope has no top-level start/end, so the per-ad parser would discard the
    whole break. Expand any dict whose ``ads`` value is a list into its inner ad
    objects; pass everything else through unchanged.
    """
    flat = []
    for item in ads:
        if isinstance(item, dict) and isinstance(item.get('ads'), list):
            flat.extend(inner for inner in item['ads'] if isinstance(inner, dict))
        else:
            flat.append(item)
    return flat


# The window prompt asks for these notes, so they arrive as prose glued to the
# front of a description and end up in the marker a reader sees.
_CONTINUATION_PREFIX_RE = re.compile(
    r'^(?:continues?\s+(?:from\s+previous|in\s+next)|continued)\b[\s;,.:-]*',
    re.IGNORECASE)


def _drop_leading(description: str, sponsor: str) -> str:
    """Drop a leading sponsor name from a description, so combining the two
    does not render "Acme: Acme ad for ...". Only on a word boundary: "Box"
    must not turn "Boxing gloves" into "ing gloves"."""
    if not description.lower().startswith(sponsor.lower()):
        return description
    rest = description[len(sponsor):]
    if rest and rest[0].isalnum():
        return description
    return rest.lstrip(' :,-.').strip() or description


def _strip_continuation_prefix(text: str) -> str:
    """Drop a leading window-continuation note from description text."""
    return _CONTINUATION_PREFIX_RE.sub('', text or '').lstrip()


def _flatten(value) -> str:
    """Flatten an LLM field to text. A back-to-back break makes the model
    answer a string field with a list, and str() would store the Python repr."""
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v).strip() for v in value if v and str(v).strip())
    return str(value).strip() if value else ''


def _as_text(value) -> str:
    """Flattened text with any leading window-continuation note dropped."""
    return _strip_continuation_prefix(_flatten(value))


def _get_valid_sponsor_value(value):
    if not value:
        return None
    str_value = _flatten(value)
    # A continuation note is window bookkeeping, not a sponsor; trimming
    # the prefix and keeping the remainder minted labels like 'window'.
    if _CONTINUATION_PREFIX_RE.match(str_value):
        return None
    if len(str_value) < 2:
        return None
    if str_value.lower() in INVALID_SPONSOR_VALUES:
        return None
    if len(str_value) > SPONSOR_MAX_NAME_CHARS:
        return None
    if is_sponsor_reasoning_rationale(str_value):
        return None
    return str_value


def _text_is_duplicate(a: str, b: str) -> bool:
    """Check if two strings are essentially the same text.

    Length has to be comparable first. A bare sponsor name is both a
    prefix of its own description and a full word subset of it, so
    without this "Box" swallowed the note explaining the read and left
    the marker saying only "Box".
    """
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    shorter, longer = sorted((a_lower, b_lower), key=len)
    if not shorter or len(shorter) < len(longer) * DUPLICATE_MIN_LENGTH_RATIO:
        return False
    if longer.startswith(shorter):
        return True
    a_words = set(a_lower.split())
    b_words = set(b_lower.split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words)
    smaller = min(len(a_words), len(b_words))
    return overlap / smaller > 0.8 if smaller > 0 else False


def _extract_sponsor_name(ad: dict) -> str:
    """Extract sponsor/advertiser name using priority fields, keywords, and dynamic scanning."""
    # Local alias for the SponsorService method - keeps call sites below short.
    extract_sponsor_from_text = SponsorService.extract_sponsor_from_reason

    for field in SPONSOR_PRIORITY_FIELDS:
        value = _get_valid_sponsor_value(ad.get(field))
        if value:
            return value

    for key in ad.keys():
        key_lower = key.lower()
        for keyword in SPONSOR_PATTERN_KEYWORDS:
            if keyword in key_lower:
                value = _get_valid_sponsor_value(ad.get(key))
                if value:
                    return value

    priority_lower = {f.lower() for f in SPONSOR_PRIORITY_FIELDS}
    for key, val in ad.items():
        key_lower = key.lower()
        if key_lower in STRUCTURAL_FIELDS or key_lower in priority_lower:
            continue
        if isinstance(val, str) and len(val) < 80:
            value = _get_valid_sponsor_value(val)
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


def _normalize_ad(ad: dict, start: float, end: float, slug: str = None,
                   episode_id: str = None, sponsor_service=None) -> dict | None:
    """Post-parse normalization shared by the timestamp-mode and segment-id-mode
    parsers: degenerate-range rejection, is_ad/classification filters, sponsor
    name + reason/description extraction, confidence normalization, the
    duration/evidence gate, and category resolution. ``ad`` still carries all
    raw LLM fields; ``start``/``end`` are already-resolved seconds (parsed
    from timestamp fields, or mapped from segment ids). Returns the final ad
    dict, or None if the candidate is rejected.
    """
    if end <= start:
        logger.warning(
            f"[{slug}:{episode_id}] Discarding ad candidate: "
            f"invalid range (start={start:.1f}s >= end={end:.1f}s) - "
            f"reason={str(ad.get('reason', ad.get('sponsor', '')))[:80]}"
        )
        return None

    # Filter out explicitly marked non-ads
    is_ad_val = ad.get('is_ad')
    if is_ad_val is not None:
        if str(is_ad_val).lower() in ('false', 'no', '0', 'none'):
            logger.info(f"[{slug}:{episode_id}] Skipping non-ad: "
                        f"{start:.1f}s-{end:.1f}s (is_ad={is_ad_val})")
            return None

    # Filter by classification/type field
    classification = str(ad.get('classification') or ad.get('type') or '').lower()
    if classification in NOT_AD_CLASSIFICATIONS:
        logger.info(f"[{slug}:{episode_id}] Skipping non-ad: "
                    f"{start:.1f}s-{end:.1f}s (classification={classification})")
        return None

    # Extract sponsor/advertiser name using priority fields + pattern matching
    # Try extract_sponsor_name first for a real sponsor name.
    # If it returns the default, fall back to Claude's raw reason.
    sponsor_name = _extract_sponsor_name(ad)
    reason = sponsor_name
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
    # Kept whole (#591); the old 300/150 caps put a literal
    # "..." in the UI with no fuller text to expand to.
    description = truncate(
        _strip_continuation_prefix(description),
        REASON_DESCRIPTION_MAX)

    # Combine sponsor + description in reason field
    if description:
        if reason and reason != 'Advertisement detected':
            # Avoid duplication: check if description is essentially the same text
            if not _text_is_duplicate(reason, description):
                description = _drop_leading(description, reason)
                reason = f"{reason}: {description}" if description else reason
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
        _singular(key) in _SPONSOR_FIELD_STEMS
        and _get_valid_sponsor_value(val)
        for key, val in ad.items()
    )
    has_known_sponsor = (
        sponsor_service and
        sponsor_service.find_sponsor_in_text(reason)
    ) if reason else False
    has_ad_language = mentions_advertising(reason)

    if not has_sponsor_field and not has_known_sponsor and not has_ad_language:
        # Low confidence + no evidence = reject regardless of duration
        if norm_conf < LOW_CONFIDENCE:
            logger.info(
                f"[{slug}:{episode_id}] Rejecting low-confidence non-sponsor: "
                f"{start:.1f}s-{end:.1f}s ({duration:.0f}s, conf={norm_conf:.0%}) - "
                f"reason: {reason[:100] if reason else 'None'}"
            )
            return None
        # No positive ad evidence -- apply duration gate
        # Short segments (<CONTENT_DURATION_THRESHOLD) get benefit of doubt
        # Long segments are almost certainly content descriptions
        if duration >= CONTENT_DURATION_THRESHOLD:
            logger.info(
                f"[{slug}:{episode_id}] Rejecting suspected content: "
                f"{start:.1f}s-{end:.1f}s ({duration:.0f}s) - "
                f"no sponsor identified in reason: {reason[:100] if reason else 'None'}"
            )
            return None
        # For shorter segments without evidence, log warning but allow through
        if duration >= LOW_EVIDENCE_WARN_THRESHOLD:
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
        'end_text': _as_text(ad.get('end_text'))
    }
    # Store sponsor name separately for UI display
    # (reuses sponsor_name captured above; ad is unmutated between)
    if sponsor_name and sponsor_name != 'Advertisement detected':
        ad_entry['sponsor'] = sponsor_name
    # Pass the LLM's raw category through unvalidated; the
    # merge seam normalizes it against SEGMENT_CATEGORIES.
    resolved_category = resolve_ad_category(ad)
    if resolved_category:
        ad_entry['category'] = resolved_category
    return ad_entry


def parse_ads_from_response(response_text: str, slug: str = None,
                              episode_id: str = None,
                              sponsor_service=None,
                              compliance_meta: dict | None = None) -> list[dict]:
    """Parse ad segments from Claude's JSON response.

    ``compliance_meta``: optional out-param dict (same pattern as
    ``run_stats`` elsewhere in the codebase). When given, this sets
    ``compliance_meta['extraction_failed']`` to True when no JSON ads array
    could be located or parsed at all, and False when a JSON array was
    successfully parsed -- including a valid, empty ``[]`` answer. Used by
    the addressing-mode compliance stats (timestamps effective mode) to
    distinguish "the model answered with no ads" from "the response wasn't
    parseable".

    Returns:
        List of validated ad dicts with start, end, confidence, reason, end_text
    """
    try:
        ads, extraction_method = extract_json_ads_array(response_text, slug, episode_id)

        if ads is None or not isinstance(ads, list):
            logger.warning(f"[{slug}:{episode_id}] No valid JSON array found in response")
            if compliance_meta is not None:
                compliance_meta['extraction_failed'] = True
            return []
        if compliance_meta is not None:
            compliance_meta['extraction_failed'] = False

        # Flatten any {"ad_break_index": N, "ads": [...]} envelopes the model
        # sometimes emits, so the per-ad parser below sees the inner ad objects.
        ads = _flatten_ad_envelopes(ads)

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
                    ad_entry = _normalize_ad(
                        ad, start, end, slug, episode_id, sponsor_service)
                    if ad_entry is not None:
                        valid_ads.append(ad_entry)
                except ValueError as e:
                    logger.warning(f"[{slug}:{episode_id}] Skipping ad with invalid timestamp: {e}")
                    continue

        return valid_ads

    except json.JSONDecodeError as e:
        logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
        if compliance_meta is not None:
            compliance_meta['extraction_failed'] = True
        return []


def _int_field(obj: dict, keys: tuple[str, ...]):
    """The first of ``keys`` present in ``obj``, coerced to int, or None if
    absent or non-numeric. Exact key match only -- unlike the fuzzy
    'start'/'end' substring matcher in ``parse_ads_from_response``, id fields
    must never be guessed at, or ``start_id`` could be misread by a substring
    match on 'start' as a timestamp."""
    for key in keys:
        if key in obj:
            try:
                return int(obj[key])
            except (TypeError, ValueError):
                return None
    return None


def parse_id_ads_from_response(response_text: str, slug: str = None,
                                episode_id: str = None,
                                sponsor_service=None) -> tuple[list[dict], bool]:
    """Parse an ID-mode LLM response (issue: hushpod adoption).

    Returns (ads, used_ids). used_ids is True when at least one object in
    the response carries integer id fields; ads then hold 'start_id'/'end_id'
    plus the usual fields (confidence, category, reason -- normalized later
    by ``resolve_segment_id_ads``). used_ids False means the model ignored
    the ID contract (some models do): the caller re-parses with
    ``parse_ads_from_response`` so the window is not lost, at the cost of
    approximate timestamps for that window.
    """
    try:
        raw, _extraction_method = extract_json_ads_array(response_text, slug, episode_id)
    except Exception as e:
        logger.warning(f"[{slug}:{episode_id}] Failed to extract ID-mode ads: {e}")
        return [], False
    if raw is None or not isinstance(raw, list):
        return [], False
    raw = _flatten_ad_envelopes(raw)
    if not raw:
        return [], True  # explicit empty "no ads" answer is a valid ID answer

    ads = []
    any_ids = False
    skipped_no_id = 0
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        sid_lo = _int_field(obj, ('start_id', 'startid', 'start_segment_id'))
        sid_hi = _int_field(obj, ('end_id', 'endid', 'end_segment_id'))
        if sid_lo is None or sid_hi is None:
            skipped_no_id += 1
            continue
        any_ids = True
        ad = dict(obj)
        ad['start_id'], ad['end_id'] = sid_lo, sid_hi
        # An id-mode object should never carry timestamp fields, but strip
        # them defensively so a stray 'start'/'end' can't leak through
        # resolve_segment_id_ads and be mistaken for the resolved value.
        ad.pop('start', None)
        ad.pop('end', None)
        ads.append(ad)
    if any_ids and skipped_no_id:
        # Mixed response: some objects used the id contract, others didn't
        # (likely timestamp-mode ads in the same response). The id-less
        # objects are silently dropped below -- surface the count so a lost
        # detection is diagnosable instead of invisible.
        logger.warning(
            f"[{slug}:{episode_id}] ID-mode response mixed formats: "
            f"skipped {skipped_no_id} object(s) without id fields")
    return (ads, True) if any_ids else ([], False)


def resolve_segment_id_ads(ads: list[dict], window_segments: list[dict],
                            slug: str = None, episode_id: str = None,
                            sponsor_service=None) -> list[dict]:
    """Map start_id/end_id to exact segment start/end seconds, then run the
    resolved ads through the same post-parse normalization
    ``parse_ads_from_response`` applies (confidence normalization, sponsor
    extraction, degenerate-range rejection, evidence gate, category
    resolution) via ``_normalize_ad``.

    Unknown ids drop the detection (an invented id is detectable; an
    invented timestamp is not -- that asymmetry is the point of this mode).
    """
    by_sid = {seg['sid']: seg for seg in window_segments if 'sid' in seg}
    resolved = []
    for ad in ads:
        lo = min(ad['start_id'], ad['end_id'])
        hi = max(ad['start_id'], ad['end_id'])
        seg_lo, seg_hi = by_sid.get(lo), by_sid.get(hi)
        if seg_lo is None or seg_hi is None:
            logger.warning(
                f"[{slug}:{episode_id}] Dropping detection with out-of-window "
                f"segment ids {lo}-{hi}")
            continue
        raw = {k: v for k, v in ad.items() if k not in ('start_id', 'end_id')}
        start = seg_lo['start']
        end = seg_hi['end']
        try:
            ad_entry = _normalize_ad(raw, start, end, slug, episode_id, sponsor_service)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"[{slug}:{episode_id}] Skipping ad with invalid field "
                f"(ids {lo}-{hi}): {e}")
            continue
        if ad_entry is not None:
            resolved.append(ad_entry)
    return resolved


# =============================================================================
# Category repair pass: a narrow follow-up call asking only for the category
# of ads a window's detection found but left uncategorized, rather than
# relying on prompt wording alone.
# =============================================================================

CATEGORY_REPAIR_SYSTEM_PROMPT = """You are assigning a category to podcast segments that were already identified in a prior pass. Do NOT detect new segments and do NOT change any start or end time -- only choose one category per segment listed in the user message.

Respond with ONLY valid JSON: an array of {"index": INTEGER, "category": STRING}, one entry per segment listed, using exactly one of:
- sponsor: a paid host read, a produced ad spot, a dynamically inserted ad (DAI), or a platform-inserted ad
- cross_promo: a produced segment promoting a different show, inserted by the platform or network
- self_promo: a produced or inserted segment where the show promotes its own other content (another show, Patreon, merch, mailing list)
- interaction: a produced or inserted segment asking listeners to subscribe, rate, review, or follow the show
- intro: the show's opening theme music and/or host introduction
- outro: the show's closing credits, sign-off, or theme music
- recap: a produced "coming up" preview, headline bumper, or "listen to this next" segment

Every listed segment MUST get exactly one category from that list. Do not add, remove, or reorder segments. No markdown, no explanation, just the JSON array."""

# Input schema for the forced-tool-call path (see
# llm_capabilities.supports_json_schema). Wrapped under "categories" because
# Anthropic tool input must be a JSON object; parse_category_repair_response
# accepts both shapes.
CATEGORY_REPAIR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "category": {"type": "string", "enum": list(SEGMENT_CATEGORIES)},
                },
                "required": ["index", "category"],
            },
        },
    },
    "required": ["categories"],
}

# Structured-output schema for detection windows (#694), gated by
# llm_client.supports_json_schema_for_calls. Wrapped under "ads" because a
# json_schema response must be a JSON object; every property is optional so
# both addressing modes and partial fields still validate.
AD_DETECTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "ads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "start_id": {"type": "integer"},
                    "end_id": {"type": "integer"},
                    # The prompt requires end_text on every segment and the
                    # sponsor extractors read these names; a schema-enforcing
                    # decoder would silently strip anything absent here.
                    "end_text": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "note": {"type": "string"},
                    **{name: {"type": "string"} for name in SPONSOR_PRIORITY_FIELDS},
                },
            },
        },
    },
    "required": ["ads"],
}

# Small fixed budget: the repair call only ever emits a short JSON array,
# not full-length ad detection. Not user-tunable; this is an internal call.
CATEGORY_REPAIR_MAX_TOKENS = 1024


def format_category_repair_prompt(transcript_excerpt: str,
                                   missing: list[tuple[int, dict]]) -> str:
    """Build the user prompt for the category repair call.

    ``missing`` is an iterable of (index, ad_dict) pairs; index is the ad's
    position in the window's full ad list, carried through unchanged so the
    response can be merged back onto the right ad unambiguously.
    """
    segments = [
        {
            "index": i,
            "start": ad.get('start'),
            "end": ad.get('end'),
            "reason": ad.get('reason') or '',
            "end_text": ad.get('end_text') or '',
        }
        for i, ad in missing
    ]
    return (
        f"Transcript excerpt for this window:\n{transcript_excerpt}\n\n"
        f"Segments needing a category:\n{json.dumps(segments)}"
    )


def _repair_index(value):
    """The segment index from a repair entry, or None. Accepts the digits a
    provider without enum enforcement may quote as a string."""
    # bool is a subclass of int in Python; exclude it explicitly so a stray
    # true/false in the index field cannot masquerade as 0/1.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    return None


# Spelled-out forms of the exact vocabulary: the model reaches for
# "self-promotion" as readily as "self_promo". A position word like "pre-roll"
# or a bare "ad" is still refused.
_CATEGORY_ALIASES = {
    'self_promotion': 'self_promo',
    'selfpromo': 'self_promo',
    'cross_promotion': 'cross_promo',
    'crosspromo': 'cross_promo',
    'sponsorship': 'sponsor',
}

# Keys a category can arrive under. Only Anthropic enforces the schema, so on
# every other provider the model names fields freely; the rest of this parser
# already tolerates that for start, end and sponsor.
_CATEGORY_KEY_HINTS = ('categor', 'segment_type', 'classification', 'type')


def _repair_category(value):
    """A known category from any field, or None. Spacing, case and hyphens
    vary between providers ("Cross-Promo"); the vocabulary does not, so only
    formatting and the spelled-out forms are normalized. A position word like
    "pre-roll", or a bare "ad", is not a category and stays rejected."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower().replace('-', '_').replace(' ', '_')
    candidate = _CATEGORY_ALIASES.get(candidate, candidate)
    return candidate if candidate in SEGMENT_CATEGORIES else None


def resolve_ad_category(ad: dict):
    """The segment category an ad object carries, wherever it put it.

    "category" first, then the other keys a model uses for the same idea. The
    value is validated against the vocabulary either way, so a `type` of "ad"
    or "advertisement" contributes nothing while a `type` of "self_promo"
    is taken at face value.
    """
    if not isinstance(ad, dict):
        return None
    direct = _repair_category(ad.get('category'))
    if direct:
        return direct
    for key, value in ad.items():
        kl = str(key).lower()
        if kl == 'category' or not any(h in kl for h in _CATEGORY_KEY_HINTS):
            continue
        found = _repair_category(value)
        if found:
            return found
    return None


def parse_category_repair_response(response_text: str) -> dict[int, str]:
    """Parse the repair call's response into {index: category}.

    Accepts a bare JSON array of {"index", "category"} objects (json_object
    path) or {"categories": [...]} (tool-forced json_schema path, since tool
    input must be a JSON object). Returns {} on any parse failure or
    validation miss; callers treat that as nothing resolved and fall back
    to the existing sponsor default.
    """
    try:
        data = json.loads(response_text)
    except (TypeError, ValueError):
        return {}
    if isinstance(data, dict):
        data = data.get('categories')
    if not isinstance(data, list):
        return {}
    resolved = {}
    rejected = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        idx = _repair_index(entry.get('index'))
        category = _repair_category(entry.get('category'))
        if idx is None or category is None:
            rejected.append(entry)
            continue
        resolved[idx] = category
    if rejected:
        # Only Anthropic enforces the schema; every other provider can answer
        # in a shape this drops, and a silent drop reads as "the model had no
        # opinion". Say what came back so it is diagnosable.
        logger.info(
            "Category repair: ignored %d unusable entr%s, e.g. %s",
            len(rejected), 'y' if len(rejected) == 1 else 'ies',
            json.dumps(rejected[:3])[:200])
    return resolved

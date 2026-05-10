"""LLM response parsing helpers."""
import json
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def find_json_array_candidates(text: str):
    """Yield each top-level ``[...]`` substring from ``text`` in left-to-right
    order.

    Linear-time single-pass scanner: tracks bracket depth and JSON string
    context (so brackets inside ``"..."`` do not affect depth) and records
    each span where the depth transitions from 0 -> 1 -> 0.
    """
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]
                    start = -1


# Common preamble patterns that some LLMs emit before JSON.
_PREAMBLE_PATTERNS = [
    r'^(?:Here (?:are|is) (?:the )?(?:detected )?ads?[:\s]*)',
    r'^(?:I (?:found|detected|identified)[^:]*[:\s]*)',
    r'^(?:The following (?:ads|advertisements)[^:]*[:\s]*)',
    r'^(?:Based on (?:my|the) analysis[^:]*[:\s]*)',
    r'^(?:After (?:reviewing|analyzing)[^:]*[:\s]*)',
]


def _strip_preamble(text: str, slug: Optional[str], episode_id: Optional[str]) -> str:
    cleaned = text.strip()
    for pattern in _PREAMBLE_PATTERNS:
        match = re.match(pattern, cleaned, re.IGNORECASE)
        if match:
            cleaned = cleaned[match.end():].strip()
            logger.debug(
                f"[{slug}:{episode_id}] Removed preamble: '{match.group()[:50]}'"
            )
            break
    return cleaned


def extract_json_ads_array(
    response_text: str,
    slug: Optional[str] = None,
    episode_id: Optional[str] = None,
) -> Tuple[Optional[list], Optional[str]]:
    """Extract a JSON array of ad dicts from an LLM's response text.

    Tries 4 strategies in order:
    0. Direct JSON parse (handles various wrapper object structures)
    1. Markdown code block extraction
    2. Bracket-depth scan for top-level JSON arrays
    3. Bracket-delimited fallback (first '[' to last ']')

    Returns (ads_list, extraction_method) or (None, None) if no valid JSON found.
    """
    cleaned_text = _strip_preamble(response_text, slug, episode_id)

    try:
        parsed = json.loads(cleaned_text)
        if isinstance(parsed, list):
            return parsed, "json_array_direct"
        if isinstance(parsed, dict):
            if 'window' in parsed and isinstance(parsed['window'], dict):
                window = parsed['window']
                for key in ['ads_detected', 'ads', 'advertisement_segments',
                            'ads_and_sponsorships', 'segments']:
                    if key in window and isinstance(window[key], list):
                        ads = window[key]
                        if key == 'segments':
                            ads = [s for s in ads
                                   if isinstance(s, dict) and s.get('type') == 'advertisement']
                        return ads, f"json_object_window_{key}"
            ad_keys = ['ads', 'ads_detected', 'advertisement_segments', 'ads_and_sponsorships']
            for key in ad_keys:
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key], f"json_object_{key}_key"
            if 'segments' in parsed and isinstance(parsed['segments'], list):
                ads = [s for s in parsed['segments']
                       if isinstance(s, dict) and s.get('type') == 'advertisement']
                return ads, "json_object_segments_key"
            _has_start = any('start' in k.lower() for k in parsed)
            _has_end = any('end' in k.lower() and k.lower() != 'endorser' for k in parsed)
            if _has_start and _has_end:
                logger.info(f"[{slug}:{episode_id}] Single ad object detected, wrapping in array")
                return [parsed], "json_object_single_ad"
            return [], "json_object_no_ads"
    except json.JSONDecodeError:
        pass

    code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1)), "markdown_code_block"
        except json.JSONDecodeError:
            pass

    scan_text = response_text[:200_000]
    last_valid_ads = None
    for candidate in find_json_array_candidates(scan_text):
        try:
            potential_ads = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(potential_ads, list):
            if not potential_ads or (isinstance(potential_ads[0], dict)
                                     and 'start' in potential_ads[0]):
                last_valid_ads = potential_ads
    if last_valid_ads is not None:
        return last_valid_ads, "regex_json_array"

    clean_response = re.sub(r'```json\s*', '', response_text)
    clean_response = re.sub(r'```\s*', '', clean_response)
    start_idx = clean_response.find('[')
    end_idx = clean_response.rfind(']') + 1
    if start_idx >= 0 and end_idx > start_idx:
        json_str = clean_response[start_idx:end_idx]
        try:
            return json.loads(json_str), "bracket_fallback"
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{slug}:{episode_id}] Strategy 3 JSON parse failed: {e} "
                f"(length={len(json_str)}, start={json_str[:50]!r}, end={json_str[-50:]!r})"
            )

    return None, None


def find_first_dict_with_key(obj, key: str) -> Optional[dict]:
    """Walk a parsed JSON value and return the first dict that contains
    ``key`` at its top level. Used to recover from LLMs that wrap their
    verdict object in extra metadata fields or nest it inside an array.
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for value in obj.values():
            found = find_first_dict_with_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_first_dict_with_key(item, key)
            if found is not None:
                return found
    return None


def extract_json_object(
    response_text: str,
    slug: Optional[str] = None,
    episode_id: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """Extract a single JSON object from an LLM's response text.

    Used by callers (e.g. the ad reviewer) that expect one JSON object per
    LLM call rather than an array. Tries 3 strategies:
    0. Direct JSON parse
    1. Markdown code block
    2. Brace-delimited fallback (first '{' to last '}')

    Returns (obj, extraction_method) or (None, None) if no valid JSON found.
    """
    cleaned_text = _strip_preamble(response_text, slug, episode_id)

    try:
        parsed = json.loads(cleaned_text)
        if isinstance(parsed, dict):
            return parsed, "json_object_direct"
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0], "json_array_first_object"
    except json.JSONDecodeError:
        pass

    code_block_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response_text)
    if code_block_match:
        try:
            obj = json.loads(code_block_match.group(1))
            if isinstance(obj, dict):
                return obj, "markdown_code_block"
        except json.JSONDecodeError:
            pass

    clean_response = re.sub(r'```json\s*', '', response_text)
    clean_response = re.sub(r'```\s*', '', clean_response)
    start_idx = clean_response.find('{')
    end_idx = clean_response.rfind('}') + 1
    if start_idx >= 0 and end_idx > start_idx:
        json_str = clean_response[start_idx:end_idx]
        try:
            obj = json.loads(json_str)
            if isinstance(obj, dict):
                return obj, "brace_fallback"
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{slug}:{episode_id}] JSON object brace_fallback parse failed: {e} "
                f"(length={len(json_str)}, start={json_str[:50]!r}, end={json_str[-50:]!r})"
            )

    return None, None

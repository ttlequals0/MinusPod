"""Scheduled review of learned patterns with approval and undo."""
from __future__ import annotations

import fcntl
import json
import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from rapidfuzz import fuzz

from database import Database
from database.settings import registry_default, registry_get_default
from llm_client import ProviderRateLimitedError
from llm_route import (
    LiveRoute, apply_failover, client_for_route, live_route_from, resolve_route,
)
from pattern_cleanup_hash import INVALID_MARKER, review_hash, stats_evidence_hash
from pattern_variants import derive_intro_outro
from processing_queue import ProcessingQueue
from sponsor_normalize import get_or_create_known_sponsor, sanitize_sponsor_name
from sponsor_service import SponsorService
from text_pattern_matcher import MIN_TEXT_LENGTH
from utils.constants import names_the_show, sanitize_sponsor_label
from utils.cron import is_due, is_valid_expression
from utils.llm_call import call_llm, schema_format_for
from utils.llm_response import extract_json_object
from utils.pattern_catalog import invalidate_pattern_catalog_scope
from utils.pattern_similarity import canonicalize_for_dedupe, similarity
from utils.time import parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger('podcast.pattern_cleanup')

PHASE = 'pattern_cleanup'
_SPLIT_CHILD_FIELDS = (
    'scope', 'text_template', 'sponsor_id', 'sponsor', 'podcast_id', 'network_id',
    'dai_platform', 'intro_variants', 'outro_variants', 'source_language', 'category',
    'created_by', 'protected_from_sync', 'is_active', 'disabled_at', 'disabled_reason',
)
LOCK_FILENAME = '.pattern_cleanup.lock'
# Held briefly around every lock attempt so a status probe never makes a real start fail.
GATE_FILENAME = '.pattern_cleanup.gate.lock'
BATCH_SIZE_RANGE = (1, 200)
UNUSED_DAYS_RANGE = (7, 3650)
CONTEXT_SECONDS = 45.0
RELOCATE_MIN_SCORE = 80
NEAR_SUBSTRING_MIN_SIMILARITY = 0.9
MIN_CONFIDENCE = 0.5
TRIM_MIN_WORDS = 5
TRIM_MIN_FRACTION = 0.10
# Alignment edges can wobble by a character at word boundaries.
OVERLAP_TOLERANCE_CHARS = 2
HIGH_FP_MIN = 2
_MODEL_INPUT_FIELDS = (
    'text_template', 'sponsor_id', 'sponsor', 'intro_variants', 'outro_variants', 'is_active',
)
_STATS_INPUT_FIELDS = (
    *_MODEL_INPUT_FIELDS, 'created_at', 'last_matched_at',
    'confirmation_count', 'false_positive_count',
    # Also guards the pre-transaction source_context computed from the scanned snapshot.
    'podcast_id', 'created_from_episode_id',
)
# Patterns whose review is unusable this many times are parked until a forced run.
INVALID_LIMIT = 3
INVALID_PREFIX = 'invalid:'
# This many failed calls in a row means the route itself is broken, so the run stops.
MAX_CONSECUTIVE_CALL_ERRORS = 3
MAX_TOKENS = 4096
MAX_REASONS = 5
MAX_REASON_CHARS = 200
# A running row older than this is treated as dead rather than probed via the lock.
RUNNING_ROW_MAX_AGE = timedelta(hours=6)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["keep", "trim", "split", "rename"]},
        "text": {"type": ["string", "null"]},
        "sponsor": {"type": ["string", "null"]},
        "pieces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "sponsor": {"type": "string"}},
                "required": ["text", "sponsor"],
            },
        },
        "contaminated": {"type": "boolean"},
        "contamination_reason": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "confidence"],
}


class CleanupInProgressError(Exception):
    """Another cleanup run holds the lock."""


class PatternCleanupCallError(Exception):
    """The review call returned no response."""


class SuggestionNotFoundError(LookupError):
    pass


class SuggestionStateError(Exception):
    """The suggestion's status or its pattern does not allow the action."""


def _clamped_int(db, key: str, bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    return max(lo, min(hi, db.get_setting_int(key, registry_get_default(key))))


def _system_prompt(db) -> str:
    return db.get_setting('pattern_cleanup_prompt') or registry_default('pattern_cleanup_prompt')


def _decode_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _before_snapshot(pattern: dict, context: str | None = None) -> dict:
    before = {
        'text_template': pattern.get('text_template'),
        'sponsor': pattern.get('sponsor'),
        'sponsor_id': pattern.get('sponsor_id'),
        'intro_variants': _decode_list(pattern.get('intro_variants')),
        'outro_variants': _decode_list(pattern.get('outro_variants')),
        'is_active': pattern.get('is_active'),
        'disabled_at': pattern.get('disabled_at'),
        'disabled_reason': pattern.get('disabled_reason'),
        # Refuse stale retire/flag decisions when counters change.
        'created_at': pattern.get('created_at'),
        'last_matched_at': pattern.get('last_matched_at'),
        'confirmation_count': pattern.get('confirmation_count'),
        'false_positive_count': pattern.get('false_positive_count'),
    }
    if context is not None:
        before['source_context'] = context
    return before


def _snapshot_fields(pattern: dict, fields: tuple[str, ...]) -> dict:
    return {field: _decode_list(pattern.get(field)) if field in (
        'intro_variants', 'outro_variants') else pattern.get(field) for field in fields}


def _suggestion_stale_fields(suggestion: dict) -> tuple[str, ...]:
    """Fields that must still match before approving; counters and disabled-state included."""
    fields = ['text_template', 'sponsor_id', 'sponsor']
    kind = suggestion['kind']
    payload = suggestion.get('payload') or {}
    if kind == 'retire':
        fields.extend(('created_at', 'last_matched_at'))
    if kind == 'flag':
        fields.extend(('false_positive_count', 'confirmation_count'))
    if (kind == 'trim'
            or kind == 'flag' and payload.get('recommended') == 'trim'):
        fields.extend(('intro_variants', 'outro_variants'))
    elif (kind in ('retire', 'split')
            or kind == 'flag' and payload.get('recommended') != 'trim'):
        fields.extend(('is_active', 'disabled_at', 'disabled_reason'))
    return tuple(fields)


def _reject_stale_fields(suggestion: dict) -> tuple[str, ...]:
    """Reject only needs the fields that feed the review stamp; counters may have moved."""
    fields = ['text_template', 'sponsor_id', 'sponsor']
    payload = suggestion.get('payload') or {}
    if suggestion['kind'] == 'trim' or payload.get('recommended') == 'trim':
        fields.extend(('intro_variants', 'outro_variants'))
    return tuple(fields)


def _matches_snapshot(pattern: dict, snapshot: dict, fields: tuple[str, ...]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    current = _snapshot_fields(pattern, fields)
    return all(field in snapshot and current[field] == snapshot[field] for field in fields)


def _suggestion_after_fields(suggestion: dict) -> tuple[str, ...]:
    kind = suggestion['kind']
    payload = suggestion.get('payload') or {}
    if kind == 'rename':
        return ('sponsor_id', 'sponsor')
    if kind in ('retire', 'split') or (
            kind == 'flag' and payload.get('recommended') != 'trim'):
        return ('is_active', 'disabled_at', 'disabled_reason')
    if payload.get('sponsor'):
        return ('text_template', 'intro_variants', 'outro_variants', 'sponsor_id', 'sponsor')
    return ('text_template', 'intro_variants', 'outro_variants')


# Selection and stats

def select_candidates(db, *, force: bool, batch_size: int) -> list[dict]:
    """Return learned patterns due for review after any run-level force reset."""
    out = []
    for row in db.get_cleanup_candidate_rows(force=force, batch_size=batch_size):
        if row.pop('has_pending_model', False) and not force:
            continue
        stamp = row.get('cleanup_reviewed_hash')
        if stamp == INVALID_MARKER or stamp == review_hash(row.get('text_template'), row.get('sponsor')):
            continue
        out.append(row)
        if len(out) >= batch_size:
            break
    return out


def _suggestion(kind: str, confidence: float, reasons: list[str], payload: dict) -> dict:
    return {'kind': kind, 'confidence': confidence, 'reasons': reasons, 'payload': payload}


def stats_suggestions(db, pattern: dict, unused_days: int, now=None) -> list[dict]:
    """Retire and high-false-positive flags from counters alone (no LLM)."""
    now = now or utc_now()
    cutoff = now - timedelta(days=unused_days)
    out = []
    created = parse_iso_utc(pattern.get('created_at'))
    last = parse_iso_utc(pattern.get('last_matched_at'))
    if created is not None and created < cutoff and (last is None or last < cutoff):
        reason = (f"No matches in {unused_days} days" if last
                  else f"Never matched in {unused_days} days")
        out.append(_suggestion('retire', 1.0, [reason], {
            'unused_days': unused_days,
            'last_matched_at': pattern.get('last_matched_at'),
            'created_at': pattern.get('created_at'),
            'confirmation_count': pattern.get('confirmation_count') or 0,
        }))
    fp = pattern.get('false_positive_count') or 0
    conf = pattern.get('confirmation_count') or 0
    if fp >= HIGH_FP_MIN and fp >= conf:
        out.append(_suggestion('flag', 1.0, [f"{fp} false positives against {conf} confirmations"], {
            'false_positive_count': fp, 'confirmation_count': conf, 'contaminated': False,
            'contamination_reason': None, 'recommended': 'disable',
        }))
    return out


# Transcript context

def source_context(db, pattern: dict, cache: dict | None = None) -> str | None:
    """Transcript around the pattern in its source episode, span marked [[ ]]; None when unavailable.
    `cache` keeps decoded segments by (slug, episode_id) across one run."""
    slug, episode_id = pattern.get('podcast_id'), pattern.get('created_from_episode_id')
    template = (pattern.get('text_template') or '').strip()
    if not slug or not episode_id or not template:
        return None
    key = (slug, episode_id)
    if cache is not None and key in cache:
        segments = cache[key]
    else:
        try:
            segments = db.get_original_segments(slug, episode_id)
        except (TypeError, ValueError):
            segments = None
        if cache is not None:
            cache[key] = segments
    if not segments:
        return None

    offsets, parts, pos = [], [], 0
    for seg in segments:
        text = (seg.get('text') or '').strip()
        offsets.append((pos, pos + len(text)))
        parts.append(text)
        pos += len(text) + 1
    full = ' '.join(parts)
    lowered = full.lower()
    hay = lowered if len(lowered) == len(full) else full
    needle = template.lower() if hay is lowered else template
    align = fuzz.partial_ratio_alignment(needle, hay, score_cutoff=RELOCATE_MIN_SCORE)
    if not align:
        return None

    hit = [i for i, (s, e) in enumerate(offsets) if s < align.dest_end and e > align.dest_start]
    if not hit:
        return None
    start_t = float(segments[hit[0]].get('start') or 0.0) - CONTEXT_SECONDS
    end_t = float(segments[hit[-1]].get('end') or 0.0) + CONTEXT_SECONDS
    window = [i for i, seg in enumerate(segments)
              if float(seg.get('end') or 0.0) >= start_t and float(seg.get('start') or 0.0) <= end_t]
    lo, hi = offsets[window[0]][0], offsets[window[-1]][1]
    span_lo, span_hi = max(lo, align.dest_start), min(hi, align.dest_end)
    return f"{full[lo:span_lo]}[[{full[span_lo:span_hi]}]]{full[span_hi:hi]}"


# Review

def _snap_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _phrase_re(words: list[str]) -> re.Pattern:
    return re.compile(r'(?<!\w)' + r'\s+'.join(map(re.escape, words)) + r'(?!\w)', re.IGNORECASE)


def _has_phrase(phrase: str | None, text: str) -> bool:
    """Whole-word, case-insensitive phrase match."""
    words = (phrase or '').split()
    return bool(words) and _phrase_re(words).search(text or '') is not None


def _tighten(original: str, start: int, end: int, frag: str) -> tuple[int, int]:
    """Shrink the window to the fragment's opening and closing words."""
    words = frag.split()
    window = original[start:end]
    n = min(3, len(words))
    first = _phrase_re(words[:n]).search(window) or _phrase_re(words[:1]).search(window)
    lasts = (list(_phrase_re(words[-n:]).finditer(window))
             or list(_phrase_re(words[-1:]).finditer(window)))
    if first and lasts and lasts[-1].end() > first.start():
        return start + first.start(), start + lasts[-1].end()
    return start, end


def _locate(fragment, original: str) -> tuple[int, int, str] | None:
    """(start, end, exact original window) when `fragment` is a near-substring of `original`."""
    if not isinstance(fragment, str):
        return None
    frag = ' '.join(fragment.split())
    if not frag or not original:
        return None
    lowered = original.lower()
    hay = lowered if len(lowered) == len(original) else original
    align = fuzz.partial_ratio_alignment(frag.lower() if hay is lowered else frag, hay)
    if not align:
        return None
    start, end = _snap_to_words(original, align.dest_start, align.dest_end)
    start, end = _tighten(original, start, end, frag)
    window = original[start:end]
    if similarity(canonicalize_for_dedupe(frag),
                  canonicalize_for_dedupe(window)) < NEAR_SUBSTRING_MIN_SIMILARITY:
        return None
    return start, end, window


def _norm(text: str) -> str:
    return ' '.join((text or '').lower().split())


def _short(value) -> str | None:
    return value.strip()[:MAX_REASON_CHARS] if isinstance(value, str) and value.strip() else None


def _clean_sponsor(raw, pattern: dict) -> str | None:
    slug = pattern.get('podcast_id')
    label = sanitize_sponsor_label(raw, show_name=pattern.get('podcast_title') or slug)
    if not label or names_the_show(label, slug):
        return None
    return sanitize_sponsor_name(label)


def _keeps_sponsor(pattern: dict, original: str, texts: list[str]) -> bool:
    """When the original names its sponsor, at least one kept text must too."""
    sponsor = pattern.get('sponsor')
    if not _has_phrase(sponsor, original):
        return True
    return any(_has_phrase(sponsor, t) for t in texts)


def _trim_keeps_sponsor(pattern: dict, original: str, kept: str, sponsors) -> bool:
    """A trim keeps the recorded sponsor, or some known sponsor when the text does not name it."""
    if _has_phrase(pattern.get('sponsor'), original):
        return _has_phrase(pattern.get('sponsor'), kept)
    if sponsors is None:
        sponsors = SponsorService(Database())
    return sponsors.find_sponsor_in_text(kept) is not None


def _validate_pieces(pieces, original: str, pattern: dict | None = None) -> list[dict] | None:
    if not isinstance(pieces, list) or len(pieces) < 2:
        return None
    pattern = pattern or {}
    located = []
    for piece in pieces:
        if not isinstance(piece, dict):
            return None
        loc = _locate(piece.get('text'), original)
        sponsor = _clean_sponsor(piece.get('sponsor'), pattern)
        if (loc is None or sponsor is None or len(loc[2]) < MIN_TEXT_LENGTH
                or not _has_phrase(sponsor, loc[2])):
            return None
        located.append((loc[0], loc[1], {'text': loc[2], 'sponsor': sponsor}))
    located.sort(key=lambda item: item[0])
    for prev, nxt in zip(located, located[1:], strict=False):
        if nxt[0] < prev[1] - OVERLAP_TOLERANCE_CHARS:
            return None
    if not _keeps_sponsor(pattern, original, [item[2]['text'] for item in located]):
        return None
    return [item[2] for item in located]


def validate_review(pattern: dict, raw: dict, sponsors=None) -> dict | None:
    """Gate the model's answer; None means no suggestion. Stored text is always an exact original window."""
    original = pattern.get('text_template') or ''
    pid = pattern.get('id')
    raw_confidence = raw.get('confidence')
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        logger.warning("pattern_cleanup: pattern %s review has invalid confidence", pid)
        return None
    try:
        confidence = float(raw_confidence)
    except OverflowError:
        logger.warning("pattern_cleanup: pattern %s review has invalid confidence", pid)
        return None
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        logger.warning("pattern_cleanup: pattern %s review has invalid confidence", pid)
        return None
    if confidence < MIN_CONFIDENCE:
        logger.info("pattern_cleanup: pattern %s review dropped at confidence %.2f", pid, confidence)
        return None
    contaminated = raw.get('contaminated') is True
    raw_reasons = raw.get('reasons') if isinstance(raw.get('reasons'), list) else []
    reasons = [r for r in map(_short, raw_reasons) if r][:MAX_REASONS]
    verdict = {
        'action': 'keep', 'text': None, 'sponsor': None, 'pieces': [],
        'contaminated': contaminated,
        'contamination_reason': _short(raw.get('contamination_reason')) if contaminated else None,
        'confidence': round(confidence, 3), 'reasons': reasons,
    }
    action = str(raw.get('action') or '').strip().lower()
    if action == 'keep':
        return verdict
    if action == 'trim':
        loc = _locate(raw.get('text'), original)
        if loc is None or len(loc[2]) < MIN_TEXT_LENGTH:
            logger.warning("pattern_cleanup: pattern %s trim failed validation", pid)
            return verdict if contaminated else None
        sponsor = None
        if raw.get('sponsor') is not None:
            sponsor = _clean_sponsor(raw.get('sponsor'), pattern)
            if sponsor is None or not _has_phrase(sponsor, loc[2]):
                logger.warning("pattern_cleanup: pattern %s trim sponsor failed validation", pid)
                return verdict if contaminated else None
            if _norm(sponsor) == _norm(pattern.get('sponsor')):
                sponsor = None
        if sponsor is None and not _trim_keeps_sponsor(pattern, original, loc[2], sponsors):
            logger.warning("pattern_cleanup: pattern %s trim failed sponsor validation", pid)
            return verdict if contaminated else None
        total = len(original.split())
        removed = total - len(loc[2].split())
        if removed < TRIM_MIN_WORDS and removed < total * TRIM_MIN_FRACTION:
            if sponsor is not None:
                verdict.update(action='rename', sponsor=sponsor)
            return verdict
        verdict.update(action='trim', text=loc[2], sponsor=sponsor)
        return verdict
    if action == 'split':
        pieces = _validate_pieces(raw.get('pieces'), original, pattern)
        if pieces is None:
            logger.warning("pattern_cleanup: pattern %s split pieces failed validation", pid)
            return verdict if contaminated else None
        verdict.update(action='split', pieces=pieces)
        return verdict
    if action == 'rename':
        sponsor = _clean_sponsor(raw.get('sponsor'), pattern)
        if sponsor is None or not _has_phrase(sponsor, original):
            logger.warning("pattern_cleanup: pattern %s rename names a sponsor not in the text", pid)
            return verdict if contaminated else None
        if _norm(sponsor) == _norm(pattern.get('sponsor')):
            return verdict
        verdict.update(action='rename', sponsor=sponsor)
        return verdict
    logger.warning("pattern_cleanup: pattern %s review returned unknown action %r", pid, action)
    return verdict if contaminated else None


def _user_prompt(pattern: dict, context: str | None) -> str:
    body = (f"Sponsor on record: {pattern.get('sponsor') or 'unknown'}\n\n"
            f"Pattern text:\n<<<\n{pattern.get('text_template') or ''}\n>>>\n\n")
    if context:
        return body + f"Transcript context:\n<<<\n{context}\n>>>"
    return body + "No transcript context is available; judge from the pattern text alone."


def review_pattern(pattern: dict, context: str | None, *, live: LiveRoute,
                   system_prompt: str | None = None, sponsors=None) -> dict | None:
    """One LLM review of a pattern, validated; None when the answer is unusable."""
    if system_prompt is None:
        system_prompt = _system_prompt(Database())
    response, error = call_llm(
        llm_client=client_for_route(live.route),
        model=live.model,
        system_prompt=system_prompt,
        prompt=_user_prompt(pattern, context),
        llm_timeout=live.timeout,
        max_retries=live.max_retries,
        max_tokens=MAX_TOKENS,
        slug=None,
        episode_id=None,
        call_label=f"pattern cleanup {pattern.get('id')}",
        phase_key=PHASE,
        route_phase=PHASE,
        provider=live.provider,
        credential_slot=live.credential_slot,
        response_format=schema_format_for(
            live.model, 'pattern_cleanup', REVIEW_SCHEMA,
            'Cleanup verdict for one learned ad pattern.', provider=live.provider),
    )
    if response is None:
        if isinstance(error, ProviderRateLimitedError):
            raise error
        raise PatternCleanupCallError(str(error) if error else 'no response')
    parsed, _ = extract_json_object(getattr(response, 'content', '') or '')
    if not isinstance(parsed, dict):
        logger.warning("pattern_cleanup: pattern %s review was not a JSON object", pattern.get('id'))
        return None
    return validate_review(pattern, parsed, sponsors)


# Run

def _live_route(original_route) -> LiveRoute:
    return live_route_from(apply_failover(original_route))


def _suggestions_for(pattern: dict, stats: list[dict], verdict: dict | None) -> list[dict]:
    """Merge stats suggestions with the model's verdict."""
    out = list(stats)
    if verdict is None:
        return out
    flag = next((s for s in out if s['kind'] == 'flag'), None)
    reasons = list(verdict['reasons'])
    if verdict['contaminated'] and verdict['contamination_reason']:
        reasons.append(verdict['contamination_reason'])
    if verdict['contaminated']:
        if flag is None:
            flag = _suggestion('flag', verdict['confidence'], reasons, {
                'false_positive_count': pattern.get('false_positive_count') or 0,
                'confirmation_count': pattern.get('confirmation_count') or 0,
                'contaminated': True,
                'contamination_reason': verdict['contamination_reason'],
                'recommended': 'disable',
            })
            out.append(flag)
        else:
            flag['payload'].update(contaminated=True, recommended='disable',
                                   contamination_reason=verdict['contamination_reason'])
            flag['reasons'] += [reason for reason in reasons if reason not in flag['reasons']]
    action = verdict['action']
    if action == 'trim' and verdict['contaminated']:
        return out
    if action == 'rename' and verdict['contaminated']:
        return out
    if action == 'trim' and flag is not None:
        flag['payload'].update(recommended='trim', trim_text=verdict['text'])
        if verdict.get('sponsor'):
            flag['payload']['sponsor'] = verdict['sponsor']
        flag['reasons'] += [r for r in reasons if r not in flag['reasons']]
    elif action == 'trim':
        payload = {'text': verdict['text']}
        if verdict.get('sponsor'):
            payload['sponsor'] = verdict['sponsor']
        out.append(_suggestion('trim', verdict['confidence'], reasons, payload))
    elif action == 'split':
        out.append(_suggestion('split', verdict['confidence'], reasons, {'pieces': verdict['pieces']}))
    elif action == 'rename':
        out.append(_suggestion('rename', verdict['confidence'], reasons, {'sponsor': verdict['sponsor']}))
    return out


def _record_invalid(db, pattern: dict, conn) -> None:
    """Count an unusable review; after INVALID_LIMIT the pattern is parked until a forced run."""
    prior = pattern.get('cleanup_reviewed_hash') or ''
    tail = prior[len(INVALID_PREFIX):] if prior.startswith(INVALID_PREFIX) else ''
    count = int(tail) + 1 if tail.isdigit() else 1
    marker = INVALID_MARKER if count >= INVALID_LIMIT else f'{INVALID_PREFIX}{count}'
    db.stamp_pattern_cleanup_reviewed(pattern['id'], marker, conn=conn)


def _record_failure(db, pattern: dict) -> None:
    """Count a failed review like an unusable one so the pattern rotates back and parks."""
    try:
        with db.transaction(immediate=True) as conn:
            current = _pattern_on(conn, pattern['id'])
            if current is not None:
                _supersede_obsolete_model_pending(conn, current)
                if (_same_model_input(pattern, current)
                        and _same_review_metadata(pattern, current)):
                    _record_invalid(db, current, conn)
    except Exception as e:
        logger.warning("pattern_cleanup: pattern %s failure not recorded: %s", pattern['id'], e)
        db.clear_leaked_transaction(logger, 'pattern cleanup')


def _same_fields(expected: dict, current: dict, fields: tuple[str, ...]) -> bool:
    return _matches_snapshot(current, _snapshot_fields(expected, fields), fields)


def _same_model_input(expected: dict, current: dict) -> bool:
    return _same_fields(expected, current, _MODEL_INPUT_FIELDS)


def _same_suggestion_content(suggestion: dict, current: dict) -> bool:
    fields = [field for field in _suggestion_stale_fields(suggestion)
              if field not in ('false_positive_count', 'confirmation_count')]
    return _matches_snapshot(current, suggestion.get('before') or {}, tuple(fields))


def _obsolete_model_version(suggestion: dict, current: dict) -> bool:
    before = suggestion.get('before') or {}
    return ('text_template' in before and 'sponsor' in before
            and review_hash(before.get('text_template'), before.get('sponsor'))
            != review_hash(current.get('text_template'), current.get('sponsor')))


def _same_stats_input(expected: dict, current: dict) -> bool:
    return _same_fields(expected, current, _STATS_INPUT_FIELDS)


def _same_review_metadata(expected: dict, current: dict) -> bool:
    return all(expected.get(field) == current.get(field) for field in (
        'cleanup_reviewed_at', 'cleanup_reviewed_hash', 'cleanup_stats_reviewed'))


def _stats_evidence(kind: str, pattern: dict, payload: dict) -> str | None:
    if kind == 'retire':
        anchor = (payload.get('last_matched_at') or payload.get('created_at')
                  or pattern.get('created_at'))
        threshold = payload.get('unused_days')
        if anchor is None or threshold is None:
            return None
        evidence = {'inactive_since': anchor, 'unused_days': threshold}
    elif kind == 'flag':
        false_positives = payload.get('false_positive_count')
        if not isinstance(false_positives, int) or isinstance(false_positives, bool):
            return None
        evidence = {'false_positive_count': false_positives}
    else:
        return None
    return stats_evidence_hash(kind, pattern.get('text_template'), pattern.get('sponsor'), evidence)


def _stats_acknowledgments(db, conn, pattern: dict) -> dict:
    acknowledgments = db.get_cleanup_stats_reviewed(pattern['id'], conn=conn)
    if acknowledgments is not None:
        return acknowledgments
    acknowledgments = {}
    for decision in db.get_cleanup_stat_decisions(pattern['id'], conn=conn):
        before = decision.get('before') or {}
        if ('text_template' not in before or 'sponsor' not in before
                or review_hash(before.get('text_template'), before.get('sponsor'))
                != review_hash(pattern.get('text_template'), pattern.get('sponsor'))):
            continue
        payload = decision.get('payload') or {}
        history_before = dict(before)
        history_before.setdefault('created_at', pattern.get('created_at'))
        history_before.setdefault('last_matched_at', payload.get('last_matched_at'))
        fingerprint = _stats_evidence(decision['kind'], history_before, payload)
        if fingerprint is not None:
            acknowledgments[decision['kind']] = fingerprint
    db.set_cleanup_stats_reviewed(pattern['id'], acknowledgments, conn=conn)
    return acknowledgments


def _acknowledge_stats_decision(db, conn, suggestion: dict, pattern: dict) -> None:
    kind = suggestion['kind']
    if kind not in ('retire', 'flag'):
        return
    before = suggestion.get('before') or {}
    payload = suggestion.get('payload') or {}
    fingerprint = _stats_evidence(kind, before, payload)
    if fingerprint is None:
        return
    acknowledgments = _stats_acknowledgments(db, conn, pattern)
    acknowledgments[kind] = fingerprint
    db.set_cleanup_stats_reviewed(pattern['id'], acknowledgments, conn=conn)


def _decode_pending_rows(rows) -> list[dict]:
    decoded = []
    for row in rows:
        item = dict(row)
        for field in ('reasons', 'payload', 'before', 'applied'):
            item[field] = json.loads(item[field]) if item.get(field) else None
        decoded.append(item)
    return decoded


def _stats_only_suggestion(suggestion: dict) -> bool:
    if suggestion['kind'] == 'retire':
        return True
    payload = suggestion.get('payload') or {}
    return (suggestion['kind'] == 'flag' and not payload.get('contaminated')
            and payload.get('recommended') != 'trim')


def _pending_pattern_suggestions(conn, pattern_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM pattern_cleanup_suggestions WHERE pattern_id = ? AND status = 'pending'",
        (pattern_id,)).fetchall()
    return _decode_pending_rows(rows)


def _supersede_obsolete_model_pending(conn, pattern: dict) -> None:
    for suggestion in _pending_pattern_suggestions(conn, pattern['id']):
        if (not _stats_only_suggestion(suggestion)
                and _obsolete_model_version(suggestion, pattern)):
            conn.execute("DELETE FROM pattern_cleanup_suggestions WHERE id = ?",
                         (suggestion['id'],))


def _is_false_positive_reason(reason: str) -> bool:
    return (isinstance(reason, str)
            and re.fullmatch(r'\d+ false positives against \d+ confirmations', reason) is not None)


def _run_stats_sweep(db, run_id: int, unused_days: int, segments_cache: dict) -> set[tuple[int, str]]:
    """Persist new statistical evidence independently from model-review eligibility."""
    changed = set()
    for scanned in db.get_cleanup_stats_rows():
        try:
            changed |= _sweep_one_pattern(db, run_id, scanned, unused_days, segments_cache)
        except Exception:
            logger.exception("pattern_cleanup: stats sweep failed for pattern %s", scanned.get('id'))
            db.clear_leaked_transaction(logger, 'pattern cleanup stats sweep')
    return changed


def _stats_proposal_needs_write(kind: str, proposal: dict, pattern: dict,
                                acknowledgments: dict, pending: dict) -> bool:
    """Whether a new or updated stats-only suggestion of `kind` should be stored."""
    fingerprint = _stats_evidence(kind, pattern, proposal['payload'])
    if fingerprint is None or acknowledgments.get(kind) == fingerprint:
        return False
    existing = pending.get(kind)
    if existing is not None and not _stats_only_suggestion(existing):
        return False
    if existing is None:
        return True
    return (_stats_evidence(kind, existing.get('before') or {}, existing.get('payload') or {})
            != fingerprint
            or not _matches_snapshot(pattern, existing.get('before') or {},
                                     _suggestion_stale_fields(existing)))


def _flag_merge_plan(model_flag: dict | None, candidates: dict, pattern: dict,
                     acknowledgments: dict) -> dict | None:
    """None when the model flag's counters already match; else the fields to upsert."""
    if (model_flag is None or _stats_only_suggestion(model_flag)
            or not _same_suggestion_content(model_flag, pattern)):
        return None
    proposal = candidates.get('flag')
    payload = dict(model_flag.get('payload') or {})
    payload.update({
        'false_positive_count': pattern.get('false_positive_count') or 0,
        'confirmation_count': pattern.get('confirmation_count') or 0,
    })
    reasons = [reason for reason in model_flag.get('reasons') or []
               if not _is_false_positive_reason(reason)]
    fingerprint = _stats_evidence('flag', pattern, proposal['payload']) if proposal else None
    if proposal is not None and acknowledgments.get('flag') != fingerprint:
        reasons.extend(reason for reason in proposal['reasons'] if reason not in reasons)
    old_before = model_flag.get('before') or {}
    stale_fields = _suggestion_stale_fields(model_flag)
    if (_matches_snapshot(pattern, old_before, stale_fields)
            and payload == model_flag.get('payload') and reasons == model_flag.get('reasons')):
        return None
    return {
        'payload': payload, 'reasons': reasons,
        'confidence': max(model_flag.get('confidence') or 0,
                          proposal['confidence'] if proposal else 0),
        'source_context': old_before.get('source_context'),
    }


def _plan_stats_sweep(pattern: dict, candidates: dict, all_pending: list[dict],
                      acknowledgments: dict) -> tuple[set[int], dict | None, dict]:
    """What the stats sweep would change for `pattern`, without touching the DB: ids to
    delete, a flag-merge plan (or None), and the kinds that need a new or updated write."""
    pending = {item['kind']: item for item in all_pending if item['kind'] in ('retire', 'flag')}
    to_delete = set()
    for existing in all_pending:
        if not _stats_only_suggestion(existing) and _obsolete_model_version(existing, pattern):
            to_delete.add(existing['id'])
            pending.pop(existing['kind'], None)
    for kind, existing in list(pending.items()):
        proposal = candidates.get(kind)
        if _stats_only_suggestion(existing) and (
                proposal is None or acknowledgments.get(kind) ==
                _stats_evidence(kind, pattern, proposal['payload'])):
            to_delete.add(existing['id'])
            pending.pop(kind)
    flag_merge = _flag_merge_plan(pending.get('flag'), candidates, pattern, acknowledgments)
    to_write = {kind: proposal for kind, proposal in candidates.items()
               if _stats_proposal_needs_write(kind, proposal, pattern, acknowledgments, pending)}
    return to_delete, flag_merge, to_write


def _sweep_one_pattern(db, run_id: int, scanned: dict, unused_days: int,
                       segments_cache: dict) -> set[tuple[int, str]]:
    """One pattern's share of the stats sweep; a read-only precheck decides whether a
    write transaction (and the transcript lookup it would otherwise hold open) is needed."""
    changed = set()
    candidates = {item['kind']: item for item in stats_suggestions(db, scanned, unused_days)}
    conn = db.get_connection()
    all_pending = _pending_pattern_suggestions(conn, scanned['id'])
    if not candidates and not all_pending:
        return changed
    # Read-only: a not-yet-backfilled cache (None) is treated as "nothing acknowledged",
    # which can only make this check over-eager about needing context, never under-eager.
    acknowledgments = db.get_cleanup_stats_reviewed(scanned['id'], conn=conn) or {}
    # _same_stats_input is checked again below with the fresh row; computed from `scanned`
    # here, which is safe because that guard proves text_template/sponsor/source fields match.
    to_delete, flag_merge, to_write = _plan_stats_sweep(scanned, candidates, all_pending, acknowledgments)
    if not to_delete and flag_merge is None and not to_write:
        return changed
    context = source_context(db, scanned, segments_cache) if to_write else None
    with db.transaction(immediate=True) as conn:
        current = _pattern_on(conn, scanned['id'])
        if current is None or not _same_stats_input(scanned, current):
            return changed
        acknowledgments = _stats_acknowledgments(db, conn, current)
        all_pending = _pending_pattern_suggestions(conn, current['id'])
        to_delete, flag_merge, to_write = _plan_stats_sweep(current, candidates, all_pending, acknowledgments)
        for suggestion_id in to_delete:
            conn.execute("DELETE FROM pattern_cleanup_suggestions WHERE id = ?", (suggestion_id,))
        if flag_merge is not None:
            before = _before_snapshot(current, flag_merge['source_context'])
            db.upsert_cleanup_suggestion(
                run_id, current['id'], 'flag', flag_merge['confidence'], flag_merge['reasons'],
                flag_merge['payload'], before, conn=conn)
            changed.add((current['id'], 'flag'))
        for kind, proposal in to_write.items():
            before = _before_snapshot(current, context)
            db.upsert_cleanup_suggestion(
                run_id, current['id'], kind, proposal['confidence'], proposal['reasons'],
                proposal['payload'], before, conn=conn)
            changed.add((current['id'], kind))
    return changed


def _process_pattern(db, run_id: int, pattern: dict, unused_days: int, live: LiveRoute,
                     system_prompt: str, sponsors=None,
                     segments_cache: dict | None = None) -> tuple[set[str], bool]:
    """Return stored kinds and whether the review became stale."""
    context = source_context(db, pattern, segments_cache)
    verdict = review_pattern(pattern, context, live=live, system_prompt=system_prompt,
                             sponsors=sponsors)
    with db.transaction(immediate=True) as conn:
        current = _pattern_on(conn, pattern['id'])
        if current is None:
            return set(), True
        if not _same_model_input(pattern, current):
            _supersede_obsolete_model_pending(conn, current)
            return set(), True
        if not _same_review_metadata(pattern, current):
            return set(), True
        _supersede_obsolete_model_pending(conn, current)
        acknowledgments = db.get_cleanup_stats_reviewed(current['id'], conn=conn) or {}
        stats = [item for item in stats_suggestions(db, current, unused_days)
                 if acknowledgments.get(item['kind']) != _stats_evidence(
                     item['kind'], current, item['payload'])]
        suggestions = _suggestions_for(current, stats, verdict)
        before = _before_snapshot(current, context)
        for s in suggestions:
            db.upsert_cleanup_suggestion(run_id, current['id'], s['kind'], s['confidence'],
                                         s['reasons'], s['payload'], before, conn=conn)
        if verdict is None:
            _record_invalid(db, current, conn)
        else:
            db.stamp_pattern_cleanup_reviewed(
                current['id'], review_hash(current.get('text_template'), current.get('sponsor')),
                conn=conn)
    return {item['kind'] for item in suggestions}, False


@contextmanager
def _lock_gate(db):
    with open(Path(db.data_dir) / GATE_FILENAME, 'w') as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        yield


def _try_run_lock(db):
    """The run lock's fd when it was free, else None; call inside _lock_gate."""
    fd = open(Path(db.data_dir) / LOCK_FILENAME, 'w')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    return fd


def is_cleanup_running(db) -> bool:
    """Cheap by default: the run table alone answers the common idle case;
    the lock is only flocked to confirm a running row that still looks live."""
    runs = db.get_cleanup_runs(limit=1)
    if not runs or runs[0]['status'] != 'running':
        return False
    started = parse_iso_utc(runs[0]['started_at'])
    if started is not None and utc_now() - started > RUNNING_ROW_MAX_AGE:
        return False
    with _lock_gate(db):
        fd = _try_run_lock(db)
        if fd is None:
            return True
        fd.close()
        return False


def _begin_run(db, force: bool, trigger: str):
    """(lock fd, run id, started_at) with the lock held, or None when another run holds it."""
    with _lock_gate(db):
        fd = _try_run_lock(db)
    if fd is None:
        return None
    try:
        # The lock is ours, so any row still marked running died with its process.
        db.fail_running_cleanup_runs('interrupted')
        started_at = utc_now_iso()
        run_id = db.create_cleanup_run(forced=force, trigger=trigger, started_at=started_at)
    except Exception:
        fd.close()
        raise
    return fd, run_id, started_at


def _finish(db, run_id: int, summary: dict, live: LiveRoute | None) -> None:
    """Persist the run outcome; a failed write is logged and the row marked failed best effort."""
    try:
        db.finish_cleanup_run(
            run_id, status=summary['status'], reviewed=summary['reviewed'],
            suggested=summary['suggested'], skipped=summary['skipped'], error=summary['error'],
            model=live.model if live else None, provider=live.provider if live else None,
            credential_slot=live.credential_slot if live else None,
            error_count=summary['errors'])
    except Exception as e:
        logger.warning("pattern_cleanup: run %s status write failed: %s", run_id, e)
        db.clear_leaked_transaction(logger, 'pattern cleanup')
        summary.update(status='failed', error=f'status write failed: {e}')
        try:
            db.finish_cleanup_run(run_id, status='failed', reviewed=summary['reviewed'],
                                  suggested=summary['suggested'], skipped=summary['skipped'],
                                  error=summary['error'], error_count=summary['errors'])
        except Exception as again:
            logger.warning("pattern_cleanup: run %s could not be marked failed: %s", run_id, again)


def _execute_run(db, run_id: int, started_at: str, *, force: bool, trigger: str) -> dict:
    """Body of a run; the caller holds the lock and created the run row."""
    start = time.monotonic()
    counts = {'reviewed': 0, 'suggested': 0, 'skipped': 0, 'errors': 0}
    suggestion_keys = set()
    original_live = None
    status, error = 'completed', None
    try:
        if force:
            db.reset_cleanup_force_state()
        batch_size = _clamped_int(db, 'pattern_cleanup_batch_size', BATCH_SIZE_RANGE)
        unused_days = _clamped_int(db, 'pattern_cleanup_unused_days', UNUSED_DAYS_RANGE)
        segments_cache: dict = {}
        suggestion_keys.update(_run_stats_sweep(db, run_id, unused_days, segments_cache))
        original_route = resolve_route(PHASE)
        original_live = live_route_from(original_route)
        system_prompt = _system_prompt(db)
        sponsors = SponsorService(db)
        call_errors = 0
        for pattern in select_candidates(db, force=force, batch_size=batch_size):
            live = _live_route(original_route)
            try:
                stored, skipped = _process_pattern(
                    db, run_id, pattern, unused_days, live, system_prompt,
                    sponsors=sponsors, segments_cache=segments_cache)
            except ProviderRateLimitedError:
                raise
            except Exception as e:
                counts['errors'] += 1
                logger.warning("pattern_cleanup: pattern %s review failed: %s", pattern['id'], e)
                db.clear_leaked_transaction(logger, 'pattern cleanup')
                _record_failure(db, pattern)
                call_errors = call_errors + 1 if isinstance(e, PatternCleanupCallError) else 0
                if call_errors >= MAX_CONSECUTIVE_CALL_ERRORS:
                    raise PatternCleanupCallError(
                        f'{call_errors} review calls failed in a row: {e}') from e
                continue
            call_errors = 0
            counts['reviewed'] += 1
            suggestion_keys.update((pattern['id'], kind) for kind in stored)
            counts['skipped'] += int(skipped)
    except Exception as e:
        status, error = 'failed', str(e) or type(e).__name__
        logger.warning("pattern_cleanup: run %s failed: %s", run_id, error)
        db.clear_leaked_transaction(logger, 'pattern cleanup')

    counts['suggested'] = len(suggestion_keys)
    summary = {
        'runId': run_id, 'status': status, 'trigger': trigger, 'forced': force, **counts,
        'model': original_live.model if original_live else None,
        'provider': original_live.provider if original_live else None,
        'credentialSlot': original_live.credential_slot if original_live else None,
        'startedAt': started_at, 'finishedAt': utc_now_iso(),
        'durationMs': int((time.monotonic() - start) * 1000), 'error': error,
    }
    _finish(db, run_id, summary, original_live)
    logger.info("pattern_cleanup: run %s %s: %s", run_id, summary['status'], counts)
    return summary


def run_cleanup(db, *, force: bool = False, trigger: str = 'schedule') -> dict:
    """Review one batch of learned patterns and store suggestions. Returns the run summary."""
    begun = _begin_run(db, force, trigger)
    if begun is None:
        raise CleanupInProgressError('a pattern cleanup run is already in progress')
    fd, run_id, started_at = begun
    try:
        return _execute_run(db, run_id, started_at, force=force, trigger=trigger)
    finally:
        fd.close()


def start_cleanup_run(db, *, force: bool = False, trigger: str = 'schedule') -> int | None:
    """Start a run in a daemon thread; the run id, or None when another run holds the lock."""
    begun = _begin_run(db, force, trigger)
    if begun is None:
        return None
    fd, run_id, started_at = begun

    def work():
        try:
            _execute_run(db, run_id, started_at, force=force, trigger=trigger)
        except Exception:
            logger.exception("pattern_cleanup: run %s crashed", run_id)
        finally:
            fd.close()

    try:
        threading.Thread(target=work, name=f'pattern-cleanup-{run_id}', daemon=True).start()
    except Exception:
        fd.close()
        db.finish_cleanup_run(run_id, status='failed', reviewed=0, suggested=0, skipped=0,
                              error='could not start the run thread')
        raise
    return run_id


def _busy_slots() -> int:
    return ProcessingQueue().slot_count()


def _schedule_reference(db):
    """The later of the last run's start and the moment scheduling was last enabled."""
    runs = db.get_cleanup_runs(limit=1)
    points = [parse_iso_utc(runs[0]['started_at']) if runs else None,
              parse_iso_utc(db.get_setting('pattern_cleanup_schedule_anchor'))]
    points = [p for p in points if p is not None]
    return max(points) if points else None


def pattern_cleanup_tick(db) -> int | None:
    """Start a background run when enabled, due by cron, and no episode is processing."""
    if not db.get_setting_bool('pattern_cleanup_enabled', default=False):
        return None
    cron = db.get_setting('pattern_cleanup_cron') or registry_default('pattern_cleanup_cron')
    if not is_valid_expression(cron):
        logger.warning("pattern_cleanup: invalid cron %r, using the default", cron)
        cron = registry_default('pattern_cleanup_cron')
    reference = _schedule_reference(db)
    if reference is not None and not is_due(cron, reference, utc_now()):
        return None
    if _busy_slots() > 0:
        logger.debug("pattern_cleanup: deferred, an episode is processing")
        return None
    run_id = start_cleanup_run(db, trigger='schedule')
    if run_id is None:
        logger.info("pattern_cleanup: skipped, another run is in progress")
    return run_id


# Approve, reject, undo

def _load(db, conn, suggestion_id: int, required_status: str) -> dict:
    suggestion = db.get_cleanup_suggestion(suggestion_id, conn=conn)
    if suggestion is None:
        raise SuggestionNotFoundError(f'suggestion {suggestion_id} not found')
    if suggestion['status'] != required_status:
        raise SuggestionStateError(
            f"suggestion {suggestion_id} is {suggestion['status']}, not {required_status}")
    return suggestion


def _pattern_on(conn, pattern_id: int) -> dict | None:
    row = conn.execute(
        "SELECT ap.*, ks.name AS sponsor FROM ad_patterns ap "
        "LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id WHERE ap.id = ?",
        (pattern_id,)).fetchone()
    return dict(row) if row else None


def _apply_trim(db, conn, pattern: dict, text: str, sponsor_name: str | None = None) -> dict:
    intro, outro = derive_intro_outro(text)
    sponsor_id = pattern.get('sponsor_id')
    sponsor = pattern.get('sponsor')
    if sponsor_name:
        sponsor_name = _clean_sponsor(sponsor_name, pattern)
        if sponsor_name is None or not _has_phrase(sponsor_name, text):
            raise SuggestionStateError('the corrected sponsor is not present in the trimmed text')
        sponsor_id = get_or_create_known_sponsor(db, sponsor_name, conn=conn)
        if sponsor_id is None:
            raise SuggestionStateError(f'sponsor name {sponsor_name!r} is not valid')
        row = conn.execute("SELECT name FROM known_sponsors WHERE id = ?", (sponsor_id,)).fetchone()
        sponsor = row['name']
    db._update_ad_pattern_conn(conn, pattern['id'], text_template=text,
                               intro_variants=intro, outro_variants=outro,
                               **({'sponsor_id': sponsor_id} if sponsor_name else {}))
    db.stamp_pattern_cleanup_reviewed(pattern['id'], review_hash(text, sponsor), conn=conn)
    applied = {'new_pattern_ids': [], 'disabled_pattern_id': None, 'text': text}
    if sponsor_name:
        applied['sponsor_id'] = sponsor_id
    return applied


def _apply_disable(db, conn, pattern: dict, reason: str) -> dict:
    db._update_ad_pattern_conn(conn, pattern['id'], is_active=0, disabled_reason=reason)
    db.stamp_pattern_cleanup_reviewed(
        pattern['id'], review_hash(pattern.get('text_template'), pattern.get('sponsor')), conn=conn)
    return {'new_pattern_ids': [], 'disabled_pattern_id': pattern['id']}


def _apply_rename(db, conn, pattern: dict, name: str) -> dict:
    sponsor_id = get_or_create_known_sponsor(db, name, conn=conn)
    if sponsor_id is None:
        raise SuggestionStateError(f'sponsor name {name!r} is not valid')
    db._update_ad_pattern_conn(conn, pattern['id'], sponsor_id=sponsor_id)
    canonical = conn.execute("SELECT name FROM known_sponsors WHERE id = ?", (sponsor_id,)).fetchone()
    db.stamp_pattern_cleanup_reviewed(
        pattern['id'], review_hash(pattern.get('text_template'), canonical['name']), conn=conn)
    return {'new_pattern_ids': [], 'disabled_pattern_id': None, 'sponsor_id': sponsor_id}


def _apply_split(db, conn, pattern: dict, pieces: list[dict]) -> dict:
    new_ids = []
    new_states = []
    for piece in pieces:
        sponsor_id = get_or_create_known_sponsor(db, piece['sponsor'], conn=conn)
        if sponsor_id is None:
            raise SuggestionStateError(f"sponsor name {piece['sponsor']!r} is not valid")
        intro, outro = derive_intro_outro(piece['text'])
        new_id = db._create_ad_pattern_conn(
            conn,
            scope=pattern['scope'],
            text_template=piece['text'],
            sponsor_id=sponsor_id,
            podcast_id=pattern['podcast_id'],
            network_id=pattern['network_id'],
            dai_platform=pattern['dai_platform'],
            intro_variants=intro,
            outro_variants=outro,
            created_from_episode_id=pattern['created_from_episode_id'],
            created_by=pattern['created_by'] or 'auto',
            protected_from_sync=pattern['protected_from_sync'] or 0,
            source_language=pattern['source_language'],
            category=pattern['category'],
        )
        if not new_id:
            raise RuntimeError('split did not create every pattern')
        child = _pattern_on(conn, new_id)
        db.stamp_pattern_cleanup_reviewed(
            new_id, review_hash(piece['text'], child['sponsor']), conn=conn)
        new_ids.append(new_id)
        new_states.append({'id': new_id, 'fields': _snapshot_fields(child, _SPLIT_CHILD_FIELDS)})
    disabled_at = utc_now_iso()
    disabled_reason = f'Cleanup split into patterns: {new_ids}'
    db._update_ad_pattern_conn(conn, pattern['id'], is_active=0, disabled_at=disabled_at,
                               disabled_reason=disabled_reason)
    db.stamp_pattern_cleanup_reviewed(
        pattern['id'], review_hash(pattern.get('text_template'), pattern.get('sponsor')), conn=conn)
    return {
        'new_pattern_ids': new_ids, 'new_pattern_states': new_states,
        'disabled_pattern_id': pattern['id'], 'disabled_at': disabled_at,
        'disabled_reason': disabled_reason,
    }


def _apply_kind(db, conn, kind: str, pattern: dict, payload: dict) -> dict:
    if kind == 'trim':
        return _apply_trim(db, conn, pattern, payload['text'], payload.get('sponsor'))
    if kind == 'rename':
        return _apply_rename(db, conn, pattern, payload['sponsor'])
    if kind == 'split':
        return _apply_split(db, conn, pattern, payload['pieces'])
    if kind == 'retire':
        return _apply_disable(db, conn, pattern,
                              f"Cleanup: no matches in {payload.get('unused_days')} days")
    if kind == 'flag':
        if payload.get('recommended') == 'trim':
            if not payload.get('trim_text'):
                raise SuggestionStateError('flag recommends a trim but has no trim text')
            return _apply_trim(db, conn, pattern, payload['trim_text'], payload.get('sponsor'))
        reason = ('Cleanup: contaminated' if payload.get('contaminated')
                  else 'Cleanup: false positives')
        return _apply_disable(db, conn, pattern, reason)
    raise SuggestionStateError(f'unknown suggestion kind {kind!r}')


def apply_suggestion(db, suggestion_id: int) -> dict:
    """Approve: apply the change in one transaction. Returns the updated suggestion."""
    with db.transaction(immediate=True) as conn:
        suggestion = _load(db, conn, suggestion_id, 'pending')
        pattern = _pattern_on(conn, suggestion['pattern_id'])
        if pattern is None:
            raise SuggestionStateError('the pattern no longer exists')
        if not pattern['is_active']:
            raise SuggestionStateError('the pattern is disabled')
        before = suggestion['before'] or {}
        check_fields = _suggestion_stale_fields(suggestion)
        if not _matches_snapshot(pattern, before, check_fields):
            raise SuggestionStateError('the pattern changed after this suggestion was made')
        applied = _apply_kind(db, conn, suggestion['kind'], pattern, suggestion['payload'] or {})
        applied['after'] = _snapshot_fields(
            _pattern_on(conn, pattern['id']), _suggestion_after_fields(suggestion))
        applied['applied_at'] = utc_now_iso()
        # Orders approvals on one pattern so undo can refuse out of order.
        applied['applied_seq'] = time.time_ns()
        _acknowledge_stats_decision(db, conn, suggestion, pattern)
        db.set_cleanup_suggestion_status(suggestion_id, 'approved', applied=applied, conn=conn)
        db.supersede_pending(pattern['id'], conn=conn)
    invalidate_pattern_catalog_scope()
    return db.get_cleanup_suggestion(suggestion_id)


def reject_suggestion(db, suggestion_id: int) -> dict:
    """Reject: leave the pattern alone and mark it reviewed."""
    with db.transaction(immediate=True) as conn:
        suggestion = _load(db, conn, suggestion_id, 'pending')
        pattern = _pattern_on(conn, suggestion['pattern_id'])
        if pattern is not None:
            before = suggestion['before'] or {}
            check_fields = _reject_stale_fields(suggestion)
            if not _matches_snapshot(pattern, before, check_fields):
                raise SuggestionStateError('the pattern changed after this suggestion was made')
            _acknowledge_stats_decision(db, conn, suggestion, pattern)
        db.set_cleanup_suggestion_status(suggestion_id, 'rejected', conn=conn)
        if pattern is not None:
            db.stamp_pattern_cleanup_reviewed(
                pattern['id'], review_hash(pattern.get('text_template'), pattern.get('sponsor')),
                conn=conn)
    return db.get_cleanup_suggestion(suggestion_id)


def _has_later_approval(db, conn, suggestion: dict) -> bool:
    mine = (suggestion['applied'] or {}).get('applied_seq') or 0
    return any(
        other['id'] != suggestion['id'] and ((other['applied'] or {}).get('applied_seq') or 0) >= mine
        for other in db.get_approved_cleanup_suggestions(suggestion['pattern_id'], conn=conn))


def _restore_disabled_fields(db, conn, pattern_id: int, before: dict) -> None:
    db._update_ad_pattern_conn(conn, pattern_id, is_active=before.get('is_active', 1),
                               disabled_at=before.get('disabled_at'),
                               disabled_reason=before.get('disabled_reason'))


def _undo_split(db, conn, pattern: dict, before: dict, applied: dict) -> None:
    new_ids = applied.get('new_pattern_ids') or []
    after = applied.get('after')
    child_states = {item['id']: item['fields']
                    for item in applied.get('new_pattern_states', [])}
    if after is None or len(child_states) != len(new_ids):
        raise SuggestionStateError('the split predates safe undo snapshots')
    if not _matches_snapshot(pattern, after, ('is_active', 'disabled_at', 'disabled_reason')):
        raise SuggestionStateError('the split pattern changed after the split')
    for new_id in new_ids:
        piece = _pattern_on(conn, new_id)
        if (piece is None or not piece['is_active']
                or db.get_approved_cleanup_suggestions(new_id, conn=conn)):
            raise SuggestionStateError('a split piece changed after the split')
        expected = child_states.get(new_id)
        if not _matches_snapshot(piece, expected, _SPLIT_CHILD_FIELDS):
            raise SuggestionStateError('a split piece changed after the split')
    _restore_disabled_fields(db, conn, pattern['id'], before)
    for new_id in new_ids:
        db.supersede_pending(new_id, conn=conn)
        db._update_ad_pattern_conn(conn, new_id, is_active=0, disabled_reason='Cleanup undo')


def undo_suggestion(db, suggestion_id: int) -> dict:
    """Undo an approved suggestion, restoring only the fields its kind changed."""
    with db.transaction(immediate=True) as conn:
        suggestion = _load(db, conn, suggestion_id, 'approved')
        pattern = _pattern_on(conn, suggestion['pattern_id'])
        if pattern is None:
            raise SuggestionStateError('the pattern no longer exists')
        if _has_later_approval(db, conn, suggestion):
            raise SuggestionStateError('undo the later approved suggestion on this pattern first')
        before = suggestion['before'] or {}
        applied = suggestion['applied'] or {}
        if suggestion['kind'] == 'split':
            _undo_split(db, conn, pattern, before, applied)
        elif applied.get('text') is not None:
            expected = applied.get('after')
            fields = _suggestion_after_fields(suggestion)
            if not _matches_snapshot(pattern, expected, fields):
                raise SuggestionStateError('the pattern changed after this suggestion was applied')
            db._update_ad_pattern_conn(
                conn, pattern['id'], text_template=before.get('text_template'),
                intro_variants=before.get('intro_variants') or [],
                outro_variants=before.get('outro_variants') or [],
                **({'sponsor_id': before.get('sponsor_id')}
                   if (suggestion.get('payload') or {}).get('sponsor') else {}))
        elif suggestion['kind'] == 'rename':
            expected = applied.get('after')
            if not _matches_snapshot(pattern, expected, ('sponsor_id', 'sponsor')):
                raise SuggestionStateError('the sponsor changed after this suggestion was applied')
            db._update_ad_pattern_conn(conn, pattern['id'], sponsor_id=before.get('sponsor_id'))
        else:
            expected = applied.get('after')
            if not _matches_snapshot(
                    pattern, expected, ('is_active', 'disabled_at', 'disabled_reason')):
                raise SuggestionStateError('the pattern was re-enabled after this suggestion')
            _restore_disabled_fields(db, conn, pattern['id'], before)
        restored = _pattern_on(conn, pattern['id'])
        db.stamp_pattern_cleanup_reviewed(
            pattern['id'], review_hash(restored.get('text_template'), restored.get('sponsor')),
            conn=conn)
        _acknowledge_stats_decision(db, conn, suggestion, pattern)
        db.set_cleanup_suggestion_status(suggestion_id, 'undone', conn=conn)
    invalidate_pattern_catalog_scope()
    return db.get_cleanup_suggestion(suggestion_id)

"""Scheduled LLM review of learned ad patterns (Experiments > Pattern cleanup).

A run reviews a batch of learned patterns and stores suggestions (trim, split,
rename, retire, flag). Nothing changes a pattern until the user approves a
suggestion; approval keeps a snapshot so it can be undone.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import re
import threading
import time
from datetime import timedelta
from pathlib import Path

from rapidfuzz import fuzz

from database import Database
from database.settings import registry_default, registry_get_default
from llm_client import ProviderRateLimitedError
from llm_route import (
    LiveRoute, apply_failover, client_for_route, live_route_from, live_route_params,
    resolve_route,
)
from pattern_variants import derive_intro_outro
from processing_queue import ProcessingQueue
from sponsor_normalize import get_or_create_known_sponsor, sanitize_sponsor_name
from text_pattern_matcher import MIN_TEXT_LENGTH
from utils.constants import sanitize_sponsor_label
from utils.cron import is_due, is_valid_expression
from utils.llm_call import call_llm, schema_format_for
from utils.llm_response import extract_json_object
from utils.pattern_catalog import invalidate_pattern_catalog_scope
from utils.pattern_similarity import canonicalize_for_dedupe, similarity
from utils.time import parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger('podcast.pattern_cleanup')

PHASE = 'pattern_cleanup'
LOCK_FILENAME = '.pattern_cleanup.lock'
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
# Patterns whose review is unusable this many times are parked until a forced run.
INVALID_LIMIT = 3
INVALID_PREFIX = 'invalid:'
INVALID_MARKER = 'invalid'
MAX_TOKENS = 4096
MAX_REASONS = 5
MAX_REASON_CHARS = 200

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


def review_hash(text: str | None, sponsor: str | None) -> str:
    """Hash of normalized text plus sponsor; a match means already reviewed."""
    norm = ' '.join((text or '').lower().split())
    raw = f"{norm}\x1f{(sponsor or '').strip().lower()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


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


def _before_snapshot(pattern: dict) -> dict:
    return {
        'text_template': pattern.get('text_template'),
        'sponsor': pattern.get('sponsor'),
        'sponsor_id': pattern.get('sponsor_id'),
        'intro_variants': _decode_list(pattern.get('intro_variants')),
        'outro_variants': _decode_list(pattern.get('outro_variants')),
        'is_active': pattern.get('is_active'),
        'disabled_at': pattern.get('disabled_at'),
        'disabled_reason': pattern.get('disabled_reason'),
    }


# Selection and stats

def select_candidates(db, *, force: bool, batch_size: int) -> list[dict]:
    """Learned patterns due for review; force clears every review stamp first."""
    if force:
        db.clear_cleanup_reviewed()
    out = []
    for row in db.get_cleanup_candidate_rows():
        # A pattern awaiting the user's decision is not re-reviewed unless forced.
        if row.pop('has_pending') and not force:
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

def source_context(db, pattern: dict) -> str | None:
    """Transcript around the pattern in its source episode, span marked [[ ]]; None when unavailable."""
    slug, episode_id = pattern.get('podcast_id'), pattern.get('created_from_episode_id')
    template = (pattern.get('text_template') or '').strip()
    if not slug or not episode_id or not template:
        return None
    try:
        segments = db.get_original_segments(slug, episode_id)
    except (TypeError, ValueError):
        return None
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
    label = sanitize_sponsor_label(raw, show_name=pattern.get('podcast_id'))
    return sanitize_sponsor_name(label) if label else None


def _keeps_sponsor(pattern: dict, original: str, texts: list[str]) -> bool:
    """When the original names its sponsor, at least one kept text must too."""
    sponsor = pattern.get('sponsor')
    if not _has_phrase(sponsor, original):
        return True
    return any(_has_phrase(sponsor, t) for t in texts)


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


def validate_review(pattern: dict, raw: dict) -> dict | None:
    """Gate the model's answer; None means no suggestion. Stored text is always an exact original window."""
    original = pattern.get('text_template') or ''
    pid = pattern.get('id')
    try:
        confidence = max(0.0, min(1.0, float(raw.get('confidence'))))
    except (TypeError, ValueError):
        logger.warning("pattern_cleanup: pattern %s review has no confidence", pid)
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
        if loc is None or len(loc[2]) < MIN_TEXT_LENGTH or not _keeps_sponsor(pattern, original, [loc[2]]):
            logger.warning("pattern_cleanup: pattern %s trim failed validation", pid)
            return None
        total = len(original.split())
        removed = total - len(loc[2].split())
        if removed < TRIM_MIN_WORDS and removed < total * TRIM_MIN_FRACTION:
            return verdict
        verdict.update(action='trim', text=loc[2])
        return verdict
    if action == 'split':
        pieces = _validate_pieces(raw.get('pieces'), original, pattern)
        if pieces is None:
            logger.warning("pattern_cleanup: pattern %s split pieces failed validation", pid)
            return None
        verdict.update(action='split', pieces=pieces)
        return verdict
    if action == 'rename':
        sponsor = _clean_sponsor(raw.get('sponsor'), pattern)
        if sponsor is None or not _has_phrase(sponsor, original):
            logger.warning("pattern_cleanup: pattern %s rename names a sponsor not in the text", pid)
            return None
        if _norm(sponsor) == _norm(pattern.get('sponsor')):
            return verdict
        verdict.update(action='rename', sponsor=sponsor)
        return verdict
    logger.warning("pattern_cleanup: pattern %s review returned unknown action %r", pid, action)
    return None


def _user_prompt(pattern: dict, context: str | None) -> str:
    body = (f"Sponsor on record: {pattern.get('sponsor') or 'unknown'}\n\n"
            f"Pattern text:\n<<<\n{pattern.get('text_template') or ''}\n>>>\n\n")
    if context:
        return body + f"Transcript context:\n<<<\n{context}\n>>>"
    return body + "No transcript context is available; judge from the pattern text alone."


def review_pattern(pattern: dict, context: str | None, *, live: LiveRoute,
                   system_prompt: str | None = None) -> dict | None:
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
    return validate_review(pattern, parsed)


# Run

def _live_route() -> LiveRoute:
    live = live_route_params(PHASE)
    if live.route is not None:
        return live
    return live_route_from(apply_failover(resolve_route(PHASE)))


def _suggestions_for(pattern: dict, stats: list[dict], verdict: dict | None) -> list[dict]:
    """Merge stats suggestions with the model's verdict."""
    out = list(stats)
    if verdict is None:
        return out
    flag = next((s for s in out if s['kind'] == 'flag'), None)
    reasons = list(verdict['reasons'])
    if verdict['contaminated'] and verdict['contamination_reason']:
        reasons.append(verdict['contamination_reason'])
    if flag is not None and verdict['contaminated']:
        flag['payload'].update(contaminated=True,
                               contamination_reason=verdict['contamination_reason'])
        flag['reasons'] += reasons
    action = verdict['action']
    if action == 'trim' and flag is not None:
        flag['payload'].update(recommended='trim', trim_text=verdict['text'])
        flag['reasons'] += [r for r in reasons if r not in flag['reasons']]
    elif action == 'trim':
        out.append(_suggestion('trim', verdict['confidence'], reasons, {'text': verdict['text']}))
    elif action == 'split':
        out.append(_suggestion('split', verdict['confidence'], reasons, {'pieces': verdict['pieces']}))
    elif action == 'rename':
        out.append(_suggestion('rename', verdict['confidence'], reasons, {'sponsor': verdict['sponsor']}))
    elif verdict['contaminated'] and flag is None:
        out.append(_suggestion('flag', verdict['confidence'], reasons, {
            'false_positive_count': pattern.get('false_positive_count') or 0,
            'confirmation_count': pattern.get('confirmation_count') or 0,
            'contaminated': True, 'contamination_reason': verdict['contamination_reason'],
            'recommended': 'disable',
        }))
    return out


def _record_invalid(db, pattern: dict, conn) -> None:
    """Count an unusable review; after INVALID_LIMIT the pattern is parked until a forced run."""
    prior = pattern.get('cleanup_reviewed_hash') or ''
    tail = prior[len(INVALID_PREFIX):] if prior.startswith(INVALID_PREFIX) else ''
    count = int(tail) + 1 if tail.isdigit() else 1
    marker = INVALID_MARKER if count >= INVALID_LIMIT else f'{INVALID_PREFIX}{count}'
    db.stamp_pattern_cleanup_reviewed(pattern['id'], marker, conn=conn)


def _process_pattern(db, run_id: int, pattern: dict, unused_days: int, live: LiveRoute,
                     system_prompt: str, force: bool = False) -> tuple[int, bool]:
    """(suggestions stored, LLM review skipped) for one pattern."""
    stats = stats_suggestions(db, pattern, unused_days)
    skip_llm = any(s['kind'] == 'retire' for s in stats)
    verdict = None
    if not skip_llm:
        verdict = review_pattern(pattern, source_context(db, pattern), live=live,
                                 system_prompt=system_prompt)
    suggestions = _suggestions_for(pattern, stats, verdict)
    before = _before_snapshot(pattern)
    with db.transaction(immediate=True) as conn:
        if force:
            db.supersede_pending(pattern['id'], conn=conn)
        for s in suggestions:
            db.upsert_cleanup_suggestion(run_id, pattern['id'], s['kind'], s['confidence'],
                                         s['reasons'], s['payload'], before, conn=conn)
        if not suggestions and not skip_llm:
            if verdict is None:
                _record_invalid(db, pattern, conn)
            elif verdict['action'] == 'keep':
                db.stamp_pattern_cleanup_reviewed(
                    pattern['id'], review_hash(pattern.get('text_template'), pattern.get('sponsor')),
                    conn=conn)
    return len(suggestions), skip_llm


def is_cleanup_running(db) -> bool:
    with open(Path(db.data_dir) / LOCK_FILENAME, 'w') as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False


def _begin_run(db, force: bool, trigger: str):
    """(lock fd, run id, started_at) with the lock held, or None when another run holds it."""
    fd = open(Path(db.data_dir) / LOCK_FILENAME, 'w')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    try:
        started_at = utc_now_iso()
        db.set_setting('pattern_cleanup_last_run', started_at)
        run_id = db.create_cleanup_run(forced=force, trigger=trigger)
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
        db.set_setting('pattern_cleanup_last_error', summary['error'] or '')
        db.set_setting('pattern_cleanup_last_summary', json.dumps(summary))
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
    live = None
    status, error = 'completed', None
    try:
        live = _live_route()
        batch_size = _clamped_int(db, 'pattern_cleanup_batch_size', BATCH_SIZE_RANGE)
        unused_days = _clamped_int(db, 'pattern_cleanup_unused_days', UNUSED_DAYS_RANGE)
        system_prompt = _system_prompt(db)
        for pattern in select_candidates(db, force=force, batch_size=batch_size):
            try:
                stored, skipped = _process_pattern(db, run_id, pattern, unused_days, live,
                                                   system_prompt, force=force)
            except ProviderRateLimitedError:
                raise
            except Exception as e:
                counts['errors'] += 1
                logger.warning("pattern_cleanup: pattern %s review failed: %s", pattern['id'], e)
                db.clear_leaked_transaction(logger, 'pattern cleanup')
                continue
            counts['reviewed'] += 1
            counts['suggested'] += stored
            counts['skipped'] += int(skipped)
    except Exception as e:
        status, error = 'failed', str(e) or type(e).__name__
        logger.warning("pattern_cleanup: run %s failed: %s", run_id, error)
        db.clear_leaked_transaction(logger, 'pattern cleanup')

    summary = {
        'runId': run_id, 'status': status, 'trigger': trigger, 'forced': force, **counts,
        'model': live.model if live else None, 'provider': live.provider if live else None,
        'credentialSlot': live.credential_slot if live else None,
        'startedAt': started_at, 'finishedAt': utc_now_iso(),
        'durationMs': int((time.monotonic() - start) * 1000), 'error': error,
    }
    _finish(db, run_id, summary, live)
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


def pattern_cleanup_tick(db) -> int | None:
    """Start a background run when enabled, due by cron, and no episode is processing."""
    if not db.get_setting_bool('pattern_cleanup_enabled', default=False):
        return None
    cron = db.get_setting('pattern_cleanup_cron') or registry_default('pattern_cleanup_cron')
    if not is_valid_expression(cron):
        logger.warning("pattern_cleanup: invalid cron %r, using the default", cron)
        cron = registry_default('pattern_cleanup_cron')
    last_run = parse_iso_utc(db.get_setting('pattern_cleanup_last_run'))
    if last_run is not None and not is_due(cron, last_run, utc_now()):
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


def _apply_trim(db, conn, pattern: dict, text: str) -> dict:
    intro, outro = derive_intro_outro(text)
    db._update_ad_pattern_conn(conn, pattern['id'], text_template=text,
                               intro_variants=intro, outro_variants=outro)
    db.stamp_pattern_cleanup_reviewed(pattern['id'], review_hash(text, pattern.get('sponsor')), conn=conn)
    return {'new_pattern_ids': [], 'disabled_pattern_id': None, 'text': text}


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
        db.stamp_pattern_cleanup_reviewed(new_id, review_hash(piece['text'], piece['sponsor']), conn=conn)
        new_ids.append(new_id)
    db._update_ad_pattern_conn(conn, pattern['id'], is_active=0, disabled_at=utc_now_iso(),
                               disabled_reason=f'Cleanup split into patterns: {new_ids}')
    db.stamp_pattern_cleanup_reviewed(
        pattern['id'], review_hash(pattern.get('text_template'), pattern.get('sponsor')), conn=conn)
    return {'new_pattern_ids': new_ids, 'disabled_pattern_id': pattern['id']}


def _apply_kind(db, conn, kind: str, pattern: dict, payload: dict) -> dict:
    if kind == 'trim':
        return _apply_trim(db, conn, pattern, payload['text'])
    if kind == 'rename':
        return _apply_rename(db, conn, pattern, payload['sponsor'])
    if kind == 'split':
        return _apply_split(db, conn, pattern, payload['pieces'])
    if kind == 'retire':
        return _apply_disable(db, conn, pattern,
                              f"Cleanup: no matches in {payload.get('unused_days')} days")
    if kind == 'flag':
        if payload.get('recommended') == 'trim' and payload.get('trim_text'):
            return _apply_trim(db, conn, pattern, payload['trim_text'])
        contaminated_only = (payload.get('contaminated')
                             and (payload.get('false_positive_count') or 0) < HIGH_FP_MIN)
        return _apply_disable(db, conn, pattern, 'Cleanup: contaminated' if contaminated_only
                              else 'Cleanup: false positives')
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
        if pattern['text_template'] != before.get('text_template', pattern['text_template']):
            raise SuggestionStateError('the pattern changed after this suggestion was made')
        applied = _apply_kind(db, conn, suggestion['kind'], pattern, suggestion['payload'] or {})
        applied['applied_at'] = utc_now_iso()
        # Orders approvals on one pattern so undo can refuse out of order.
        applied['applied_seq'] = time.time_ns()
        db.set_cleanup_suggestion_status(suggestion_id, 'approved', applied=applied, conn=conn)
    invalidate_pattern_catalog_scope()
    return db.get_cleanup_suggestion(suggestion_id)


def reject_suggestion(db, suggestion_id: int) -> dict:
    """Reject: leave the pattern alone and mark it reviewed."""
    with db.transaction(immediate=True) as conn:
        suggestion = _load(db, conn, suggestion_id, 'pending')
        db.set_cleanup_suggestion_status(suggestion_id, 'rejected', conn=conn)
        pattern = _pattern_on(conn, suggestion['pattern_id'])
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


def _undo_split(db, conn, pattern: dict, before: dict, applied: dict) -> None:
    new_ids = applied.get('new_pattern_ids') or []
    for new_id in new_ids:
        piece = _pattern_on(conn, new_id)
        if (piece is None or not piece['is_active']
                or db.get_approved_cleanup_suggestions(new_id, conn=conn)):
            raise SuggestionStateError('a split piece changed after the split')
    db._update_ad_pattern_conn(conn, pattern['id'], is_active=before.get('is_active', 1),
                               disabled_at=before.get('disabled_at'),
                               disabled_reason=before.get('disabled_reason'))
    for new_id in new_ids:
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
            if pattern['text_template'] != applied['text']:
                raise SuggestionStateError('the pattern changed after this suggestion was applied')
            db._update_ad_pattern_conn(
                conn, pattern['id'], text_template=before.get('text_template'),
                intro_variants=before.get('intro_variants') or [],
                outro_variants=before.get('outro_variants') or [])
        elif suggestion['kind'] == 'rename':
            if pattern['sponsor_id'] != applied.get('sponsor_id'):
                raise SuggestionStateError('the sponsor changed after this suggestion was applied')
            db._update_ad_pattern_conn(conn, pattern['id'], sponsor_id=before.get('sponsor_id'))
        else:
            if pattern['is_active']:
                raise SuggestionStateError('the pattern was re-enabled after this suggestion')
            db._update_ad_pattern_conn(conn, pattern['id'], is_active=before.get('is_active', 1),
                                       disabled_at=before.get('disabled_at'),
                                       disabled_reason=before.get('disabled_reason'))
        restored = _pattern_on(conn, pattern['id'])
        db.stamp_pattern_cleanup_reviewed(
            pattern['id'], review_hash(restored.get('text_template'), restored.get('sponsor')),
            conn=conn)
        db.set_cleanup_suggestion_status(suggestion_id, 'undone', conn=conn)
    invalidate_pattern_catalog_scope()
    return db.get_cleanup_suggestion(suggestion_id)

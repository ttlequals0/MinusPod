"""Opt-in LLM ad reviewer."""
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Literal
from collections.abc import Callable

from config import (
    resolve_stage_tunables,
    AD_REVIEWER_PARALLEL_ADS_DEFAULT,
    AD_REVIEWER_PARALLEL_ADS_MIN,
    AD_REVIEWER_PARALLEL_ADS_MAX,
    resolve_env_backed_default,
    HOLD_REASON_REVIEWER_CONTRADICTION,
    AUDIO_CUE_ROLE_DEFAULT,
    AUDIO_CUE_ROLE_NON_AD,
    AUDIO_CUE_TYPE_CONTENT_TRANSITION,
    is_template_cue,
    MIN_AD_DURATION_FOR_REMOVAL,
)
from audio_enforcer import content_anchors
from database import DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT
from llm_capabilities import PASS_REVIEWER_1, PASS_REVIEWER_2
from run_log import run_in_worker_thread
from llm_client import (
    get_llm_max_retries, get_llm_timeout, is_rate_limit_error,
    StructuralRateLimitError,
)
from utils.llm_call import call_llm, call_llm_for_window
from utils.llm_response import extract_json_ads_array, extract_json_object
from utils.markers import dai_core_bounds, invalidate_tail_provenance
from utils.prompt import format_sponsor_block, render_prompt, apply_override
from utils.text import (
    BOUNDARY_SNAP_TOLERANCE_S,
    get_timestamped_transcript_for_range,
    get_transcript_text_for_range,
)


Verdict = Literal["confirmed", "adjust", "reject", "resurrect", "failure"]

logger = logging.getLogger(__name__)


def _review_failure_reason(error: Exception) -> str:
    """Short, non-leaking reason for a failed reviewer LLM call.

    The full error is logged separately; the raw provider payload (e.g. a Gemini
    429 JSON blob) must never reach the verdict reasoning, which the UI renders.
    StructuralRateLimitError carries our own already-sanitized, actionable text
    (per-minute cap or daily-quota guidance), so surface it verbatim.
    """
    if isinstance(error, StructuralRateLimitError):
        return f"Review unavailable: {error}"
    if is_rate_limit_error(error):
        return "Review unavailable: LLM rate limit reached"
    return "Review unavailable: LLM call failed"


# Verdict/reasoning contradiction guard (spec 1.4). Verdicts come from
# boundary arithmetic, so an unchanged span with not-an-ad reasoning ships
# as "confirmed" -- hold those for review, never auto-reject. Patterns are
# assertion-shaped regexes ("is not advertising"), not bare nouns: an
# affirming reasoning ("not a false positive") must not trigger a hold.
_PLURAL_NEGATION_PATTERN = (
    r'\bnone\s+(?:of\s+(?:them|these|those)\s+)?(?:are|is)\s+'
    r'(?:an?\s+)?ads?\b'
    r'|\b(?:they|these|those)\s+are\s+not\s+'
    r'(?:an?\s+)?(?:ads?|advertis\w*)\b'
)

REVIEWER_CONTRADICTION_PATTERNS = (
    r'\bcontains?\s+no\s+advertis',            # "contain(s) no advertising content"
    r'\bis\s+not\s+(?:an?\s+)?(?:paid\s+)?advertis',  # "is not advertising"
    r'\bnot\s+an\s+ad\b',
    r'\bno\s+ad\s+content\b',
    r'\bcontains?\s+no\s+ad\b',
    r'\bis\s+a\s+false\s+positive\b',
    r'\bcontains?\s+only\s+the\s+(?:phrase|fragment|words?)\b',
    r'\btranscription\s+artifact\b',
    r'\b(?:is\s+|entirely\s+)organic\s+conversation\b',
    _PLURAL_NEGATION_PATTERN,
)

_CONTRADICTION_RES = tuple(re.compile(p) for p in REVIEWER_CONTRADICTION_PATTERNS)

# Affirmation guard: reasoning that asserts the span IS an ad can never be
# a contradiction hold, even when a negation appears later in the same
# prose. Boundary notes like "that interview material is not advertising"
# refer to a sub-span the reviewer wants trimmed, not the candidate
# (tosh-show 6e9f8a115e24, daily-tech-news-show 0b79e6e6c143 both held
# real ad breaks this way). Assertion-shaped, like the negations above.
#
# TODO(structural): this affirmation/negation/trim-language regex triad is a
# growing free-text heuristic (each pattern traces to a production episode).
# The durable fix is a structured verdict field (e.g. is_ad plus trim bounds)
# in the review prompt contract, so intent stops hiding in prose.
_ELLIPTICAL_AFFIRMATION_PATTERN = (
    r'^\s*(?:multiple|several)\s+(?:[\w-]+\s+){0,2}ads?(?!-)\b')

REVIEWER_AFFIRMATION_PATTERNS = (
    r'\bis\s+an?\s+ad\s+break\b',
    r'\bis\s+an?\s+(?:genuine\s+|real\s+|actual\s+|paid\s+|'
    r'dynamically\s+inserted\s+)?ad(?:vertisement)?(?!-)\b',
    r'\bare\s+(?:all\s+)?(?:genuine\s+|real\s+)?ads(?!-)\b',
    r'\bis\s+(?:genuine\s+|real\s+|paid\s+)?advertising\b',
    # Elliptical reviewer prose often begins with the classification rather
    # than a verb: "Multiple Norwegian ads ... adjusted start to exclude ...".
    # This is still an explicit affirmation when paired with trim language.
    _ELLIPTICAL_AFFIRMATION_PATTERN,
)

_AFFIRMATION_RES_BY_PATTERN = {
    pattern: re.compile(pattern) for pattern in REVIEWER_AFFIRMATION_PATTERNS
}
_AFFIRMATION_RES = tuple(
    _AFFIRMATION_RES_BY_PATTERN[p] for p in REVIEWER_AFFIRMATION_PATTERNS)
_ELLIPTICAL_AFFIRMATION_RE = _AFFIRMATION_RES_BY_PATTERN[
    _ELLIPTICAL_AFFIRMATION_PATTERN]

# Trim-language precheck: only spend the recovery LLM call when the
# reasoning describes a sub-span trim; plain "not an ad" skips it.
_TRIM_LANGUAGE_RE = re.compile(
    r'\btrim'
    r'|\bends?\s+at\b'
    r'|\bstarts?\s+at\b'
    r'|\bbegins?\s+at\b'
    r'|\b(?:must|should)\s+be\s+removed\b'
    r'|\bremoved?\s+from\b'
    r'|\bshould\s+move\b'
    r'|\bmove[sd]?\s+(?:to|back|forward)\b'
    r'|\bexcluded?\b',
    re.IGNORECASE,
)

# Elliptical openings ("Multiple ads ...") need stronger scoping than a
# generic word such as "excluded".  When their prose also contains a
# negation, only an explicit edge adjustment establishes that the negation
# describes preserved context rather than the whole candidate.
_EXPLICIT_BOUNDARY_TRIM_RE = re.compile(
    r'\b(?:adjust(?:ed|ing)?|move[sd]?|shift(?:ed|ing)?|trim(?:med|ming)?)\s+'
    r'(?:the\s+)?(?:start|end|boundar(?:y|ies))\b'
    r'|\b(?:start|end|boundar(?:y|ies))\s+(?:was\s+)?'
    r'(?:adjusted|moved|shifted|trimmed)\b',
    re.IGNORECASE,
)

_WHOLE_CANDIDATE_NEGATION_RE = re.compile(
    _PLURAL_NEGATION_PATTERN
    + r'|\b(?:this|it|the\s+)?(?:entire\s+|whole\s+|full\s+)?'
    r'(?:candidate|span|segment|block)\s+(?:is|contains?)\s+'
    r'(?:not\s+(?:an?\s+ad|advertis\w*)|no\s+(?:ad|advertis\w*))\b'
    r'|\b(?:this|it)\s+is\s+not\s+(?:an?\s+ad|advertis\w*)\b',
    re.IGNORECASE,
)

# In-range slack for recovered trim bounds. A recovered edge may sit up to
# this far outside the original span (float noise from the model re-reading
# timestamps) and is clamped back in; anything further out is rejected.
_TRIM_RANGE_EPSILON_S = 0.5

_TRIM_RECOVERY_SYSTEM_PROMPT = (
    "A podcast ad reviewer returned a candidate ad span with unchanged "
    "boundaries while its reasoning says part of the span is not ad content. "
    "Read the reasoning and identify the sub-span that IS the ad.\n\n"
    "Return ONLY strict JSON, no explanation, no markdown:\n"
    '{"ad_start": FLOAT_SECONDS, "ad_end": FLOAT_SECONDS}\n'
    "Both values must be numeric seconds inside the original span. If the "
    "reasoning does not identify a specific ad sub-span, return null instead."
)


def reasoning_affirms_ad(reasoning: str | None) -> bool:
    """True when reviewer reasoning asserts the candidate span is an ad."""
    if not reasoning:
        return False
    lowered = reasoning.lower()
    whole_negations = list(_WHOLE_CANDIDATE_NEGATION_RE.finditer(lowered))
    for regex in _AFFIRMATION_RES:
        match = regex.search(lowered)
        if not match:
            continue
        # A broad positive phrase such as "are ads" can be a substring of
        # the explicit denial "none are ads". Never count text inside the
        # denial itself as a separate affirmation.
        if any(neg.start() <= match.start() and match.end() <= neg.end()
               for neg in whole_negations):
            continue
        if regex is _ELLIPTICAL_AFFIRMATION_RE:
            tail = lowered[match.end():]
            if any(neg.start() >= match.end() for neg in whole_negations):
                continue
            contradiction_positions = [
                found.start()
                for contradiction in _CONTRADICTION_RES
                if (found := contradiction.search(tail)) is not None
            ]
            if contradiction_positions:
                boundary_trim = _EXPLICIT_BOUNDARY_TRIM_RE.search(tail)
                if (boundary_trim is None
                        or min(contradiction_positions) < boundary_trim.start()):
                    continue
        return True
    return False


def reasoning_contradicts_cut(reasoning: str | None) -> bool:
    """True when reviewer reasoning asserts the span is not an ad.

    An affirmation wins only when the prose also describes a boundary trim:
    that combination means the negation is a note about a sub-span, which
    trim recovery handles. An affirmation with a negation but NO trim
    language is a whole-span dispute ("is an ad for the show's own merch,
    which is not advertising") and must still hold: pre-guard behavior.
    """
    if not reasoning:
        return False
    if reasoning_affirms_ad(reasoning) and _TRIM_LANGUAGE_RE.search(reasoning):
        return False
    lowered = reasoning.lower()
    return any(r.search(lowered) for r in _CONTRADICTION_RES)


def _adjusted_ad_copy(ad: dict, start: float, end: float,
                      original_start: float, original_end: float,
                      reasoning: str | None, confidence: float | None,
                      model: str | None) -> dict:
    """Copy of ``ad`` with adjusted bounds and the reviewer bookkeeping
    fields every adjust application must stamp. Single seam so the two
    adjust paths (boundary-delta adjust, affirmed-confirm trim recovery)
    cannot drift on which fields they write."""
    updated = dict(ad)
    invalidate_tail_provenance(updated, end)
    updated["start"] = start
    updated["end"] = end
    updated["reviewer_verdict"] = "adjust"
    updated["reviewer_original_start"] = original_start
    updated["reviewer_original_end"] = original_end
    updated["reviewer_reasoning"] = reasoning
    updated["reviewer_confidence"] = confidence
    updated["reviewer_model"] = model
    return updated


def is_contradiction_hold(verdict: str, reasoning: str | None,
                          structured_is_ad: bool | None = None) -> bool:
    """Single hold criterion shared by the reviewer pool split and both
    pass-1/pass-2 apply paths. Pure: no side effects. Structured is_ad=true
    overrides the prose heuristics; trim-bounds recovery remains open."""
    if verdict not in ('confirmed', 'adjust'):
        return False
    if structured_is_ad is True:
        return False
    return reasoning_contradicts_cut(reasoning)


def log_contradiction_event(verdict: "ReviewVerdict", *, model: str | None,
                            slug: str | None, episode_id: str | None) -> None:
    """Emit the contradiction-guard telemetry line for ``verdict``. Called
    only from the reviewer's accepted-pool loop, which sees every verdict
    once."""
    if verdict.verdict not in ('confirmed', 'adjust'):
        return
    if not reasoning_contradicts_cut(verdict.reasoning):
        return
    event = ("reviewer_contradiction_suppressed_by_structured"
             if verdict.structured_is_ad is True
             else "reviewer_contradiction_guard_fired")
    logger.info(
        "%s model=%s slug=%s episode_id=%s start=%.1f end=%.1f",
        event, model or "unknown", slug, episode_id,
        verdict.original_start, verdict.original_end)


def _resolve_reviewer_parallel_ads() -> int:
    """Resolve the reviewer parallel-ads concurrency for this run.

    DB-customized value wins over env default; both fall back to the
    registered default in ENV_BACKED_SETTINGS. Clamped to [1, 32].
    """
    try:
        from llm_client import _get_cached_setting
        db_val = _get_cached_setting('ad_reviewer_parallel_ads')
    except Exception:
        db_val = None

    raw = db_val if db_val is not None else resolve_env_backed_default('ad_reviewer_parallel_ads')
    try:
        n = int(raw) if raw is not None else AD_REVIEWER_PARALLEL_ADS_DEFAULT
    except (ValueError, TypeError):
        n = AD_REVIEWER_PARALLEL_ADS_DEFAULT
    return max(
        AD_REVIEWER_PARALLEL_ADS_MIN,
        min(AD_REVIEWER_PARALLEL_ADS_MAX, n),
    )


# How wide the resurrection band is below the user's min_cut_confidence
# (e.g. threshold 0.80 -> resurrection eligible if 0.60 <= confidence < 0.80).
RESURRECT_BAND_WIDTH = 0.20


# Tolerance for treating a returned ad as "boundaries unchanged". Floats
# that differ from the input by less than this are treated as confirmed
# rather than adjust. Set tight (0.1s) so genuine sub-second corrections
# from the LLM surface as adjust verdicts in the audit log; only true
# rounding noise rounds away.
_CONFIRMED_BOUNDARY_TOLERANCE_S = 0.1

# Prose/number consistency check on adjust verdicts: warn when the reasoning
# names a boundary figure further than the edge-proximity tolerance + 2s
# margin from the emitted boundary (observability only; see
# _warn_prose_boundary_mismatch).
_PROSE_BOUNDARY_WARN_GAP_S = BOUNDARY_SNAP_TOLERANCE_S + 2.0

# Explicit seconds figures next to boundary words in adjust reasoning, e.g.
# "the ad ends at 28.4s" / "starts at roughly 95s". (?!:) rejects clock-style
# times ("ends at 1:05") whose leading digit is not a seconds figure.
_PROSE_END_FIGURE_RE = re.compile(
    r'\b(?:ends?\s+(?:at|around|near|by)|until|through)\s+'
    r'(?:roughly\s+|about\s+|approximately\s+|around\s+)?'
    r'(\d+(?:\.\d+)?)(?!:)\s*s?\b',
    re.IGNORECASE,
)
_PROSE_START_FIGURE_RE = re.compile(
    r'\b(?:starts?|begins?)\s+(?:at|around|near)\s+'
    r'(?:roughly\s+|about\s+|approximately\s+|around\s+)?'
    r'(\d+(?:\.\d+)?)(?!:)\s*s?\b',
    re.IGNORECASE,
)


def _warn_prose_boundary_mismatch(
    reasoning: str | None,
    start: float,
    end: float,
    *,
    slug: str | None,
    episode_id: str | None,
) -> None:
    """Log when adjust reasoning names a boundary figure far from the emitted
    number (the-tim-dillon-show a55cb5b8216d: reasoning named the ad's final
    sentence near 28.4s while the emitted end was 20.0s). Observability only:
    auto-arbitrating between two model numbers would be guesswork.
    """
    if not reasoning:
        return
    for regex, boundary, side in (
        (_PROSE_START_FIGURE_RE, start, "start"),
        (_PROSE_END_FIGURE_RE, end, "end"),
    ):
        for match in regex.finditer(reasoning):
            figure = float(match.group(1))
            if abs(figure - boundary) > _PROSE_BOUNDARY_WARN_GAP_S:
                logger.warning(
                    f"[{slug}:{episode_id}] Reviewer adjust prose/number "
                    f"mismatch: reasoning names {side} {figure:.1f}s but "
                    f"emitted {side} is {boundary:.1f}s "
                    f"(reasoning: {reasoning[:120]!r})"
                )
                return


def _first_num(d: dict, keys: tuple, default: float) -> float:
    """Return the first finite numeric value among keys, else default.

    Skips None, booleans, and NaN/Inf. The reviewer prompt may carry the
    correction under a corrected_/adjusted_ key when start/end is absent.
    """
    for k in keys:
        v = d.get(k)
        if v is None or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return default


@dataclass
class ReviewVerdict:
    """Per-ad reviewer outcome."""
    pool: str  # "accepted" or "resurrection"
    pass_num: int  # 1 or 2
    verdict: Verdict
    original_start: float
    original_end: float
    adjusted_start: float | None = None
    adjusted_end: float | None = None
    reasoning: str | None = None
    confidence: float | None = None
    model_used: str = ""
    latency_ms: int = 0
    success: bool = True
    structured_is_ad: bool | None = None


@dataclass
class ReviewResult:
    """Full output of one reviewer invocation over both pools.

    `accepted_after_review` is the post-reviewer cut list (adjustments applied,
    rejections removed, resurrections added). `verdicts` is the full audit
    trail, one entry per ad the reviewer evaluated.
    """
    accepted_after_review: list[dict] = field(default_factory=list)
    rejected_by_reviewer: list[dict] = field(default_factory=list)
    resurrected: list[dict] = field(default_factory=list)
    verdicts: list[ReviewVerdict] = field(default_factory=list)
    held_by_contradiction: list[dict] = field(default_factory=list)


def _format_cue_section(*, audio_analysis, ad_start: float, ad_end: float,
                        cue_pair=None, cue_snap=None, silence_snap=None,
                        bucket_radius: float = 60.0) -> str:
    """Render audio cue context for the reviewer's per-ad user prompt (#350).

    Includes:
    - Template cues within bucket_radius of the candidate boundaries,
      rendered as ground-truth boundary markers.
    - Spectral cues in the same radius, rendered as weak evidence only.
    - The ``cue_pair`` block when the ad was synthesised from a bracketing
      pair of cues; signals the reviewer to keep the ad even if the
      transcript inside looks light on promotional language.
    - The ``cue_snap`` block when the boundary snap already shifted an edge
      to a cue; signals the reviewer not to undo that snap.

    Returns the empty string when there is nothing relevant to surface.
    """
    lines = []
    if audio_analysis is not None:
        try:
            cues = audio_analysis.get_signals_by_type('audio_cue')
        except AttributeError:
            cues = []
        # Template cues: precise matches, treated as ground-truth markers.
        near_start_tmpl = []
        near_end_tmpl = []
        # Spectral cues: loudness bursts, weak evidence only.
        near_start_spec = []
        near_end_spec = []
        near_non_ad = []
        near_content_transition = []
        for cue in cues:
            if cue.confidence < 0.80:
                continue
            details = cue.details or {}
            label = details.get('label') or 'audio cue'
            role = details.get('role', AUDIO_CUE_ROLE_DEFAULT)
            near_start_edge = (
                abs(cue.start - ad_start) <= bucket_radius
                or abs(cue.end - ad_start) <= bucket_radius
            )
            near_end_edge = (
                abs(cue.start - ad_end) <= bucket_radius
                or abs(cue.end - ad_end) <= bucket_radius
            )
            if role == AUDIO_CUE_ROLE_NON_AD:
                if not (near_start_edge or near_end_edge):
                    continue
                # content_transition may be an ad boundary; intro/outro never are.
                cue_type = (cue.details or {}).get('cue_type')
                if cue_type == AUDIO_CUE_TYPE_CONTENT_TRANSITION:
                    near_content_transition.append((cue, label))
                else:
                    near_non_ad.append((cue, label))
                continue
            template = is_template_cue(details)
            if near_start_edge:
                if template:
                    near_start_tmpl.append((cue, label))
                else:
                    near_start_spec.append(cue)
            if near_end_edge:
                if template:
                    near_end_tmpl.append((cue, label))
                else:
                    near_end_spec.append(cue)

        if near_start_tmpl or near_end_tmpl:
            lines.append("AUDIO CUE EVIDENCE:")
            for cue, label in near_start_tmpl:
                lines.append(
                    f"  - near AD START: \"{label}\" cue at "
                    f"{cue.start:.1f}s-{cue.end:.1f}s "
                    f"(confidence {cue.confidence:.0%})"
                )
            for cue, label in near_end_tmpl:
                lines.append(
                    f"  - near AD END: \"{label}\" cue at "
                    f"{cue.start:.1f}s-{cue.end:.1f}s "
                    f"(confidence {cue.confidence:.0%})"
                )
            lines.append(
                "These cues are show stingers / break jingles. Treat each as a "
                "ground-truth boundary marker for its side of the break -- do not "
                "pull a boundary across a cue without strong transcript evidence."
            )

        if near_start_spec or near_end_spec:
            lines.append("GENERIC AUDIO CUES NEARBY (weak evidence):")
            for cue in near_start_spec:
                lines.append(
                    f"  - near AD START: loudness burst at "
                    f"{cue.start:.1f}s-{cue.end:.1f}s "
                    f"(confidence {cue.confidence:.0%})"
                )
            for cue in near_end_spec:
                lines.append(
                    f"  - near AD END: loudness burst at "
                    f"{cue.start:.1f}s-{cue.end:.1f}s "
                    f"(confidence {cue.confidence:.0%})"
                )
            lines.append(
                "Do not move a boundary to one of these without transcript support."
            )

        if near_non_ad:
            lines.append("SHOW INTRO/OUTRO MARKERS NEARBY:")
            for cue, label in near_non_ad:
                lines.append(
                    f"  - \"{label}\" at {cue.start:.1f}s-{cue.end:.1f}s "
                    f"(the show's open/close, NOT an ad boundary)"
                )
            lines.append(
                "Do not anchor this ad's boundary to these markers; they are the "
                "show's own intro/outro, not break stingers. "
                "A pre-roll ad may end exactly where the intro starts; "
                "a post-roll ad may begin where the outro ends."
            )

        if near_content_transition:
            lines.append("CONTENT TRANSITION MARKERS NEARBY:")
            for cue, label in near_content_transition:
                lines.append(
                    f"  - \"{label}\" at {cue.start:.1f}s-{cue.end:.1f}s "
                    f"(a recurring content/segment transition, may or may not be an ad boundary)"
                )
            lines.append(
                "Prefer aligning a boundary to a marker when the transcript "
                "supports it, but a marker never forces a cut."
            )

        splice = getattr(audio_analysis, 'splice_evidence', None) or {}
        near_splice = []
        for event in splice.get('events', []):
            time = event.get('time')
            if time is None:
                continue
            end_time = event.get('end_time')
            end_time = end_time if end_time is not None else time
            near = any(
                abs(event_time - ad_edge) <= bucket_radius
                for ad_edge in (ad_start, ad_end)
                for event_time in (time, end_time)
            )
            if near:
                near_splice.append((event, time, end_time))
        if near_splice:
            lines.append("SPLICE EVIDENCE NEAR BOUNDARIES:")
            for event, time, end_time in near_splice:
                parts = [f"{event.get('duration_s', 0.0):.1f}s"]
                if event.get('depth_dbfs') is not None:
                    parts.append(f"depth {event['depth_dbfs']} dBFS")
                if event.get('loudness_step_lu') is not None:
                    parts.append(f"loudness step {event['loudness_step_lu']:+.1f} LU")
                if event.get('centroid_step_hz') is not None:
                    parts.append(f"centroid step {event['centroid_step_hz']:+.0f} Hz")
                if event.get('flatness_step') is not None:
                    parts.append(f"flatness step {event['flatness_step']:+.2f}")
                lines.append(
                    f"  - {event.get('type')} at {time:.1f}s-{end_time:.1f}s "
                    f"({', '.join(parts)})"
                )
            lines.append(
                "These are encoding artifacts typical of dynamic ad insertion. "
                "An untranscribed span next to one is likely inserted ad audio, "
                "not the show going quiet -- weigh that when the transcript "
                "inside the candidate is sparse or empty."
            )

        # Pre/post-roll position bias: an ad wholly outside the content span
        # (before the first intro or after the last outro) is expected to be
        # promotional, so lean toward keeping it.
        pre_roll_boundary, post_roll_boundary = content_anchors(audio_analysis)
        position_notes = []
        if pre_roll_boundary is not None and ad_end <= pre_roll_boundary:
            position_notes.append(
                f"before the show's intro marker at {pre_roll_boundary:.0f}s "
                f"(pre-roll zone)"
            )
        if post_roll_boundary is not None and ad_start >= post_roll_boundary:
            position_notes.append(
                f"after the show's outro marker at {post_roll_boundary:.0f}s "
                f"(post-roll zone)"
            )
        if position_notes:
            lines.append(
                "POSITION: this ad sits " + " and ".join(position_notes)
                + " -- promotional copy here is expected; lean toward keeping it."
            )
    if cue_pair:
        start_label = (cue_pair.get('start') or {}).get('label')
        end_label = (cue_pair.get('end') or {}).get('label')
        lines.append(
            f"CUE-PAIR ORIGIN: this ad was bracketed by matching cues "
            f"(start={start_label!r}, end={end_label!r}). The transcript inside "
            f"may be sparse on promotional language; keep the ad as long as any "
            f"sponsor or platform copy appears between the cues."
        )
    if cue_snap:
        moved = []
        for edge in ('start', 'end'):
            rec = cue_snap.get(edge)
            if not rec:
                continue
            # candidates counts the chosen cue too
            ambig = ""
            if rec.get('ambiguous') and rec.get('candidates'):
                _n = rec['candidates'] - 1
                ambig = f" ({_n} other {'cue' if _n == 1 else 'cues'} nearby)"
            if edge == 'start':
                moved.append(
                    f"start snapped to \"{rec.get('label') or 'cue'}\" "
                    f"end (was {rec.get('original')}s){ambig}"
                )
            else:
                moved.append(
                    f"end snapped to \"{rec.get('label') or 'cue'}\" "
                    f"start (was {rec.get('original')}s){ambig}"
                )
        if moved:
            lines.append(
                "CUE SNAP APPLIED: " + "; ".join(moved) +
                ". Do not undo these snaps; they land the cut on the chime "
                "rather than mid-conversation."
            )
    if silence_snap:
        moved = []
        for edge in ('start', 'end'):
            rec = silence_snap.get(edge)
            if not rec:
                continue
            moved.append(
                f"{edge} snapped to silence midpoint "
                f"(was {rec['original']}s, silence {rec['silence_start']}s-{rec['silence_end']}s)"
            )
        if moved:
            lines.append(
                "SILENCE SNAP APPLIED: " + "; ".join(moved) +
                ". The edge sits in a silence gap deliberately; do not move it."
            )
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


class AdReviewer:
    """Reviews detector + validator output before audio cuts are applied."""

    def __init__(
        self,
        db,
        llm_client,
        sponsor_service=None,
        sponsor_history_provider: Callable[[str], str] | None = None,
    ):
        self.db = db
        self._llm_client = llm_client
        self.sponsor_service = sponsor_service
        self._sponsor_history_provider = sponsor_history_provider

    def review(
        self,
        accepted_ads: list[dict],
        resurrection_eligible: list[dict],
        segments: list[dict],
        episode_meta: dict,
        pass_num: int,
        pass_model: str,
    ) -> ReviewResult:
        """Run reviewer over both pools.

        Args:
            accepted_ads: Ads currently in the cut list (after validation +
                confidence gate). Reviewer may confirm, adjust boundaries, or
                reject these.
            resurrection_eligible: Ads validator left out of the cut list whose
                confidence is in the resurrection band. Reviewer may resurrect
                or keep rejected.
            segments: Whisper segments (in the same coordinate space as ad
                start/end) used for context windows.
            episode_meta: Dict with keys: podcast_name, episode_title,
                podcast_description, episode_description, slug, episode_id,
                podcast_id.
            pass_num: 1 (first detection pass) or 2 (verification pass).
            pass_model: Model used by the corresponding pass; used as fallback
                when ``review_model`` setting is ``same_as_pass``.

        Returns:
            ReviewResult with the post-reviewer accepted list and the audit
            trail. On catastrophic failure, returns the inputs unmodified with
            a synthetic failure verdict per ad so the audit log still records
            the attempt.
        """
        try:
            return self._review_inner(
                accepted_ads, resurrection_eligible, segments,
                episode_meta, pass_num, pass_model,
            )
        except Exception as e:
            logger.error(
                f"[{episode_meta.get('slug')}:{episode_meta.get('episode_id')}] "
                f"Reviewer pass {pass_num} hit catastrophic failure: {e}",
                exc_info=True,
            )
            return ReviewResult(accepted_after_review=list(accepted_ads))

    def _review_inner(
        self,
        accepted_ads: list[dict],
        resurrection_eligible: list[dict],
        segments: list[dict],
        episode_meta: dict,
        pass_num: int,
        pass_model: str,
    ) -> ReviewResult:
        if not accepted_ads and not resurrection_eligible:
            return ReviewResult()

        max_shift = self._read_max_boundary_shift()
        model = self._resolve_model(pass_model)
        sponsor_block = format_sponsor_block(self._sponsor_list_or_empty())
        review_prompt = self._render_review_prompt(max_shift, sponsor_block)
        resurrect_prompt = self._render_resurrect_prompt(sponsor_block)

        result = ReviewResult(verdicts=[])

        parallel_ads = _resolve_reviewer_parallel_ads()
        if parallel_ads > 1 and (len(accepted_ads) + len(resurrection_eligible)) > 1:
            logger.info(
                f"[{episode_meta.get('slug')}:{episode_meta.get('episode_id')}] "
                f"Reviewer pass {pass_num} running "
                f"{len(accepted_ads)}+{len(resurrection_eligible)} ads with "
                f"concurrency={parallel_ads}"
            )

        # Accepted pool first. Position-indexed merge preserves input order so
        # verdicts list and downstream pattern-correction lookups match the
        # original sequential semantics.
        accepted_results = self._run_review_batch(
            accepted_ads,
            pool="accepted",
            pass_num=pass_num,
            segments=segments,
            episode_meta=episode_meta,
            system_prompt=review_prompt,
            model=model,
            max_shift=max_shift,
            max_workers=parallel_ads,
        )
        for verdict, updated_ad in accepted_results:
            # Human corrections are the highest-confidence signal. They are
            # deliberately omitted from the reviewer call and retain their
            # validated bounds and cut decision unchanged. Do not synthesize
            # a ReviewVerdict here: ad_reviewer_log and its totalReviews stat
            # count actual LLM calls. The persisted validation.user_confirmed
            # field is the audit record for this human-authorized bypass.
            if verdict is None:
                result.accepted_after_review.append(updated_ad)
                continue
            result.verdicts.append(verdict)
            log_contradiction_event(
                verdict, model=verdict.model_used,
                slug=episode_meta.get('slug'),
                episode_id=episode_meta.get('episode_id'))
            if verdict.verdict == "reject":
                marked = dict(updated_ad)
                marked["was_cut"] = False
                marked["reviewer_verdict"] = "reject"
                marked["reviewer_reasoning"] = verdict.reasoning
                marked["reviewer_confidence"] = verdict.confidence
                marked["reviewer_model"] = verdict.model_used
                marked["source"] = "reviewer"
                result.rejected_by_reviewer.append(marked)
            elif is_contradiction_hold(
                    verdict.verdict, verdict.reasoning,
                    verdict.structured_is_ad):
                held = dict(updated_ad)
                held["was_cut"] = False
                held["held_for_review"] = True
                held["hold_reason"] = HOLD_REASON_REVIEWER_CONTRADICTION
                held["reviewer_verdict"] = verdict.verdict
                held["reviewer_reasoning"] = verdict.reasoning
                held["reviewer_confidence"] = verdict.confidence
                held["reviewer_model"] = verdict.model_used
                held["source"] = "reviewer"
                held["reviewer_contradiction"] = True
                # Preserve the reviewer's proposed trim so the review UI can
                # offer approving the trimmed span instead of all-or-nothing.
                # An adjust verdict already carries adjusted_*; a confirmed
                # verdict came back with unchanged boundaries, so recover
                # machine bounds from the reasoning prose with one follow-up
                # call (on any failure the hold ships without bounds, exactly
                # as before; the verdict stays 'confirmed' and the ad stays
                # out of the cut list either way). The persisted marker (held
                # via _apply_reviewer_verdict_to_ad in processing.py) keeps
                # pass-1 boundaries; these fields are mirrored here so both
                # hold sites carry the same shape.
                if verdict.verdict == "confirmed":
                    recovered = self._recover_contradiction_trim(
                        verdict,
                        ad=updated_ad,
                        segments=segments,
                        model=model,
                        pass_num=pass_num,
                        slug=episode_meta.get('slug'),
                        episode_id=episode_meta.get('episode_id'),
                    )
                    if recovered is not None:
                        verdict.adjusted_start, verdict.adjusted_end = recovered
                if (verdict.adjusted_start is not None
                        and verdict.adjusted_end is not None):
                    held["reviewer_proposed_start"] = verdict.adjusted_start
                    held["reviewer_proposed_end"] = verdict.adjusted_end
                logger.warning(
                    f"[{episode_meta.get('slug')}:{episode_meta.get('episode_id')}] "
                    f"Reviewer contradiction hold @ "
                    f"{verdict.original_start:.1f}-{verdict.original_end:.1f}s: "
                    f"verdict={verdict.verdict} but reasoning says not an ad: "
                    f"reasoning={verdict.reasoning[:80]!r}"
                )
                result.held_by_contradiction.append(held)
            elif (verdict.verdict == "confirmed"
                  and pass_num == 1
                  and reasoning_affirms_ad(verdict.reasoning)
                  and _TRIM_LANGUAGE_RE.search(verdict.reasoning or "")):
                # Affirmed ad whose prose describes a trim the boundary
                # arithmetic missed (model returned the span unchanged).
                # Recover the trim and apply it as an adjust; on any
                # failure accept unchanged. Never a hold: the reviewer
                # says this IS an ad. Pass 1 only: the pass-2 apply path
                # coerces every adjust back to confirmed and discards the
                # bounds, so spending the recovery call there buys nothing.
                recovered = self._recover_contradiction_trim(
                    verdict,
                    ad=updated_ad,
                    segments=segments,
                    model=model,
                    pass_num=pass_num,
                    slug=episode_meta.get('slug'),
                    episode_id=episode_meta.get('episode_id'),
                )
                if recovered is None:
                    result.accepted_after_review.append(updated_ad)
                else:
                    new_start, new_end = self._clamp_proposed_bounds(
                        updated_ad, recovered[0], recovered[1],
                        verdict.original_start, verdict.original_end,
                        max_shift,
                        episode_meta.get('slug'),
                        episode_meta.get('episode_id'))
                    verdict.verdict = "adjust"
                    verdict.adjusted_start = new_start
                    verdict.adjusted_end = new_end
                    trimmed = _adjusted_ad_copy(
                        updated_ad, new_start, new_end,
                        verdict.original_start, verdict.original_end,
                        verdict.reasoning, verdict.confidence,
                        verdict.model_used)
                    result.accepted_after_review.append(trimmed)
                    logger.info(
                        f"[{episode_meta.get('slug')}:"
                        f"{episode_meta.get('episode_id')}] Affirmed "
                        f"confirm routed to trim recovery @ "
                        f"{verdict.original_start:.1f}-"
                        f"{verdict.original_end:.1f}s -> "
                        f"{new_start:.1f}-{new_end:.1f}s (applied as adjust)"
                    )
            else:
                result.accepted_after_review.append(updated_ad)

        resurrection_results = self._run_review_batch(
            resurrection_eligible,
            pool="resurrection",
            pass_num=pass_num,
            segments=segments,
            episode_meta=episode_meta,
            system_prompt=resurrect_prompt,
            model=model,
            max_shift=max_shift,
            max_workers=parallel_ads,
        )
        for verdict, updated_ad in resurrection_results:
            result.verdicts.append(verdict)
            if verdict.verdict == "resurrect":
                marked = dict(updated_ad)
                marked["was_cut"] = True
                marked["reviewer_verdict"] = "resurrect"
                marked["reviewer_reasoning"] = verdict.reasoning
                marked["reviewer_confidence"] = verdict.confidence
                marked["reviewer_model"] = verdict.model_used
                marked["source"] = "reviewer"
                result.resurrected.append(marked)
                result.accepted_after_review.append(marked)

        self._flush_log(result.verdicts, episode_meta)
        return result

    def _run_review_batch(self, ads, *, pool, pass_num, segments,
                          episode_meta, system_prompt, model, max_shift,
                          max_workers):
        """Run _review_single across a list of ads, sequential or via thread
        pool depending on max_workers. Returns (verdict, updated_ad) pairs
        in input order regardless of completion order."""
        if not ads:
            return []

        def _run_one(idx):
            if (pool == "accepted"
                    and (ads[idx].get("validation") or {}).get("user_confirmed")):
                return None, ads[idx]
            return self._review_single(
                ad=ads[idx],
                pool=pool,
                pass_num=pass_num,
                segments=segments,
                episode_meta=episode_meta,
                system_prompt=system_prompt,
                model=model,
                max_shift=max_shift,
            )

        if max_workers <= 1 or len(ads) == 1:
            return [_run_one(i) for i in range(len(ads))]

        ordered = [None] * len(ads)
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix='reviewer') as executor:
            futures = {executor.submit(run_in_worker_thread, _run_one, i): i
                       for i in range(len(ads))}
            for fut in as_completed(futures):
                idx = futures[fut]
                ordered[idx] = fut.result()
        return ordered

    def _review_single(
        self,
        *,
        ad: dict,
        pool: str,
        pass_num: int,
        segments: list[dict],
        episode_meta: dict,
        system_prompt: str,
        model: str,
        max_shift: int,
    ) -> tuple[ReviewVerdict, dict]:
        """Review one ad. Always returns (verdict, ad). On failure or
        unparseable response, verdict.verdict is 'failure' and ad is the input
        unmodified."""
        original_start = float(ad.get("start", 0.0))
        original_end = float(ad.get("end", 0.0))

        user_prompt = self._build_user_prompt(
            ad=ad,
            segments=segments,
            episode_meta=episode_meta,
            pool=pool,
            max_shift=max_shift,
        )
        slug = episode_meta.get("slug")
        episode_id = episode_meta.get("episode_id")
        window_label = f"reviewer-pass{pass_num}-{pool}"

        pass_name = PASS_REVIEWER_1 if pass_num == 1 else PASS_REVIEWER_2
        max_tokens, temperature, reasoning = resolve_stage_tunables('reviewer')

        t0 = time.monotonic()
        response, error = call_llm_for_window(
            llm_client=self._llm_client,
            model=model,
            system_prompt=system_prompt,
            prompt=user_prompt,
            llm_timeout=get_llm_timeout(),
            max_retries=get_llm_max_retries(),
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning,
            slug=slug,
            episode_id=episode_id,
            window_label=window_label,
            pass_name=pass_name,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if response is None:
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer {window_label} "
                f"@ {original_start:.1f}s failed: {error}. Falling through "
                f"with original ad."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="failure",
                    original_start=original_start, original_end=original_end,
                    reasoning=_review_failure_reason(error),
                    model_used=model, latency_ms=latency_ms, success=False,
                ),
                ad,
            )

        text = self._extract_response_text(response)
        ads_returned, _method = extract_json_ads_array(
            text, slug=slug, episode_id=episode_id
        )
        if ads_returned is None:
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer {window_label} "
                f"@ {original_start:.1f}s returned unparseable response "
                f"(text head: {text[:200]!r}). Falling through with original ad."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="failure",
                    original_start=original_start, original_end=original_end,
                    reasoning="Unparseable LLM response",
                    model_used=model, latency_ms=latency_ms, success=False,
                ),
                ad,
            )

        # Empty array carries the rejection signal in both pools: the LLM
        # decided this segment is not (or no longer) an ad to cut.
        if not ads_returned:
            verdict = "reject"
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict=verdict,
                    original_start=original_start, original_end=original_end,
                    reasoning=None, confidence=None,
                    model_used=model, latency_ms=latency_ms, success=True,
                ),
                ad,
            )

        # One or more elements: take the first. Multi-element responses are
        # not expected (one ad in, one ad out) but are handled defensively.
        kept = ads_returned[0]
        if not isinstance(kept, dict):
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer {window_label} returned "
                f"non-object array element. Falling through with original ad."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="failure",
                    original_start=original_start, original_end=original_end,
                    reasoning="Array element is not an object",
                    model_used=model, latency_ms=latency_ms, success=False,
                ),
                ad,
            )

        raw_is_ad = kept.get("is_ad")
        structured_is_ad = raw_is_ad if isinstance(raw_is_ad, bool) else None
        if structured_is_ad is False:
            # Structured whole-span rejection wins over any returned bounds.
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="reject",
                    original_start=original_start, original_end=original_end,
                    reasoning=kept.get("reason"), confidence=None,
                    model_used=model, latency_ms=latency_ms, success=True,
                    structured_is_ad=False,
                ),
                ad,
            )

        # Schema asks for start/end; fall back to corrected_/adjusted_ only when
        # the model omits them (some responses carry the correction there).
        new_start = _first_num(
            kept, ("start", "corrected_start", "adjusted_start"), original_start)
        new_end = _first_num(
            kept, ("end", "corrected_end", "adjusted_end"), original_end)
        reason = kept.get("reason")
        try:
            confidence = float(kept["confidence"]) if "confidence" in kept else None
        except (TypeError, ValueError):
            confidence = None

        clamped_start, clamped_end = self._clamp_proposed_bounds(
            ad, new_start, new_end, original_start, original_end,
            max_shift, slug, episode_id)

        # Verdict is derived from the boundary delta, not from the LLM.
        delta_start = clamped_start - original_start
        delta_end = clamped_end - original_end
        unchanged = (
            abs(delta_start) <= _CONFIRMED_BOUNDARY_TOLERANCE_S
            and abs(delta_end) <= _CONFIRMED_BOUNDARY_TOLERANCE_S
        )
        # Log every non-zero LLM-proposed shift, even when rounded to
        # confirmed, so we can see the distribution of adjustments the model
        # is making vs the tolerance floor.
        if delta_start != 0 or delta_end != 0:
            disposition = "rounded to confirmed" if unchanged else "applied as adjust"
            logger.info(
                f"[{slug}:{episode_id}] Reviewer @ {original_start:.1f}-"
                f"{original_end:.1f}s proposed delta start={delta_start:+.2f}s "
                f"end={delta_end:+.2f}s ({disposition})"
            )
        if pool == "resurrection":
            verdict = "resurrect"
        elif unchanged:
            verdict = "confirmed"
        else:
            verdict = "adjust"

        if verdict == "adjust":
            _warn_prose_boundary_mismatch(
                reason, clamped_start, clamped_end,
                slug=slug, episode_id=episode_id,
            )
            updated = _adjusted_ad_copy(
                ad, clamped_start, clamped_end, original_start, original_end,
                reason, confidence, model)
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="adjust",
                    original_start=original_start, original_end=original_end,
                    adjusted_start=clamped_start, adjusted_end=clamped_end,
                    reasoning=reason, confidence=confidence,
                    model_used=model, latency_ms=latency_ms, success=True,
                    structured_is_ad=structured_is_ad,
                ),
                updated,
            )

        return (
            ReviewVerdict(
                pool=pool, pass_num=pass_num, verdict=verdict,
                original_start=original_start, original_end=original_end,
                reasoning=reason, confidence=confidence,
                model_used=model, latency_ms=latency_ms, success=True,
                structured_is_ad=structured_is_ad,
            ),
            ad,
        )

    def _clamp_proposed_bounds(self, ad, new_start, new_end,
                               original_start, original_end, max_shift,
                               slug, episode_id):
        """Clamp reviewer-proposed bounds: inverted-bounds fallback, per-edge
        shift cap, merged-span floor, final validity fallback. Single seam for
        every path that turns reviewer prose or deltas into marker bounds."""
        if new_end <= new_start:
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer proposed inverted boundaries "
                f"({new_start:.1f}s >= {new_end:.1f}s) @ original "
                f"{original_start:.1f}-{original_end:.1f}s. Keeping original."
            )
            new_start, new_end = original_start, original_end

        clamped_start = self._clamp_to_cap(new_start, original_start, max_shift)
        clamped_end = self._clamp_to_cap(new_end, original_end, max_shift)
        if clamped_start != new_start or clamped_end != new_end:
            logger.info(
                f"[{slug}:{episode_id}] Reviewer adjust clamped from "
                f"{new_start:.1f}-{new_end:.1f} to "
                f"{clamped_start:.1f}-{clamped_end:.1f} (cap {max_shift}s)"
            )

        # A merged ad's [start, end] is the union of multiple independently
        # detected sub-ads. The reviewer must not pull a boundary inward
        # past a transcript-anchored member (it would silently drop a
        # confirmed sub-ad), but differential/VAD members carry
        # alignment-derived padding whose trimming is exactly the
        # reviewer's job. Markers persisted before member tracking carry
        # the flag without the protected keys; those keep the old blanket
        # expand-only rule.
        if ad.get('merged_distinct_ads'):
            if 'merged_protected_start' in ad:
                p_start = ad.get('merged_protected_start')
                p_end = ad.get('merged_protected_end')
            else:
                p_start, p_end = original_start, original_end
            floor_start = (clamped_start if p_start is None
                           else min(clamped_start, p_start))
            floor_end = (clamped_end if p_end is None
                         else max(clamped_end, p_end))
            if floor_start != clamped_start or floor_end != clamped_end:
                logger.info(
                    f"[{slug}:{episode_id}] Reviewer inward shrink clamped "
                    f"to protected members on merged ad @ "
                    f"{original_start:.1f}-{original_end:.1f}s: "
                    f"{clamped_start:.1f}-{clamped_end:.1f} -> "
                    f"{floor_start:.1f}-{floor_end:.1f}"
                )
            clamped_start, clamped_end = floor_start, floor_end

        # Cross-fetch evidence remains authoritative inside a merged
        # candidate. The reviewer may trim coarse LLM/VAD extensions outside
        # these measured regions, but cannot leave part of an inserted block
        # in the published episode.
        core_start, core_end = dai_core_bounds(ad)
        if core_start is not None:
            floor_start = min(clamped_start, core_start)
            floor_end = max(clamped_end, core_end)
            if floor_start != clamped_start or floor_end != clamped_end:
                logger.info(
                    f"[{slug}:{episode_id}] Reviewer inward shrink clamped "
                    f"to DAI core @ {core_start:.1f}-{core_end:.1f}s: "
                    f"{clamped_start:.1f}-{clamped_end:.1f} -> "
                    f"{floor_start:.1f}-{floor_end:.1f}"
                )
            clamped_start, clamped_end = floor_start, floor_end

        if clamped_end <= clamped_start:
            clamped_start, clamped_end = original_start, original_end
        return clamped_start, clamped_end

    def _recover_contradiction_trim(
        self,
        verdict: "ReviewVerdict",
        *,
        ad: dict,
        segments: list[dict],
        model: str,
        pass_num: int,
        slug: str | None,
        episode_id: str | None,
    ) -> tuple[float, float] | None:
        """Recover machine-readable trim bounds from a prose-only trim.

        Fired only on a contradiction hold whose verdict derived as
        'confirmed': the model returned the span unchanged while its
        reasoning described a trim in prose (the-brilliant-idiots
        79eedd7bf2a7 shipped "the ad content ends at roughly 65.8s ... must
        be trimmed off the end" with boundaries 0.0-87.8s intact, so the
        hold carried no proposed bounds the UI could one-tap approve).

        One small follow-up call through the shared call_llm seam turns the
        reasoning back into {"ad_start": float, "ad_end": float}. Returns
        (start, end) strictly inside the original span, or None on any
        failure; it never raises into the pipeline.

        Two call sites share this recovery: the contradiction-hold branch
        uses the result only to enrich the held marker's proposed bounds
        (the ad stays held either way); the affirmed-confirm branch applies
        the result as an adjust, accepting the ad with the recovered bounds.
        """
        # Merged spans: recovery may trim differential/VAD padding but must
        # not sever a transcript-anchored member. Legacy markers without
        # member tracking keep the old skip.
        if ad.get('merged_distinct_ads') and 'merged_protected_start' not in ad:
            logger.info(
                f"[{slug}:{episode_id}] Trim recovery skipped on legacy "
                f"merged ad @ {verdict.original_start:.1f}-"
                f"{verdict.original_end:.1f}s (no member tracking)"
            )
            return None
        reasoning = verdict.reasoning or ""
        if not _TRIM_LANGUAGE_RE.search(reasoning):
            return None
        o_start, o_end = verdict.original_start, verdict.original_end
        user_prompt = (
            f"Original candidate span: {o_start:.2f}s - {o_end:.2f}s\n"
            f"Reviewer reasoning:\n{reasoning}\n"
        )
        pass_name = PASS_REVIEWER_1 if pass_num == 1 else PASS_REVIEWER_2
        call_label = f"reviewer-pass{pass_num}-trim-recovery"
        try:
            response, error = call_llm(
                llm_client=self._llm_client,
                model=model,
                system_prompt=_TRIM_RECOVERY_SYSTEM_PROMPT,
                prompt=user_prompt,
                llm_timeout=get_llm_timeout(),
                max_retries=0,
                max_tokens=300,
                slug=slug,
                episode_id=episode_id,
                call_label=call_label,
                pass_name=pass_name,
            )
        except Exception as e:
            # call_llm never raises by contract; belt-and-braces so a bug
            # there can never break the hold path.
            logger.warning(f"[{slug}:{episode_id}] {call_label} raised: {e}")
            return None
        if response is None:
            logger.warning(
                f"[{slug}:{episode_id}] {call_label} failed: {error}. "
                f"Holding without proposed bounds."
            )
            return None
        obj, _method = extract_json_object(
            self._extract_response_text(response),
            slug=slug, episode_id=episode_id,
        )
        if not obj:
            # Explicit null (no sub-span identified) or unparseable response.
            return None
        start = _first_num(obj, ("ad_start",), math.nan)
        end = _first_num(obj, ("ad_end",), math.nan)
        if math.isnan(start) or math.isnan(end):
            return None
        if (start < o_start - _TRIM_RANGE_EPSILON_S
                or end > o_end + _TRIM_RANGE_EPSILON_S):
            logger.warning(
                f"[{slug}:{episode_id}] {call_label} out of range: "
                f"{start:.1f}-{end:.1f}s vs span {o_start:.1f}-{o_end:.1f}s. "
                f"Rejecting."
            )
            return None
        start = max(start, o_start)
        end = min(end, o_end)
        if end <= start:
            return None
        # Widen back out to the protected union so a tracked merged ad's
        # recovered trim cannot sever a transcript-anchored member.
        if ad.get('merged_distinct_ads'):
            p_start = ad.get('merged_protected_start')
            p_end = ad.get('merged_protected_end')
            if p_start is not None:
                start = min(start, p_start)
            if p_end is not None:
                end = max(end, p_end)
        core_start, core_end = dai_core_bounds(ad)
        if core_start is not None:
            start = min(start, core_start)
            end = max(end, core_end)
        # Protected merge members or measured DAI evidence can widen the
        # recovered proposal back to the full marker. That is no longer a
        # trim, so neither review path should surface it as one.
        if (start - o_start) + (o_end - end) <= _CONFIRMED_BOUNDARY_TOLERANCE_S:
            return None
        # Evaluate the render floor after protected bounds expand the model's
        # raw proposal. A tiny proposal inside a substantial measured core is
        # still a valid core-sized trim.
        if end - start < MIN_AD_DURATION_FOR_REMOVAL:
            logger.info(
                f"[{slug}:{episode_id}] {call_label} recovered trim "
                f"{start:.1f}-{end:.1f}s is {end - start:.1f}s, under the "
                f"{MIN_AD_DURATION_FOR_REMOVAL:.0f}s floor for span "
                f"{o_start:.1f}-{o_end:.1f}s. Holding without bounds."
            )
            return None
        logger.info(
            f"[{slug}:{episode_id}] {call_label} recovered proposed trim "
            f"{start:.1f}-{end:.1f}s from span {o_start:.1f}-{o_end:.1f}s"
        )
        return start, end

    @staticmethod
    def _clamp_to_cap(proposed: float, original: float, cap: int) -> float:
        delta = proposed - original
        if delta > cap:
            return original + cap
        if delta < -cap:
            return original - cap
        return proposed

    def _build_user_prompt(
        self,
        *,
        ad: dict,
        segments: list[dict],
        episode_meta: dict,
        pool: str,
        max_shift: int = 60,
    ) -> str:
        """Build the per-ad user prompt.

        Mirrors detection's minimal-prose shape (Podcast / Episode /
        description / Transcript) so the LLM emits a JSON array of ad
        segments rather than inventing its own analysis schema. The
        candidate ad is called out inline inside the transcript with
        brackets, the same way detection presents segments. The system
        prompt examples then drive the output shape.
        """
        start = float(ad.get("start", 0.0))
        end = float(ad.get("end", 0.0))
        before_text = get_transcript_text_for_range(
            segments, max(0.0, start - 60.0), start
        )
        # Per-segment timestamped lines so every candidate sentence carries
        # its boundary: with only the two span-edge anchors the model cannot
        # emit an exact trim timestamp for a sentence it names (the-tim-dillon-
        # show a55cb5b8216d trimmed to an interpolated 20.0s when the ad's
        # final sentence ended at 28.4s). Context blocks stay stripped: they
        # are boundary-adjacent only and can be long.
        ad_text = get_timestamped_transcript_for_range(segments, start, end)
        if not ad_text:
            fallback = ad.get("end_text", "") or ""
            ad_text = f"[{start:.1f}s-{end:.1f}s] {fallback}" if fallback else ""
        after_text = get_transcript_text_for_range(segments, end, end + 60.0)

        podcast_name = episode_meta.get("podcast_name", "Unknown")
        episode_title = episode_meta.get("episode_title", "Unknown")
        episode_description = episode_meta.get("episode_description", "") or ""
        podcast_description = episode_meta.get("podcast_description", "") or ""

        if self._sponsor_history_provider:
            try:
                history = self._sponsor_history_provider(episode_meta.get("slug"))
                if history:
                    podcast_description = (
                        podcast_description + "\n" + history
                        if podcast_description else history
                    )
            except Exception as e:
                logger.warning(f"sponsor history provider failed: {e}")

        description_section = ""
        if podcast_description or episode_description:
            description_section = (
                f"Podcast description: {podcast_description}\n"
                f"Episode description: {episode_description}\n"
            )

        if pool == "resurrection":
            framing = (
                f"This segment was rejected for low confidence by the validator. "
                f"Decide whether it should be cut after all.\n"
                f"Original boundaries: {start:.2f}s - {end:.2f}s.\n"
            )
        else:
            framing = (
                f"This is the candidate ad to review.\n"
                f"Original boundaries: {start:.2f}s - {end:.2f}s.\n"
            )

        cue_section = _format_cue_section(
            audio_analysis=episode_meta.get('audio_analysis'),
            ad_start=start,
            ad_end=end,
            cue_pair=ad.get('cue_pair'),
            cue_snap=ad.get('cue_snap'),
            silence_snap=ad.get('silence_snap'),
            bucket_radius=float(max_shift),
        )

        return (
            f"Podcast: {podcast_name}\n"
            f"Episode: {episode_title}\n"
            f"{description_section}\n"
            f"{framing}\n"
            f"{cue_section}"
            f"Transcript (60s before, the candidate ad, 60s after; candidate "
            f"lines carry [start-end] second timestamps):\n"
            f"[{max(0.0, start - 60.0):.1f}s] {before_text}\n"
            f">>> CANDIDATE AD START [{start:.1f}s] >>>\n"
            f"{ad_text}\n"
            f"<<< CANDIDATE AD END [{end:.1f}s] <<<\n"
            f"[{end:.1f}s] {after_text}\n"
        )

    def _render_review_prompt(self, max_shift: int, sponsor_block: str) -> str:
        prompt = self._read_setting("review_prompt") or DEFAULT_REVIEW_PROMPT
        rendered = render_prompt(
            prompt,
            sponsor_database=sponsor_block,
            max_boundary_shift_seconds=str(max_shift),
        )
        if "{max_boundary_shift_seconds}" not in prompt:
            rendered = (
                f"{rendered}\n\nBoundary cap: any start or "
                f"end must be within {max_shift} seconds of the "
                f"original detected boundaries."
            )
        return self._apply_pass_override(rendered, "review_prompt_override")

    def _render_resurrect_prompt(self, sponsor_block: str) -> str:
        prompt = self._read_setting("resurrect_prompt") or DEFAULT_RESURRECT_PROMPT
        rendered = render_prompt(prompt, sponsor_database=sponsor_block)
        return self._apply_pass_override(rendered, "resurrect_prompt_override")

    def _apply_pass_override(self, rendered: str, setting_key: str) -> str:
        """Append the user's per-pass override (empty by default -> no change)."""
        return apply_override(rendered, self._read_setting(setting_key))

    def _sponsor_list_or_empty(self) -> str:
        if not self.sponsor_service:
            return ""
        try:
            return self.sponsor_service.get_claude_sponsor_list() or ""
        except Exception as e:
            logger.warning(f"reviewer sponsor list lookup failed: {e}")
            return ""

    def _read_setting(self, key: str) -> str | None:
        try:
            return self.db.get_setting(key)
        except Exception:
            return None

    def _read_max_boundary_shift(self) -> int:
        raw = self._read_setting("review_max_boundary_shift")
        try:
            return max(1, int(raw)) if raw is not None else 60
        except (TypeError, ValueError):
            return 60

    def _resolve_model(self, pass_model: str) -> str:
        # Same same_as_pass rule as tools.reviewer_calibration._resolve_calibration_model,
        # which resolves the pass model from settings instead of the live run.
        configured = self._read_setting("review_model") or "same_as_pass"
        if configured == "same_as_pass":
            return pass_model
        return configured

    @staticmethod
    def _extract_response_text(response) -> str:
        """Pull the response body text.

        ``LLMClient.messages_create`` returns an ``LLMResponse`` whose
        ``content`` is already the extracted string. Anthropic SDK responses
        instead carry ``content`` as a list of TextBlocks. Handle both, and
        fall through to ``response.text`` for any other shape; never call
        ``str(response)`` since a dataclass repr produces literal ``\\n``
        escape sequences that look like JSON but break the parser.
        """
        content = getattr(response, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content:
            first = content[0]
            if hasattr(first, "text"):
                return first.text
            if isinstance(first, dict) and "text" in first:
                return first["text"]
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        return ""

    def _flush_log(self, verdicts: list[ReviewVerdict], episode_meta: dict) -> None:
        """Write all ad_reviewer_log rows in one transaction. Failures here are
        logged and dropped - audit logging is not on the critical path."""
        if not verdicts:
            return
        episode_id = episode_meta.get("episode_id")
        podcast_id = episode_meta.get("podcast_id")
        rows = [
            (
                episode_id, podcast_id, v.pass_num, v.pool,
                v.original_start, v.original_end, v.verdict,
                v.adjusted_start, v.adjusted_end,
                v.reasoning, v.confidence, v.model_used,
                v.latency_ms, 1 if v.success else 0,
            )
            for v in verdicts
        ]
        conn = None
        try:
            conn = self.db.get_connection()
            conn.executemany(
                """INSERT INTO ad_reviewer_log
                   (episode_id, podcast_id, pass, pool, original_start,
                    original_end, verdict, adjusted_start, adjusted_end,
                    reasoning, confidence, model_used, latency_ms, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.commit()
        except Exception as e:
            if conn is not None:
                conn.rollback()
            logger.warning(f"reviewer log write failed: {e}")


def split_resurrection_pool(
    all_ads_with_validation: list[dict],
    ads_to_remove: list[dict],
    min_cut_confidence: float,
) -> list[dict]:
    """Identify ads eligible for resurrection.

    An ad is eligible when:
    - It is in `all_ads_with_validation` but NOT in the cut list
      (`ads_to_remove`) - i.e. the validator/confidence gate kept it out
    - Its confidence falls in the resurrection band:
      ``[min_cut_confidence - RESURRECT_BAND_WIDTH, min_cut_confidence)``
    - Its validation decision did not stack non-confidence rejection reasons
      (duration violations, transcript mismatches, density violations, FP
      corrections all disqualify)

    Returns a fresh list of dicts (does not mutate inputs).
    """
    cut_keys = {(a.get("start"), a.get("end")) for a in ads_to_remove}
    band_low = max(0.0, min_cut_confidence - RESURRECT_BAND_WIDTH)
    band_high = min_cut_confidence
    eligible = []
    for ad in all_ads_with_validation:
        key = (ad.get("start"), ad.get("end"))
        if key in cut_keys:
            continue
        # Never resurrect a held ad: a duration-hold sits in the resurrection
        # band and a resurrect verdict would silently un-hold it.
        if ad.get("held_for_review"):
            continue
        validation = ad.get("validation") or {}
        confidence = validation.get("adjusted_confidence", ad.get("confidence"))
        try:
            confidence = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            continue
        if not (band_low <= confidence < band_high):
            continue
        if _has_disqualifying_reasons(validation):
            continue
        eligible.append(ad)
    return eligible


def _has_disqualifying_reasons(validation: dict) -> bool:
    """Return True if validator flags indicate a non-confidence rejection.

    Matches the prefix scheme `ad_validator.py` emits into
    ``validation['flags']``: ERROR-level flags are structural/quality issues
    the reviewer cannot fix (duration violations, "not an ad" marker), and
    "User marked as false positive" is a definitive user opt-out. Confidence
    flags are intentionally NOT disqualifying since the reviewer's whole
    purpose is to second-guess them.
    """
    flags = validation.get('flags') or []
    if isinstance(flags, str):
        flags = [flags]
    for flag in flags:
        text = str(flag)
        if text.startswith('ERROR:') and 'confidence' not in text.lower():
            return True
        if 'user marked as false positive' in text.lower():
            return True
    return False

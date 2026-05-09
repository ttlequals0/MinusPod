"""Opt-in LLM ad reviewer."""
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional, Tuple

from database import DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT
from llm_client import get_llm_max_retries, get_llm_timeout
from utils.llm_call import call_llm_for_window
from utils.llm_response import extract_json_object, find_first_dict_with_key
from utils.prompt import format_sponsor_block, render_prompt
from utils.text import get_transcript_text_for_range


Verdict = Literal["confirmed", "adjust", "reject", "resurrect", "failure"]

logger = logging.getLogger(__name__)


# How wide the resurrection band is below the user's min_cut_confidence
# (e.g. threshold 0.80 -> resurrection eligible if 0.60 <= confidence < 0.80).
RESURRECT_BAND_WIDTH = 0.20

# Per-ad LLM response is small (one JSON object). Cap so an unbounded model
# response cannot eat a whole detection budget.
REVIEW_MAX_TOKENS = 1024

# Strict normalization map for the verdict field. Keys are lowercased; values
# are the canonical verdict identifiers used in the rest of the codebase.
_VERDICT_NORMALIZATION = {
    "confirmed": "confirmed",
    "confirm": "confirmed",
    "adjust": "adjust",
    "adjust_boundary": "adjust",
    "boundary_adjust": "adjust",
    "adjusted": "adjust",
    "reject": "reject",
    "rejected": "reject",
    "false_positive": "reject",
    "resurrect": "resurrect",
    "resurrected": "resurrect",
    "rescue": "resurrect",
}


@dataclass
class ReviewVerdict:
    """Per-ad reviewer outcome."""
    pool: str  # "accepted" or "resurrection"
    pass_num: int  # 1 or 2
    verdict: Verdict
    original_start: float
    original_end: float
    adjusted_start: Optional[float] = None
    adjusted_end: Optional[float] = None
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    model_used: str = ""
    latency_ms: int = 0
    success: bool = True


@dataclass
class ReviewResult:
    """Full output of one reviewer invocation over both pools.

    `accepted_after_review` is the post-reviewer cut list (adjustments applied,
    rejections removed, resurrections added). `verdicts` is the full audit
    trail, one entry per ad the reviewer evaluated.
    """
    accepted_after_review: List[Dict] = field(default_factory=list)
    rejected_by_reviewer: List[Dict] = field(default_factory=list)
    resurrected: List[Dict] = field(default_factory=list)
    verdicts: List[ReviewVerdict] = field(default_factory=list)


class AdReviewer:
    """Reviews detector + validator output before audio cuts are applied."""

    def __init__(
        self,
        db,
        llm_client,
        sponsor_service=None,
        sponsor_history_provider: Optional[Callable[[str], str]] = None,
    ):
        self.db = db
        self._llm_client = llm_client
        self.sponsor_service = sponsor_service
        self._sponsor_history_provider = sponsor_history_provider

    def review(
        self,
        accepted_ads: List[Dict],
        resurrection_eligible: List[Dict],
        segments: List[Dict],
        episode_meta: Dict,
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
        accepted_ads: List[Dict],
        resurrection_eligible: List[Dict],
        segments: List[Dict],
        episode_meta: Dict,
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

        for ad in accepted_ads:
            verdict, updated_ad = self._review_single(
                ad=ad,
                pool="accepted",
                pass_num=pass_num,
                segments=segments,
                episode_meta=episode_meta,
                system_prompt=review_prompt,
                model=model,
                max_shift=max_shift,
            )
            result.verdicts.append(verdict)

            if verdict.verdict == "reject":
                marked = dict(updated_ad)
                marked["was_cut"] = False
                marked["reviewer_verdict"] = "reject"
                marked["reviewer_reasoning"] = verdict.reasoning
                marked["reviewer_confidence"] = verdict.confidence
                marked["reviewer_model"] = verdict.model_used
                marked["source"] = "reviewer"
                result.rejected_by_reviewer.append(marked)
            else:
                # confirmed/adjust/failure stay in cut list; _review_single
                # already mutated boundaries for adjust.
                result.accepted_after_review.append(updated_ad)

        for ad in resurrection_eligible:
            verdict, updated_ad = self._review_single(
                ad=ad,
                pool="resurrection",
                pass_num=pass_num,
                segments=segments,
                episode_meta=episode_meta,
                system_prompt=resurrect_prompt,
                model=model,
                max_shift=max_shift,
            )
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

    def _review_single(
        self,
        *,
        ad: Dict,
        pool: str,
        pass_num: int,
        segments: List[Dict],
        episode_meta: Dict,
        system_prompt: str,
        model: str,
        max_shift: int,
    ) -> Tuple[ReviewVerdict, Dict]:
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
        )
        slug = episode_meta.get("slug")
        episode_id = episode_meta.get("episode_id")
        window_label = f"reviewer-pass{pass_num}-{pool}"

        t0 = time.monotonic()
        response, error = call_llm_for_window(
            llm_client=self._llm_client,
            model=model,
            system_prompt=system_prompt,
            prompt=user_prompt,
            llm_timeout=get_llm_timeout(),
            max_retries=get_llm_max_retries(),
            max_tokens=REVIEW_MAX_TOKENS,
            slug=slug,
            episode_id=episode_id,
            window_label=window_label,
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
                    reasoning=f"LLM call failed: {error}",
                    model_used=model, latency_ms=latency_ms, success=False,
                ),
                ad,
            )

        text = self._extract_response_text(response)
        parsed, _method = extract_json_object(text, slug=slug, episode_id=episode_id)
        # LLMs sometimes wrap the verdict in extra metadata fields or nest it
        # inside an `ads_reviewed: [...]` array despite the prompt asking for
        # a single object. Walk the parsed value to recover the verdict.
        verdict_obj = (
            parsed if isinstance(parsed, dict) and "verdict" in parsed
            else find_first_dict_with_key(parsed, "verdict") if parsed is not None
            else None
        )
        if verdict_obj is None:
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
        parsed = verdict_obj

        raw_verdict = str(parsed.get("verdict", "")).strip().lower()
        canonical = _VERDICT_NORMALIZATION.get(raw_verdict)
        reasoning = parsed.get("reasoning")
        confidence = parsed.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        if canonical is None:
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer {window_label} returned "
                f"unknown verdict {raw_verdict!r}. Treating as failure."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="failure",
                    original_start=original_start, original_end=original_end,
                    reasoning=f"Unknown verdict: {raw_verdict}",
                    confidence=confidence, model_used=model,
                    latency_ms=latency_ms, success=False,
                ),
                ad,
            )

        # Pool gates verdict set: accepted pool may not return resurrect, and
        # resurrection pool may not return confirmed/adjust. Coerce illegal
        # cross-pool verdicts to safe defaults rather than fabricating data.
        if pool == "accepted" and canonical == "resurrect":
            canonical = "confirmed"
        if pool == "resurrection" and canonical in ("confirmed", "adjust"):
            canonical = "resurrect"

        if canonical == "adjust":
            return self._apply_adjust(
                ad=ad,
                parsed=parsed,
                pool=pool,
                pass_num=pass_num,
                original_start=original_start,
                original_end=original_end,
                reasoning=reasoning,
                confidence=confidence,
                model=model,
                latency_ms=latency_ms,
                max_shift=max_shift,
                slug=slug,
                episode_id=episode_id,
            )

        return (
            ReviewVerdict(
                pool=pool, pass_num=pass_num, verdict=canonical,
                original_start=original_start, original_end=original_end,
                reasoning=reasoning, confidence=confidence,
                model_used=model, latency_ms=latency_ms, success=True,
            ),
            ad,
        )

    def _apply_adjust(
        self,
        *,
        ad: Dict,
        parsed: Dict,
        pool: str,
        pass_num: int,
        original_start: float,
        original_end: float,
        reasoning: Optional[str],
        confidence: Optional[float],
        model: str,
        latency_ms: int,
        max_shift: int,
        slug: Optional[str],
        episode_id: Optional[str],
    ) -> Tuple[ReviewVerdict, Dict]:
        try:
            new_start = float(parsed.get("adjusted_start", original_start))
            new_end = float(parsed.get("adjusted_end", original_end))
        except (TypeError, ValueError):
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer adjust verdict "
                f"@ {original_start:.1f}s missing/invalid adjusted_start/end. "
                f"Treating as confirmed."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="confirmed",
                    original_start=original_start, original_end=original_end,
                    reasoning=reasoning, confidence=confidence,
                    model_used=model, latency_ms=latency_ms, success=True,
                ),
                ad,
            )

        if new_end <= new_start:
            logger.warning(
                f"[{slug}:{episode_id}] Reviewer proposed inverted boundaries "
                f"({new_start:.1f}s >= {new_end:.1f}s) @ original "
                f"{original_start:.1f}-{original_end:.1f}s. Treating as confirmed."
            )
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="confirmed",
                    original_start=original_start, original_end=original_end,
                    reasoning=reasoning, confidence=confidence,
                    model_used=model, latency_ms=latency_ms, success=True,
                ),
                ad,
            )

        clamped_start = self._clamp_to_cap(new_start, original_start, max_shift)
        clamped_end = self._clamp_to_cap(new_end, original_end, max_shift)
        if clamped_start != new_start or clamped_end != new_end:
            logger.info(
                f"[{slug}:{episode_id}] Reviewer adjust clamped from "
                f"{new_start:.1f}-{new_end:.1f} to "
                f"{clamped_start:.1f}-{clamped_end:.1f} (cap {max_shift}s)"
            )

        if clamped_end <= clamped_start:
            return (
                ReviewVerdict(
                    pool=pool, pass_num=pass_num, verdict="confirmed",
                    original_start=original_start, original_end=original_end,
                    reasoning=reasoning, confidence=confidence,
                    model_used=model, latency_ms=latency_ms, success=True,
                ),
                ad,
            )

        adjusted_ad = dict(ad)
        adjusted_ad["start"] = clamped_start
        adjusted_ad["end"] = clamped_end
        adjusted_ad["reviewer_verdict"] = "adjust"
        adjusted_ad["reviewer_original_start"] = original_start
        adjusted_ad["reviewer_original_end"] = original_end
        adjusted_ad["reviewer_reasoning"] = reasoning
        adjusted_ad["reviewer_confidence"] = confidence
        adjusted_ad["reviewer_model"] = model

        return (
            ReviewVerdict(
                pool=pool, pass_num=pass_num, verdict="adjust",
                original_start=original_start, original_end=original_end,
                adjusted_start=clamped_start, adjusted_end=clamped_end,
                reasoning=reasoning, confidence=confidence,
                model_used=model, latency_ms=latency_ms, success=True,
            ),
            adjusted_ad,
        )

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
        ad: Dict,
        segments: List[Dict],
        episode_meta: Dict,
        pool: str,
    ) -> str:
        start = float(ad.get("start", 0.0))
        end = float(ad.get("end", 0.0))
        before_text = get_transcript_text_for_range(
            segments, max(0.0, start - 60.0), start
        )
        ad_text = get_transcript_text_for_range(segments, start, end) or (
            ad.get("end_text", "") or ""
        )
        after_text = get_transcript_text_for_range(segments, end, end + 60.0)

        sponsor = ad.get("sponsor") or ad.get("brand") or "(unknown)"
        reason = ad.get("reason") or ad.get("reasoning") or "(no reason recorded)"
        validator_confidence = ad.get("confidence")

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

        validator_line = ""
        if pool == "resurrection" and validator_confidence is not None:
            validator_line = (
                f"VALIDATOR CONFIDENCE: {float(validator_confidence):.2f} "
                f"(below cut threshold)\n"
            )

        return (
            f"Podcast: {podcast_name}\n"
            f"Episode: {episode_title}\n"
            f"Podcast description: {podcast_description}\n"
            f"Episode description: {episode_description}\n\n"
            f"DETECTED AD:\n"
            f"Start: {start:.2f}s\n"
            f"End: {end:.2f}s\n"
            f"Sponsor: {sponsor}\n"
            f"Detector reasoning: {reason}\n"
            f"{validator_line}\n"
            f"TRANSCRIPT (60s before ad):\n{before_text}\n\n"
            f"DETECTED AD TRANSCRIPT:\n{ad_text}\n\n"
            f"TRANSCRIPT (60s after ad):\n{after_text}\n"
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
                f"{rendered}\n\nBoundary cap: any adjusted_start or "
                f"adjusted_end must be within {max_shift} seconds of the "
                f"original detected boundaries."
            )
        return rendered

    def _render_resurrect_prompt(self, sponsor_block: str) -> str:
        prompt = self._read_setting("resurrect_prompt") or DEFAULT_RESURRECT_PROMPT
        return render_prompt(prompt, sponsor_database=sponsor_block)

    def _sponsor_list_or_empty(self) -> str:
        if not self.sponsor_service:
            return ""
        try:
            return self.sponsor_service.get_claude_sponsor_list() or ""
        except Exception as e:
            logger.warning(f"reviewer sponsor list lookup failed: {e}")
            return ""

    def _read_setting(self, key: str) -> Optional[str]:
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

    def _flush_log(self, verdicts: List[ReviewVerdict], episode_meta: Dict) -> None:
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
            logger.warning(f"reviewer log write failed: {e}")


def split_resurrection_pool(
    all_ads_with_validation: List[Dict],
    ads_to_remove: List[Dict],
    min_cut_confidence: float,
) -> List[Dict]:
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


def _has_disqualifying_reasons(validation: Dict) -> bool:
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

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any

from systemone.spans import spans_from_probabilities

INPUT_COST_PER_MTOK = 0.042
CHOICE_PROBABILITY_SUM_TOLERANCE = 0.01
CHOICE_PROBABILITY_SUM_EPSILON = 1e-12
TYPESAFE_MAX_CHOICE_OPTIONS = 255
MAX_QUESTIONS_PER_REQUEST = None
MAX_REQUEST_BYTES = None

GUIDANCE = (
    "Each line of `transcript` is one segment of a podcast episode, prefixed "
    "with its line id. A line is ADVERTISING when it is a sponsor read, a "
    "produced ad spot, a dynamically inserted ad, a hosting-platform pre-roll "
    "or post-roll, a cross-promotion for another show, or a produced segment "
    "asking listeners to subscribe, rate, or follow. Signs of advertising: a "
    "sponsor or brand name, a URL, a promo code, a product pitch, a call to "
    "action, or concentrated marketing copy that is tonally separate from the "
    "conversation. A line is EDITORIAL CONTENT when it is the host or a guest "
    "discussing the episode's subject. A guest talking about their own book or "
    "project, and the host mentioning their own show or Patreon in passing "
    "during conversation, are editorial content, not advertising."
)
CATEGORY_GUIDANCE = (
    "Each line of `transcript` is one segment around a single advertising "
    "break in a podcast, prefixed with its line id. The lines named in "
    "`focus` are the break itself; the others are nearby context. Decide what "
    "KIND of break the focus lines are."
)
NOUL_INSTRUCTIONS = "Line {lid} of `transcript` is advertising, not editorial content."
CATEGORY_CHOICE_KEY = "category"
CATEGORY_CHOICE_INSTRUCTIONS = (
    "Choose the one category that best describes the advertising break at lines {ids}. "
    "Use the supplied transcript context."
)
CATEGORY_DESCRIPTIONS = {
    "sponsor": "a paid sponsor read, product advertisement, or paid ad for another podcast or show",
    "cross_promo": "an unpaid promotion for another podcast or show in the same network or by the same host",
    "self_promo": "the host promoting their own show, Patreon, merch, membership, or back catalog",
    "interaction": "a call to action to like, subscribe, rate, review, follow, or comment",
    "intro": "an intro segment opening the episode",
    "outro": "an outro segment closing the episode",
    "recap": "a recap or summary of earlier content",
}


class JevReviewValidationError(ValueError):
    code = "jev_upstream_invalid_response"
    _RULES = frozenset({
        "response_object", "answers_object", "answers_keys", "answer_object",
        "evidence_probability", "choice_criteria_object", "choice_criteria_empty",
        "choice_option_limit", "choice_answer_shape", "choice_confidence",
        "choice_probability", "choice_probability_keys", "choice_probability_sum",
        "usage_object", "usage_input_tokens", "usage_output_tokens", "truncated_response",
    })
    _DETAILS = frozenset({"expected_count", "actual_count", "actual", "expected_total", "actual_total", "tolerance"})

    def __init__(self, rule: str, numeric_details: dict[str, int | float] | None = None):
        self.rule = rule if rule in self._RULES else "response_object"
        self.numeric_details = self._safe_details(numeric_details or {})
        super().__init__(f"upstream response validation failed: {self.rule}")

    @classmethod
    def _safe_details(cls, details: dict[str, int | float]) -> dict[str, int | float]:
        safe = {}
        for name, value in details.items():
            if name not in cls._DETAILS or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            numeric = float(value)
            if math.isfinite(numeric):
                safe[name] = value
        return safe


class JevCategoryValidationError(JevReviewValidationError):
    pass


class RequestLimitError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _usage_tokens(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and int(value) == value:
        return int(value)
    rule = "usage_input_tokens" if key == "input_tokens" else "usage_output_tokens"
    raise JevReviewValidationError(rule)


def _usage_cost(usage: dict[str, Any]) -> float | None:
    value = _finite_float(usage.get("cost"))
    return value if value is not None and value >= 0 else None


def _aggregate_usage(values: list[int | None]) -> int | None:
    return sum(values) if values and all(value is not None for value in values) else None


def _aggregate_cost(values: list[float | None]) -> float | None:
    return math.fsum(values) if values and all(value is not None for value in values) else None


def line_id(sid: int) -> str:
    return f"L{sid:04d}"


def build_state(segments: Sequence[dict[str, Any]]) -> str:
    return "\n".join(f"{line_id(int(seg['sid']))}| {str(seg.get('text', '')).strip()}" for seg in segments)


def build_questions(segments: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        f"s{seg['sid']}": {
            "type": "noul",
            "instructions": NOUL_INSTRUCTIONS.format(lid=line_id(int(seg["sid"]))),
        }
        for seg in segments
    }


def build_payload(segments, *, model, uid=None, guidance=GUIDANCE, caller_context=""):
    state = {"guidance": guidance, "transcript": build_state(segments)}
    if caller_context.strip():
        state["caller_context"] = caller_context
    if uid is not None:
        state["uid"] = uid
    return {"state": state, "model": model, "questions": build_questions(segments)}


def _is_truncated(body: dict[str, Any]) -> bool:
    if body.get("truncated") is True or body.get("is_truncated") is True:
        return True
    return body.get("finish_reason") in {"length", "max_tokens", "truncated"}


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _request_chunks(payload: dict[str, Any], *, max_questions: int | None,
                    max_request_bytes: int | None, max_choice_options: int | None):
    questions = payload.get("questions")
    if not isinstance(questions, dict):
        raise RequestLimitError("questions must be an object")
    if max_questions is not None and (not isinstance(max_questions, int) or isinstance(max_questions, bool) or max_questions < 1):
        raise ValueError("max_questions must be null or a positive integer")
    if max_request_bytes is not None and (not isinstance(max_request_bytes, int) or isinstance(max_request_bytes, bool) or max_request_bytes < 1):
        raise ValueError("max_request_bytes must be null or a positive integer")
    items = list(questions.items())
    chunks = []
    current = {}
    for key, question in items:
        criteria = question.get("criteria") if isinstance(question, dict) else None
        if criteria is not None and max_choice_options is not None and len(criteria) > max_choice_options:
            raise JevReviewValidationError("choice_option_limit", {"expected_count": max_choice_options, "actual_count": len(criteria)})
        candidate = dict(current)
        candidate[key] = question
        part = {**payload, "questions": candidate}
        if current and ((max_questions is not None and len(candidate) > max_questions)
                        or (max_request_bytes is not None and _serialized_size(part) > max_request_bytes)):
            chunks.append({**payload, "questions": current})
            current = {key: question}
            part = {**payload, "questions": current}
        else:
            current = candidate
        if max_request_bytes is not None and _serialized_size(part) > max_request_bytes:
            raise RequestLimitError("state and one question exceed max_request_bytes")
    if current:
        chunks.append({**payload, "questions": current})
    if not chunks:
        if max_request_bytes is not None and _serialized_size(payload) > max_request_bytes:
            raise RequestLimitError("request exceeds max_request_bytes")
        chunks.append(payload)
    return chunks


def _call_chunks(payload, fetcher, *, request_kind, window_label, max_questions, max_request_bytes,
                 max_choice_options, deadline_at=None, review=False):
    if fetcher is None:
        raise RuntimeError("a System One fetcher is required")
    chunks = _request_chunks(payload, max_questions=max_questions, max_request_bytes=max_request_bytes,
                             max_choice_options=max_choice_options)
    answers = {}
    input_usage = []
    output_usage = []
    costs = []
    for index, chunk in enumerate(chunks):
        kwargs = {"request_kind": request_kind}
        validator = (lambda body: _review_answers(body, chunk["questions"], max_choice_options)) if review else (
            lambda body: parse_response(body, set(chunk["questions"]))
        )
        kwargs["validate"] = validator
        if deadline_at is not None:
            kwargs["deadline_at"] = deadline_at
        if window_label is not None:
            kwargs["window_label"] = f"{window_label}:{index + 1}/{len(chunks)}" if len(chunks) > 1 else window_label
        body = fetcher(chunk, **kwargs)
        if _is_truncated(body):
            raise JevReviewValidationError("truncated_response")
        parsed = _review_answers(body, chunk["questions"], max_choice_options) if review else parse_response(body, set(chunk["questions"]))
        chunk_answers = parsed.get("answers", parsed.get("probabilities", {}))
        if answers.keys() & chunk_answers.keys():
            raise JevReviewValidationError("answers_keys")
        answers.update(chunk_answers)
        input_usage.append(parsed["input_tokens"])
        output_usage.append(parsed["output_tokens"])
        costs.append(parsed["cost"])
    return {
        "answers": answers,
        "input_tokens": _aggregate_usage(input_usage),
        "output_tokens": _aggregate_usage(output_usage),
        "cost": _aggregate_cost(costs),
    }


def parse_response(body, expected_keys=None):
    if not isinstance(body, dict):
        raise JevReviewValidationError("response_object")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise JevReviewValidationError("answers_object")
    expected_keys = set(answers) if expected_keys is None else expected_keys
    if set(answers) != expected_keys:
        raise JevReviewValidationError("answers_keys", {"expected_count": len(expected_keys), "actual_count": len(answers)})
    if _is_truncated(body):
        raise JevReviewValidationError("truncated_response")
    probabilities = {}
    for key, answer in answers.items():
        numeric = _finite_float(answer.get("noul")) if isinstance(answer, dict) else None
        if not isinstance(key, str) or not key.startswith("s") or numeric is None or not 0 <= numeric <= 1:
            raise JevReviewValidationError("evidence_probability")
        probabilities[key] = numeric
    usage = body.get("usage")
    if usage is None:
        usage = {}
    elif not isinstance(usage, dict):
        raise JevReviewValidationError("usage_object")
    return {"probabilities": probabilities, "input_tokens": _usage_tokens(usage, "input_tokens"),
            "output_tokens": _usage_tokens(usage, "output_tokens"), "cost": _usage_cost(usage)}


def jev_ask(segments, *, model, uid=None, enter=None, stay=None, guidance=GUIDANCE,
            caller_context="", fetcher: Callable[..., dict[str, Any]], request_kind="detection",
            window_label=None, max_questions=MAX_QUESTIONS_PER_REQUEST,
            max_request_bytes=MAX_REQUEST_BYTES, max_choice_options=TYPESAFE_MAX_CHOICE_OPTIONS,
            **_legacy):
    payload = build_payload(segments, model=model, uid=uid, guidance=guidance, caller_context=caller_context)
    parsed = _call_chunks(payload, fetcher, request_kind=request_kind, window_label=window_label,
                          max_questions=max_questions, max_request_bytes=max_request_bytes,
                          max_choice_options=max_choice_options,
                          deadline_at=_legacy.get("deadline_at"))
    probabilities = parsed["answers"]
    return {"model": model, "probabilities": probabilities,
            "usage": {"input_tokens": parsed["input_tokens"], "output_tokens": parsed["output_tokens"],
                      "cost": parsed["cost"]},
            "cache_hit": False,
            "spans": spans_from_probabilities(segments, probabilities, enter=enter, stay=stay)}


def _review_answers(body, questions, max_choice_options=TYPESAFE_MAX_CHOICE_OPTIONS):
    if not isinstance(body, dict):
        raise JevReviewValidationError("response_object")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise JevReviewValidationError("answers_object")
    if set(answers) != set(questions):
        raise JevReviewValidationError("answers_keys", {"expected_count": len(questions), "actual_count": len(answers)})
    parsed = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict):
            raise JevReviewValidationError("answer_object")
        question = questions[key]
        if question.get("type") == "noul":
            numeric = _finite_float(answer.get("noul"))
            if numeric is None or not 0 <= numeric <= 1:
                raise JevReviewValidationError("evidence_probability")
            parsed[key] = numeric
            continue
        criteria = question.get("criteria")
        if not isinstance(criteria, dict):
            raise JevReviewValidationError("choice_criteria_object")
        if not criteria:
            raise JevReviewValidationError("choice_criteria_empty")
        if max_choice_options is not None and len(criteria) > max_choice_options:
            raise JevReviewValidationError("choice_option_limit", {"expected_count": max_choice_options, "actual_count": len(criteria)})
        confidence = _finite_float(answer.get("confidence"))
        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        if not isinstance(choice, str) or not isinstance(probabilities, dict):
            raise JevReviewValidationError("choice_answer_shape")
        if confidence is None or not 0 <= confidence <= 1:
            raise JevReviewValidationError("choice_confidence")
        normalized = {}
        for option, value in probabilities.items():
            number = _finite_float(value)
            if not isinstance(option, str) or number is None or not 0 <= number <= 1:
                raise JevReviewValidationError("choice_probability")
            normalized[option] = number
        if set(normalized) != set(criteria) or choice not in normalized:
            raise JevReviewValidationError("choice_probability_keys", {"expected_count": len(criteria), "actual_count": len(normalized)})
        total = math.fsum(normalized.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=CHOICE_PROBABILITY_SUM_TOLERANCE + CHOICE_PROBABILITY_SUM_EPSILON):
            raise JevReviewValidationError("choice_probability_sum", {"expected_total": 1.0, "actual_total": total, "tolerance": CHOICE_PROBABILITY_SUM_TOLERANCE})
        parsed[key] = {"choice": choice, "confidence": confidence, "probabilities": normalized}
    usage = body.get("usage")
    if usage is None:
        usage = {}
    elif not isinstance(usage, dict):
        raise JevReviewValidationError("usage_object")
    return {"answers": parsed, "input_tokens": _usage_tokens(usage, "input_tokens"),
            "output_tokens": _usage_tokens(usage, "output_tokens"), "cost": _usage_cost(usage)}


def jev_review_questions(*, state, questions, model, uid=None, fetcher,
                         request_kind="review", window_label=None,
                         max_questions=MAX_QUESTIONS_PER_REQUEST,
                         max_request_bytes=MAX_REQUEST_BYTES,
                         max_choice_options=TYPESAFE_MAX_CHOICE_OPTIONS, **_legacy):
    full_state = dict(state)
    if uid is not None:
        full_state["uid"] = uid
    payload = {"state": full_state, "model": model, "questions": questions}
    parsed = _call_chunks(payload, fetcher, request_kind=request_kind, window_label=window_label,
                          max_questions=max_questions, max_request_bytes=max_request_bytes,
                          max_choice_options=max_choice_options,
                          deadline_at=_legacy.get("deadline_at"), review=True)
    return {"answers": parsed["answers"], "cache_hit": False, "usage": {"input_tokens": parsed["input_tokens"],
            "output_tokens": parsed["output_tokens"], "cost": parsed["cost"]}}


def _focus_ids(focus):
    ordered = sorted(focus, key=lambda item: int(item["sid"]))
    lo, hi = int(ordered[0]["sid"]), int(ordered[-1]["sid"])
    return line_id(lo) if lo == hi else f"{line_id(lo)}-{line_id(hi)}"


def build_category_payload(focus, context, categories, *, model, uid=None, guidance=CATEGORY_GUIDANCE):
    merged = {int(item["sid"]): item for item in [*context, *focus]}
    ordered = [merged[key] for key in sorted(merged)]
    ids = _focus_ids(focus)
    state = {"guidance": guidance, "transcript": build_state(ordered), "focus": ids}
    if uid is not None:
        state["uid"] = uid
    criteria = {category: CATEGORY_DESCRIPTIONS.get(category, category) for category in categories}
    return {"state": state, "model": model, "questions": {CATEGORY_CHOICE_KEY: {
        "type": "choice", "instructions": CATEGORY_CHOICE_INSTRUCTIONS.format(ids=ids), "criteria": criteria}}}


def jev_category(focus, context, categories, *, model, uid=None, guidance=CATEGORY_GUIDANCE,
                 fetcher, window_label=None, max_questions=MAX_QUESTIONS_PER_REQUEST,
                 max_request_bytes=MAX_REQUEST_BYTES,
                 max_choice_options=TYPESAFE_MAX_CHOICE_OPTIONS, **_legacy):
    payload = build_category_payload(focus, context, categories, model=model, uid=uid, guidance=guidance)
    result = jev_review_questions(state=payload["state"], questions=payload["questions"], model=model,
                                 fetcher=fetcher, request_kind="category", window_label=window_label,
                                 max_questions=max_questions, max_request_bytes=max_request_bytes,
                                 max_choice_options=max_choice_options,
                                 **_legacy)
    answer = result["answers"][CATEGORY_CHOICE_KEY]
    return {"category": answer["choice"], "confidence": answer["confidence"],
            "cache_hit": False,
            "probabilities": answer["probabilities"], "usage": result["usage"]}

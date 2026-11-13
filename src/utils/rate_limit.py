"""Rate-limit helpers shared by the LLM client and its callers."""
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def _clamp_seconds(value: float, max_seconds: float) -> float:
    """Clamp a seconds-to-wait value to [0, max_seconds]."""
    return min(max(value, 0.0), max_seconds)


def parse_retry_after(value: str | None, *, max_seconds: float = 300.0) -> float | None:
    """Parse an HTTP `Retry-After` header (delta-seconds or RFC 7231 date) into seconds-to-wait.

    None when missing or unparseable, so callers use their normal backoff. Clamped to
    ``[0, max_seconds]`` so a pathological hint cannot stall the caller.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        seconds = (target - datetime.now(timezone.utc)).total_seconds()

    return _clamp_seconds(seconds, max_seconds)


# Groq's tokens-per-minute 429 body has a message like "Limit 6000, Used 0, Requested 7500".
# Parsed defensively: any failure returns None (transient retry path).
_GROQ_LIMIT_RE = re.compile(r"limit\s+(\d[\d,]*)", re.IGNORECASE)
_GROQ_USED_RE = re.compile(r"used\s+(\d[\d,]*)", re.IGNORECASE)
_GROQ_REQUESTED_RE = re.compile(r"requested\s+~?(\d[\d,]*)", re.IGNORECASE)


def parse_groq_rate_limit_body(body: Any) -> dict | None:
    """Extract integer ``limit``, ``used``, ``requested`` from a Groq-style 429 body.

    Takes parsed JSON, a string or any object with a string repr. None unless all three parse
    and the message describes a tokens-per-minute exceedance (the transient retry path stays in charge).
    """
    if body is None:
        return None

    payload = None
    if isinstance(body, dict):
        payload = body
    elif isinstance(body, str):
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None

    message = ""
    err_type = ""
    err_code = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
            err_type = str(err.get("type") or "")
            err_code = str(err.get("code") or "")

    if not message:
        message = str(body)

    is_token_limit = (
        err_type.lower() == "tokens"
        or err_code.lower() == "rate_limit_exceeded"
        or "tokens per minute" in message.lower()
        or "tpm" in message.lower()
    )
    if not is_token_limit:
        return None

    def _to_int(m):
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", ""))
        except (TypeError, ValueError):
            return None

    limit = _to_int(_GROQ_LIMIT_RE.search(message))
    used = _to_int(_GROQ_USED_RE.search(message))
    requested = _to_int(_GROQ_REQUESTED_RE.search(message))

    if limit is None or requested is None:
        return None

    return {"limit": limit, "used": used, "requested": requested}


# Google/Gemini 429 bodies (also via OpenRouter) carry the wait in the body, not Retry-After:
# a RetryInfo detail (retryDelay: "4s") and/or "Please retry in 4.2s". Float patterns are
# anchored to a real number so "1.2.3" or a lone "." cannot reach float().
_GOOGLE_RETRY_IN_RE = re.compile(r"retry in\s+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*s$", re.IGNORECASE)


def _reset_seconds_from_error(err: dict) -> float | None:
    """Unclamped seconds-to-wait from an error dict's reset fields, or None.

    Tries ``seconds_until_reset``, ``resets_at`` (epoch), then ``resets_at_iso``, skipping unparseable ones.
    """
    seconds = err.get("seconds_until_reset")
    if seconds is not None:
        try:
            return float(seconds)
        except (TypeError, ValueError):
            pass

    resets_at = err.get("resets_at")
    if resets_at is not None:
        try:
            return float(resets_at) - datetime.now(timezone.utc).timestamp()
        except (TypeError, ValueError):
            pass

    resets_at_iso = err.get("resets_at_iso")
    if resets_at_iso is not None:
        try:
            target = datetime.fromisoformat(str(resets_at_iso))
        except (TypeError, ValueError):
            target = None
        if target is not None:
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return (target - datetime.now(timezone.utc)).total_seconds()

    return None


def _coerce_error_dict(body: Any) -> dict | None:
    """Normalize a body (dict, list wrapping one, or JSON string) to its inner ``error`` dict, else the payload.

    When proxied through OpenRouter, the upstream error (own ``details``/``status``) is a JSON
    string under ``error.metadata.raw``; descend into it so callers see the real fields. Skipped
    when the outer error has its own parseable reset field (an empty, non-numeric or null one
    does not count). Any parse failure returns None.
    """
    payload = body
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    err = err if isinstance(err, dict) else payload
    has_own_reset = _reset_seconds_from_error(err) is not None
    if "details" not in err and "status" not in err and not has_own_reset:
        meta = err.get("metadata")
        raw = meta.get("raw") if isinstance(meta, dict) else None
        if isinstance(raw, str):
            try:
                inner = json.loads(raw)
            except (ValueError, TypeError):
                inner = None
            if isinstance(inner, dict):
                inner_err = inner.get("error")
                return inner_err if isinstance(inner_err, dict) else inner
    return err


def _coerce_google_error(body: Any) -> dict | None:
    """Normalize a Google 429 body to its inner ``error`` dict; plain non-JSON strings give None
    (callers regex ``str(body)``)."""
    return _coerce_error_dict(body)


def parse_google_retry_delay(body: Any, *, max_seconds: float = 300.0) -> float | None:
    """Seconds-to-wait from a Google 429 body, or None.

    Prefers the RetryInfo detail (``retryDelay: "4s"``), then a "Please retry in <n>s" message hint,
    then the raw repr. Clamped to ``[0, max_seconds]``.
    """
    err = _coerce_google_error(body)
    seconds = None
    if isinstance(err, dict):
        for detail in err.get("details") or []:
            if isinstance(detail, dict) and "RetryInfo" in str(detail.get("@type", "")):
                m = _DURATION_RE.match(str(detail.get("retryDelay", "")).strip())
                if m:
                    seconds = float(m.group(1))
                    break
        if seconds is None:
            m = _GOOGLE_RETRY_IN_RE.search(str(err.get("message") or ""))
            if m:
                seconds = float(m.group(1))
    if seconds is None:
        m = _GOOGLE_RETRY_IN_RE.search(str(body))
        if m:
            seconds = float(m.group(1))
    if seconds is None:
        return None
    return _clamp_seconds(seconds, max_seconds)


def parse_upstream_reset(body: Any, *, max_seconds: float = 300.0) -> float | None:
    """Seconds until a provider's stated reset time, or None.

    Reads ``seconds_until_reset``, ``resets_at`` (epoch) then ``resets_at_iso`` (ISO 8601 with
    offset), skipping missing or unparseable fields. Clamped to ``[0, max_seconds]``.
    """
    err = _coerce_error_dict(body)
    if not isinstance(err, dict):
        return None
    seconds = _reset_seconds_from_error(err)
    if seconds is None:
        return None
    return _clamp_seconds(seconds, max_seconds)


def parse_google_daily_quota(body: Any) -> dict | None:
    """Detect a Google free-tier daily quota exhaustion (cannot recover today).

    Returns ``{limit, model, quota_id}`` for a RESOURCE_EXHAUSTED 429 with a per-day quota
    (``quotaId`` contains "PerDay", or "per day" in the message); other 429s return None.
    """
    err = _coerce_google_error(body)
    if not isinstance(err, dict):
        return None
    status = str(err.get("status") or "")
    is_resource_exhausted = (
        "RESOURCE_EXHAUSTED" in status or "RESOURCE_EXHAUSTED" in str(body)
    )
    if not is_resource_exhausted:
        return None
    message = str(err.get("message") or "")

    quota_id = None
    limit = None
    model = None
    for detail in err.get("details") or []:
        if not isinstance(detail, dict) or "QuotaFailure" not in str(detail.get("@type", "")):
            continue
        for v in detail.get("violations") or []:
            if "perday" in str(v.get("quotaId") or "").lower():
                quota_id = str(v.get("quotaId"))
                try:
                    limit = int(v["quotaValue"]) if v.get("quotaValue") is not None else None
                except (TypeError, ValueError):
                    limit = None
                dims = v.get("quotaDimensions")
                model = dims.get("model") if isinstance(dims, dict) else None
                break
        if quota_id:
            break

    # A PerDay quotaId is authoritative. Use the message only if it names a daily limit and
    # not a per-minute one: misreading a recoverable throttle as daily would fail the episode for good.
    if quota_id is None:
        msg = message.lower()
        mentions_daily = "per day" in msg or "perday" in msg or "per-day" in msg
        mentions_minute = "per minute" in msg or "perminute" in msg or "per-minute" in msg
        if not (mentions_daily and not mentions_minute):
            return None
    if limit is None:
        m = re.search(r"limit:\s*(\d+)", message)
        if m:
            limit = int(m.group(1))
    return {"limit": limit, "model": model, "quota_id": quota_id or "per-day"}

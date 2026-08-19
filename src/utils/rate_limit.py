"""Rate-limit helpers shared by the LLM client and its callers."""
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def parse_retry_after(value: str | None, *, max_seconds: float = 300.0) -> float | None:
    """Parse an HTTP `Retry-After` header into seconds-to-wait.

    Accepts either a delta-seconds string (e.g. ``"7"``) or an RFC 7231
    HTTP-date. Returns ``None`` when the value is missing or unparseable so
    callers can fall back to their normal backoff curve.

    The result is clamped to ``[0, max_seconds]`` to bound pathological server
    hints (e.g. a one-hour Retry-After) without making the caller wait forever.
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

    if seconds < 0:
        seconds = 0.0
    if seconds > max_seconds:
        seconds = max_seconds
    return float(seconds)


# Groq's 429 body for a tokens-per-minute exceedance includes a message like:
#   Limit 6000, Used 0, Requested 7500
# along with a structured type/code pair. We parse defensively: any failure
# returns None so the caller falls back to the transient retry path.
_GROQ_LIMIT_RE = re.compile(r"limit\s+(\d[\d,]*)", re.IGNORECASE)
_GROQ_USED_RE = re.compile(r"used\s+(\d[\d,]*)", re.IGNORECASE)
_GROQ_REQUESTED_RE = re.compile(r"requested\s+~?(\d[\d,]*)", re.IGNORECASE)


def parse_groq_rate_limit_body(body: Any) -> dict | None:
    """Extract limit/used/requested from a Groq-style 429 body.

    Accepts a dict (already-parsed JSON), a string (raw JSON or plain text),
    or any object with a string repr. Returns a dict with integer fields
    ``limit``, ``used``, ``requested`` when all three numbers can be parsed
    and the message describes a tokens-per-minute exceedance, otherwise None.

    Defensive by design: any unparseable input returns None so the existing
    transient retry path remains in charge.
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


# Google/Gemini RPC 429 bodies (also seen via OpenRouter's OpenAI-compatible path)
# put the recommended wait in the body, not the Retry-After header: a RetryInfo
# detail (retryDelay: "4s") and/or a "Please retry in 4.2s" hint in the message.
# Float patterns are anchored to a real number (\d+ optional .\d+) so a malformed
# token like "1.2.3" or a lone "." cannot reach float() and raise ValueError.
_GOOGLE_RETRY_IN_RE = re.compile(r"retry in\s+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*s$", re.IGNORECASE)


def _coerce_google_error(body: Any) -> dict | None:
    """Normalize a Google 429 body to its inner ``error`` dict (or the payload).

    Accepts a dict, a list wrapping it, or a JSON string. Plain non-JSON strings
    return None so callers can fall back to a regex over ``str(body)``. When the
    error is proxied through OpenRouter, the upstream Google error (with its
    ``details``/``status``) is a JSON string under ``error.metadata.raw``; descend
    into it so the daily-quota / retry-delay parsers see the real fields.
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
    if "details" not in err and "status" not in err:
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


def parse_google_retry_delay(body: Any, *, max_seconds: float = 300.0) -> float | None:
    """Seconds-to-wait from a Google 429 body, or None.

    Prefers the structured RetryInfo detail (``retryDelay: "4s"``); falls back to a
    "Please retry in <n>s" hint in the message, then to the raw repr. Clamped to
    ``[0, max_seconds]``. Returns None when no delay is present.
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
    return min(max(seconds, 0.0), max_seconds)


def parse_google_daily_quota(body: Any) -> dict | None:
    """Detect a Google free-tier DAILY quota exhaustion (cannot recover today).

    Returns ``{limit, model, quota_id}`` when the 429 is RESOURCE_EXHAUSTED with a
    per-day quota (a QuotaFailure violation whose ``quotaId`` contains "PerDay", or
    "per day" in the message). Per-minute limits and other 429s return None so they
    stay on the normal retry path.
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

    # Authoritative signal is a PerDay quotaId violation. Fall back to the message
    # only when it names a daily limit and does NOT also name a per-minute one --
    # Google free-tier messages sometimes enumerate both, and misreading a
    # recoverable per-minute throttle as daily would fail the episode for good.
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

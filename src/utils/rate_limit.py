"""Rate-limit helpers shared by the LLM client and its callers."""
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional


def parse_retry_after(value: Optional[str], *, max_seconds: float = 300.0) -> Optional[float]:
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


def parse_groq_rate_limit_body(body: Any) -> Optional[dict]:
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

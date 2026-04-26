"""Rate-limit helpers shared by the LLM client and its callers."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional


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

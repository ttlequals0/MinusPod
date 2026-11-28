"""Adaptive per-feed refresh interval: a pure function plus the hint parsers it consumes."""
import random
import re
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

CADENCE_DIVISOR = 12
MIN_CADENCE_EPISODES = 3
MAX_CADENCE_EPISODES = 10
DUE_WINDOW_FRACTION = 0.25
FAILURE_BACKOFF_MAX_SECONDS = 6 * 3600
FAILURE_JITTER_FRACTION = 0.10
RETRY_AFTER_MAX_SECONDS = 24 * 3600
MAX_FAILURE_EXPONENT = 16

REASON_FIXED = 'fixed'
REASON_FLOOR = 'floor'
REASON_CADENCE = 'cadence'
REASON_DUE = 'due'
REASON_HINT = 'hint'
REASON_MAX = 'max'
REASON_DORMANT = 'dormant'
REASON_BACKOFF = 'backoff'
REASON_RETRY_AFTER = 'retry_after'

_PERIOD_SECONDS = {
    'HOURLY': 3600, 'DAILY': 86400, 'WEEKLY': 7 * 86400,
    'MONTHLY': 30 * 86400, 'YEARLY': 365 * 86400,
}
_SY_PERIODS = {k.lower(): v for k, v in _PERIOD_SECONDS.items()}


@dataclass(frozen=True)
class RefreshDecision:
    interval_seconds: int
    reason: str


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def observed_cadence_seconds(episode_dates, now) -> float | None:
    """Median gap between the newest valid publish dates, or None when it cannot be trusted.

    Future dates are ignored: a scheduled-ahead episode says nothing about how often the show publishes."""
    dates = sorted({_aware(d) for d in episode_dates if d is not None and _aware(d) <= now},
                   reverse=True)[:MAX_CADENCE_EPISODES]
    if len(dates) < MIN_CADENCE_EPISODES:
        return None
    gaps = [(a - b).total_seconds() for a, b in zip(dates, dates[1:], strict=False)]
    median = statistics.median(gaps)
    return median if median > 0 else None


def parse_retry_after(value, now) -> int | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date), capped at 24 hours; None if unusable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    seconds = None
    if re.fullmatch(r'\d{1,12}', text):
        seconds = int(text)
    else:
        try:
            when = _aware(parsedate_to_datetime(text))
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when is None:
            return None
        seconds = int((when - now).total_seconds())
    if seconds <= 0:
        return None
    return min(seconds, RETRY_AFTER_MAX_SECONDS)


def parse_cache_control_max_age(value) -> int | None:
    """max-age seconds from a Cache-Control header; None when absent, zero, or malformed."""
    if not value:
        return None
    match = re.search(r'(?:^|[,\s])max-age\s*=\s*"?(\d{1,10})"?', str(value), re.IGNORECASE)
    if not match:
        return None
    seconds = int(match.group(1))
    return seconds or None


def parse_rrule_period_seconds(rrule) -> int | None:
    """Publish period implied by an iCalendar RRULE (FREQ, INTERVAL, BYDAY on weekly); None if malformed."""
    if not rrule or not isinstance(rrule, str):
        return None
    parts = {}
    for chunk in rrule.strip().split(';'):
        key, sep, val = chunk.partition('=')
        if not sep:
            return None
        parts[key.strip().upper()] = val.strip().upper()
    base = _PERIOD_SECONDS.get(parts.get('FREQ', ''))
    if base is None:
        return None
    try:
        interval = int(parts.get('INTERVAL', '1'))
    except ValueError:
        return None
    if interval < 1 or interval > 1000:
        return None
    period = base * interval
    byday = [d for d in parts.get('BYDAY', '').split(',') if d]
    if parts['FREQ'] == 'WEEKLY' and byday:
        period = period / min(len(byday), 7)
    return int(period)


def parse_sy_period_seconds(update_period, update_frequency) -> int | None:
    """Publish period from sy:updatePeriod and sy:updateFrequency (times per period); None if malformed."""
    base = _SY_PERIODS.get((update_period or '').strip().lower())
    if base is None:
        return None
    try:
        times = int((update_frequency or '1').strip())
    except (TypeError, ValueError):
        return None
    if times < 1 or times > 100000:
        return None
    return int(base / times)


def _failure_backoff(floor, failures, rng):
    cap = max(FAILURE_BACKOFF_MAX_SECONDS, floor)
    base = min(floor * 2 ** min(max(failures - 1, 0), MAX_FAILURE_EXPONENT), cap)
    return base * (1 + rng.uniform(0, FAILURE_JITTER_FRACTION))


def compute_refresh_interval(*, now, floor_seconds, max_seconds, episode_dates=(),
                             update_period_seconds=None, feed_complete=False,
                             http_max_age_seconds=None, failures=0,
                             retry_after_until=None, adaptive=True, dormant=False,
                             rng=None) -> RefreshDecision:
    """How long to wait before the next fetch of one feed, and why.

    Healthy feeds take the shortest interval any signal justifies, clamped to [floor, max]. Failures
    back off from the floor and Retry-After lengthens whatever was chosen, in every mode."""
    now = _aware(now)
    floor = max(int(floor_seconds), 1)
    ceiling = max(int(max_seconds), floor)
    rng = rng or random

    if failures > 0:
        decision = RefreshDecision(int(_failure_backoff(floor, failures, rng)), REASON_BACKOFF)
    elif not adaptive:
        decision = RefreshDecision(floor, REASON_FIXED)
    elif feed_complete:
        decision = RefreshDecision(ceiling, REASON_HINT)
    elif dormant:
        decision = RefreshDecision(ceiling, REASON_DORMANT)
    else:
        decision = _healthy_interval(now, floor, ceiling, episode_dates, update_period_seconds,
                                     http_max_age_seconds)

    if retry_after_until is not None:
        remaining = min((_aware(retry_after_until) - now).total_seconds(), RETRY_AFTER_MAX_SECONDS)
        if remaining > decision.interval_seconds:
            decision = RefreshDecision(int(remaining), REASON_RETRY_AFTER)
    return decision


def _healthy_interval(now, floor, ceiling, episode_dates, update_period_seconds, http_max_age_seconds):
    candidates = []
    cadence = observed_cadence_seconds(episode_dates, now)
    if cadence:
        candidates.append((cadence / CADENCE_DIVISOR, REASON_CADENCE))
    if update_period_seconds and update_period_seconds > 0:
        candidates.append((update_period_seconds / CADENCE_DIVISOR, REASON_HINT))
    interval, reason = min(candidates) if candidates else (floor, REASON_FLOOR)

    if cadence and _episode_due(now, episode_dates, cadence):
        return RefreshDecision(floor, REASON_DUE)

    if http_max_age_seconds and http_max_age_seconds > interval:
        interval, reason = http_max_age_seconds, REASON_HINT
    if interval >= ceiling:
        return RefreshDecision(ceiling, REASON_MAX)
    if interval <= floor:
        return RefreshDecision(floor, REASON_FLOOR)
    return RefreshDecision(int(interval), reason)


def _episode_due(now, episode_dates, cadence) -> bool:
    published = [_aware(d) for d in episode_dates if d is not None and _aware(d) <= now]
    if not published:
        return False
    elapsed = (now - max(published)).total_seconds()
    return abs(elapsed - cadence) <= DUE_WINDOW_FRACTION * cadence


def next_refresh_at(now, decision) -> datetime:
    return now + timedelta(seconds=decision.interval_seconds)


# Response facts from the most recent fetch on this thread, for the scheduler to read
# without widening fetch_feed_conditional's return value.
_fetch_meta = threading.local()


def reset_fetch_meta():
    _fetch_meta.value = {}


def note_fetch_meta(**facts):
    if not hasattr(_fetch_meta, 'value'):
        _fetch_meta.value = {}
    _fetch_meta.value.update(facts)


def get_fetch_meta() -> dict:
    return dict(getattr(_fetch_meta, 'value', {}))

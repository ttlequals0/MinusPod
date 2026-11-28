"""Adaptive per-feed refresh interval: a pure function plus the hint parsers it consumes."""
import logging
import math
import random
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from utils.rate_limit import parse_retry_after

CADENCE_DIVISOR = 12
MIN_CADENCE_EPISODES = 3
MAX_CADENCE_EPISODES = 10
DUE_WINDOW_FRACTION = 0.25
FAILURE_BACKOFF_MAX_SECONDS = 6 * 3600
FAILURE_JITTER_FRACTION = 0.10
RETRY_AFTER_MAX_SECONDS = 24 * 3600
MAX_FAILURE_EXPONENT = 16

_REASON_FIXED = 'fixed'
_REASON_FLOOR = 'floor'
_REASON_CADENCE = 'cadence'
_REASON_DUE = 'due'
_REASON_HINT = 'hint'
_REASON_MAX = 'max'
_REASON_DORMANT = 'dormant'
REASON_BACKOFF = 'backoff'
REASON_RETRY_AFTER = 'retry_after'

_PERIOD_SECONDS = {
    'HOURLY': 3600, 'DAILY': 86400, 'WEEKLY': 7 * 86400,
    'MONTHLY': 30 * 86400, 'YEARLY': 365 * 86400,
}
_SY_PERIODS = {k.lower(): v for k, v in _PERIOD_SECONDS.items()}

_ALWAYS_LOGGED_REASONS = frozenset({REASON_BACKOFF, REASON_RETRY_AFTER})


def reason_log_level(old, new) -> int | None:
    """Log level for a refresh-reason change, None when nothing changed and nothing is worth a line."""
    if new in _ALWAYS_LOGGED_REASONS:
        return logging.INFO
    if new == old:
        return None
    if old is None:
        return logging.DEBUG
    # due <-> normal flaps around every expected episode; dormant, backoff and retry_after stay INFO
    if _REASON_DUE in (old, new) and not {old, new} & (_ALWAYS_LOGGED_REASONS | {_REASON_DORMANT}):
        return logging.DEBUG
    return logging.INFO


@dataclass(frozen=True)
class RefreshDecision:
    interval_seconds: int
    reason: str


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


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


def retry_after_seconds(value) -> int | None:
    """Whole seconds from a Retry-After header, capped at 24 hours; None when absent, past or unusable."""
    seconds = parse_retry_after(value, max_seconds=RETRY_AFTER_MAX_SECONDS)
    return int(seconds) or None if seconds and seconds > 0 else None


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
    """Wait before the next fetch of one feed, and why: shortest healthy interval clamped to
    [floor, max]; failures back off from the floor; Retry-After lengthens either."""
    now = _aware(now)
    floor = max(int(floor_seconds), 1)
    ceiling = max(int(max_seconds), floor)
    rng = rng or random

    if failures > 0:
        decision = RefreshDecision(int(_failure_backoff(floor, failures, rng)), REASON_BACKOFF)
    elif not adaptive:
        decision = RefreshDecision(floor, _REASON_FIXED)
    elif feed_complete:
        decision = RefreshDecision(ceiling, _REASON_HINT)
    elif dormant:
        decision = RefreshDecision(ceiling, _REASON_DORMANT)
    else:
        decision = _healthy_interval(now, floor, ceiling, episode_dates, update_period_seconds,
                                     http_max_age_seconds)

    if retry_after_until is not None:
        remaining = min((_aware(retry_after_until) - now).total_seconds(), RETRY_AFTER_MAX_SECONDS)
        if remaining > decision.interval_seconds:
            decision = RefreshDecision(math.ceil(remaining), REASON_RETRY_AFTER)
    return decision


def _healthy_interval(now, floor, ceiling, episode_dates, update_period_seconds, http_max_age_seconds):
    candidates = []
    cadence = observed_cadence_seconds(episode_dates, now)
    if cadence:
        candidates.append((cadence / CADENCE_DIVISOR, _REASON_CADENCE))
    if update_period_seconds and update_period_seconds > 0:
        candidates.append((update_period_seconds / CADENCE_DIVISOR, _REASON_HINT))
    interval, reason = min(candidates) if candidates else (floor, _REASON_FLOOR)

    if cadence and _episode_due(now, episode_dates, cadence):
        return RefreshDecision(floor, _REASON_DUE)

    if http_max_age_seconds and http_max_age_seconds > interval:
        interval, reason = http_max_age_seconds, _REASON_HINT
    if interval >= ceiling:
        return RefreshDecision(ceiling, _REASON_MAX)
    if interval <= floor:
        return RefreshDecision(floor, _REASON_FLOOR)
    return RefreshDecision(int(interval), reason)


def _episode_due(now, episode_dates, cadence) -> bool:
    published = [_aware(d) for d in episode_dates if d is not None and _aware(d) <= now]
    if not published:
        return False
    elapsed = (now - max(published)).total_seconds()
    return abs(elapsed - cadence) <= DUE_WINDOW_FRACTION * cadence


def next_refresh_at(now, decision) -> datetime:
    return now + timedelta(seconds=decision.interval_seconds)


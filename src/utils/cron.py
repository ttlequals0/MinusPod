"""Minimal 5-field cron evaluator for MinusPod's community-sync schedule.

Supports the common cron syntax users will write — numbers, '*', ranges,
lists, and step values — for the five standard fields:

  minute (0-59)  hour (0-23)  day-of-month (1-31)  month (1-12)  dow (0-6 sun=0)

Designed to answer two questions:

  * `is_valid_expression(expr)` -> bool — for input validation in settings.
  * `is_due(expr, last_run, now)` -> bool — has a fire time elapsed since
    `last_run`? Used by the background sync job that ticks every 15 min.

Day-of-month and day-of-week follow vixie-cron's OR semantics: if both
are restricted, a fire happens when EITHER matches; if only one is
restricted, only that one is consulted. Names for months and days are
intentionally NOT supported to keep parsing simple.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Set, Tuple

_FIELD_RANGES = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (Sunday = 0)
)


def _parse_field(spec: str, lo: int, hi: int) -> Set[int]:
    spec = spec.strip()
    if not spec:
        raise ValueError('empty cron field')
    out: Set[int] = set()
    for part in spec.split(','):
        step = 1
        if '/' in part:
            base, step_str = part.split('/', 1)
            step = int(step_str)
            if step < 1:
                raise ValueError(f'step must be >= 1, got {step_str}')
        else:
            base = part
        if base == '*' or base == '':
            start, end = lo, hi
        elif '-' in base:
            a, b = base.split('-', 1)
            start = int(a)
            end = int(b)
            if start < lo or end > hi or start > end:
                raise ValueError(f'out-of-range range: {base}')
        else:
            v = int(base)
            if v < lo or v > hi:
                raise ValueError(f'out-of-range value: {v}')
            start = end = v
        for n in range(start, end + 1, step):
            out.add(n)
    if not out:
        raise ValueError(f'no values for field "{spec}"')
    return out


def parse_expression(expr: str) -> Tuple[Set[int], Set[int], Set[int], Set[int], Set[int]]:
    """Parse a 5-field cron expression into per-field sets. Raises ValueError."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f'expected 5 fields, got {len(parts)}')
    sets = tuple(
        _parse_field(parts[i], *_FIELD_RANGES[i]) for i in range(5)
    )
    return sets  # type: ignore[return-value]


def is_valid_expression(expr: str) -> bool:
    """True if `expr` is a parseable 5-field cron expression."""
    try:
        parse_expression(expr)
        return True
    except (ValueError, TypeError):
        return False


def _matches(dt: datetime, sets: Tuple[Set[int], Set[int], Set[int], Set[int], Set[int]]) -> bool:
    minute_s, hour_s, dom_s, month_s, dow_s = sets
    if dt.minute not in minute_s:
        return False
    if dt.hour not in hour_s:
        return False
    if dt.month not in month_s:
        return False
    dow = dt.weekday()  # 0=Mon..6=Sun
    cron_dow = (dow + 1) % 7  # cron: 0=Sun..6=Sat
    # Vixie semantics: if both DOM and DOW are constrained (not full sets),
    # match on EITHER. If only one is constrained, use only that one.
    dom_full = dom_s == set(range(1, 32))
    dow_full = dow_s == set(range(0, 7))
    if dom_full and dow_full:
        return True
    if dom_full:
        return cron_dow in dow_s
    if dow_full:
        return dt.day in dom_s
    return dt.day in dom_s or cron_dow in dow_s


def next_fire(expr: str, after: datetime, *, max_iters: int = 60 * 24 * 366) -> datetime:
    """Return the first datetime > `after` that matches `expr`.

    Resolution is minute. `after` may be naive or tz-aware; the return value
    matches whichever was passed in. Raises ValueError if no fire within
    `max_iters` minutes.
    """
    sets = parse_expression(expr)
    # Drop to minute resolution and add one minute so `after` itself isn't a hit.
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(max_iters):
        if _matches(candidate, sets):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f'No match within {max_iters} minutes for expression: {expr}')


def is_due(expr: str, last_run: datetime, now: datetime) -> bool:
    """True if the next scheduled fire after `last_run` has elapsed by `now`."""
    try:
        nxt = next_fire(expr, last_run)
    except ValueError:
        return False
    return nxt <= now

"""Weekly processing window (#992): hold the queue outside operator-set hours."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import PROCESSING_SCHEDULE_DAY_KEYS, coerce_bool_setting
from database import Database
from database.settings import registry_current_value
from utils.time import ISO_FORMAT, utc_now

logger = logging.getLogger('podcast.refresh')

DAY_KEYS = PROCESSING_SCHEDULE_DAY_KEYS
_KEYS = {
    'enabled': 'processing_schedule_enabled',
    'start': 'processing_schedule_start',
    'end': 'processing_schedule_end',
    'days': 'processing_schedule_days',
}
# Window start dates from yesterday (an overnight window may still be open) to this many days ahead.
_HORIZON_DAYS = 8


def _ordered_days(csv: str) -> list[str]:
    """Day keys in a stored comma string, in week order."""
    days = set(csv.split(','))
    return [d for d in DAY_KEYS if d in days]


def get_processing_schedule(db) -> dict:
    """Stored schedule with defaults for unset or invalid values, in the instance time zone."""
    return {
        'enabled': coerce_bool_setting(db.get_setting(_KEYS['enabled'])),
        'start': registry_current_value(db, _KEYS['start']),
        'end': registry_current_value(db, _KEYS['end']),
        'days': _ordered_days(registry_current_value(db, _KEYS['days'])),
        'timezone': registry_current_value(db, 'notification_timezone'),
    }


def save_processing_schedule(db, *, enabled=None, start=None, end=None, days=None) -> None:
    """Store the given fields; callers validate them first."""
    values = {
        'enabled': None if enabled is None else ('true' if enabled else 'false'),
        'start': start,
        'end': end,
        'days': None if days is None else ','.join(d for d in DAY_KEYS if d in days),
    }
    with db.settings_transaction():
        for field, value in values.items():
            if value is not None:
                db.set_setting(_KEYS[field], value, is_default=False)


def _active_schedule(db) -> dict | None:
    """Validated schedule with its zone when enabled, else None."""
    if not coerce_bool_setting(db.get_setting(_KEYS['enabled'])):
        return None
    tz = registry_current_value(db, 'notification_timezone')
    # The validator accepts UTC without tzdata, so only other names need ZoneInfo.
    zone = timezone.utc if tz == 'UTC' else ZoneInfo(tz)
    return {'enabled': True, 'start': registry_current_value(db, _KEYS['start']),
            'end': registry_current_value(db, _KEYS['end']),
            'days': _ordered_days(registry_current_value(db, _KEYS['days'])),
            'timezone': tz, 'zone': zone}


def _windows(schedule: dict, today: date) -> list[tuple[datetime, datetime]]:
    """UTC (start, end) windows for each selected start date, built from local wall-clock times."""
    zone = schedule['zone']
    start_t = time.fromisoformat(schedule['start'])
    end_t = time.fromisoformat(schedule['end'])
    spills = end_t <= start_t
    windows = []
    for offset in range(-1, _HORIZON_DAYS + 1):
        day = today + timedelta(days=offset)
        if DAY_KEYS[day.weekday()] not in schedule['days']:
            continue
        end_day = day + timedelta(days=1) if spills else day
        windows.append((
            datetime.combine(day, start_t, tzinfo=zone).astimezone(timezone.utc),
            datetime.combine(end_day, end_t, tzinfo=zone).astimezone(timezone.utc),
        ))
    return windows


def _merge(windows: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def schedule_state(db, now: datetime | None = None) -> dict:
    """Whether new runs may start now, and when that next changes (ISO 8601 UTC, None if never)."""
    schedule = _active_schedule(db)
    if schedule is None:
        return {'enabled': False, 'open': True, 'nextChangeAt': None}
    now = (now or utc_now()).astimezone(timezone.utc)
    merged = _merge(_windows(schedule, now.astimezone(schedule['zone']).date()))
    for start, end in merged:
        if start <= now < end:
            if len(merged) == 1 and len(schedule['days']) == len(DAY_KEYS):
                return {'enabled': True, 'open': True, 'nextChangeAt': None}
            return {'enabled': True, 'open': True, 'nextChangeAt': end.strftime(ISO_FORMAT)}
    upcoming = min(start for start, _ in merged if start > now)
    return {'enabled': True, 'open': False, 'nextChangeAt': upcoming.strftime(ISO_FORMAT)}


def is_schedule_closed(db=None) -> bool:
    """True when the schedule is enabled and outside its window; fails closed on a database error."""
    try:
        return not schedule_state(db or Database())['open']
    except Exception as exc:
        logger.warning("Could not read processing schedule: %s", exc)
        return True

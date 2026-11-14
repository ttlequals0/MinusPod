"""Shared timestamp parsing, formatting, and cut-adjustment helpers."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ISO_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string (e.g. '2026-03-15T12:00:00Z')."""
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def is_valid_timezone(tz_name) -> bool:
    """True when `tz_name` is a resolvable IANA zone name."""
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def local_now_iso(tz_name) -> str:
    """Current time in tz_name as ISO 8601 with UTC offset; falls back to UTC for
    an unresolvable zone so a bad setting never crashes a notification."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = timezone.utc
    return datetime.now(tz).isoformat(timespec='seconds')


def local_iso(value, tz_name) -> str | None:
    """ISO 8601 UTC string rendered in tz_name with its UTC offset; None if value does not parse."""
    dt = parse_iso_utc(value)
    if dt is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = timezone.utc
    return dt.astimezone(tz).isoformat(timespec='seconds')


def epoch_to_iso(ts) -> str | None:
    """Epoch seconds as an ISO 8601 UTC string; None for falsy input."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime(ISO_FORMAT)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to a UTC-aware datetime; None if empty or unparseable.

    A naive result is treated as UTC.
    """
    if not value:
        return None
    try:
        dt = parse_iso_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_timestamp(ts) -> float:
    """Convert a timestamp to seconds.

    Accepts int/float, "1178.5" or "1178.5s", and [HH:]MM:SS[.mmm]. A comma
    decimal separator (common in VTT) is accepted. Raises ValueError if unparseable.
    """
    if isinstance(ts, (int, float)):
        return float(ts)

    if not ts or not isinstance(ts, str):
        raise ValueError(f"Cannot parse timestamp: {ts!r}")

    # Normalize: strip whitespace, remove 's' suffix, replace comma decimal
    ts = ts.strip().rstrip('s').strip().replace(',', '.')

    # Try direct float conversion first (handles "1178.5" etc.)
    try:
        return float(ts)
    except ValueError:
        pass

    # Try colon-separated formats
    parts = ts.split(':')

    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
    except (ValueError, IndexError):
        pass

    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def format_duration(seconds: float) -> str:
    """Format whole seconds as M:SS, or H:MM:SS past an hour."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_time(seconds: float, include_hours: bool = False) -> str:
    """Format seconds as H:MM:SS.ss, or M:SS.ss when under an hour and not include_hours."""
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    if hours > 0 or include_hours:
        return f"{hours}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes}:{secs:05.2f}"


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as a VTT/SRT timestamp (HH:MM:SS.mmm)."""
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def merge_cut_spans(cuts: list[dict], default_replacement: float = 0.0) -> list[list[float]]:
    """Merge overlapping/touching cut spans into [start, end, n_spans,
    total_replacement] groups.

    total_replacement sums each span's own `replacement_duration` (see
    audio_processor.compute_applied_cuts), falling back to
    `default_replacement` when a span omits it. One replacement is credited
    per source span even when cuts from different render passes merge.
    """
    sorted_cuts = sorted(cuts, key=lambda x: x.get('start', 0))
    merged: list[list[float]] = []
    for cut in sorted_cuts:
        start = cut.get('start', 0)
        end = cut.get('end', 0)
        if end <= start:
            continue
        replacement = cut.get('replacement_duration')
        if replacement is None:
            replacement = default_replacement
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += 1
            merged[-1][3] += replacement
        else:
            merged.append([start, end, 1, replacement])
    return merged


def span_inside_any_cut(start: float, end: float, cuts: list[dict]) -> bool:
    """True when [start, end] sits entirely inside the removed spans' union.

    A matching span has no surviving content and should be dropped rather
    than remapped. Cuts are merged first, so two overlapping cuts count as one.
    """
    return any(s <= start and end <= e for s, e, _, _ in merge_cut_spans(cuts))


def adjust_timestamp(original_time: float, ads_removed: list[dict],
                     replacement_duration: float = 0.0) -> float:
    """Map an original-audio timestamp (seconds) to its position after ad removal.

    Each ad ending before the timestamp subtracts its duration; a timestamp
    inside an ad snaps to that ad's start.

    ads_removed: [{'start', 'end'}]; a span's own 'replacement_duration' wins
    over the `replacement_duration` argument (seconds of beep inserted per
    span without one; content shifts by cut length minus replacement).
    """
    if not ads_removed:
        return original_time

    # Merge first: pass-1 and pass-2 cuts can overlap in original time and
    # would otherwise have the overlap subtracted twice.
    merged = merge_cut_spans(ads_removed, default_replacement=replacement_duration)

    return shift_for_merged_cuts(original_time, merged)


def shift_for_merged_cuts(original_time: float, merged: list[list[float]]) -> float:
    """Post-cut position of `original_time` given merge_cut_spans output."""
    adjustment = 0.0
    for ad_start, ad_end, _n_spans, total_replacement in merged:
        if ad_end <= original_time:
            # Group fully precedes us: shift back by removed length minus inserted replacements.
            adjustment += (ad_end - ad_start) - total_replacement
        elif ad_start < original_time < ad_end:
            # Timestamp falls within an ad: snap to the replacement's start.
            adjustment += (original_time - ad_start)
            break
        else:
            # Ad is after our timestamp
            break

    return max(0.0, original_time - adjustment)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 string, accepting the 'Z' suffix fromisoformat() rejects before 3.11."""
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Return the overlap duration (seconds) of [start_a, end_a] and [start_b, end_b].

    Zero when the ranges do not overlap.
    """
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def overlap_ratio(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Return the fraction of region B covered by region A (0.0-1.0); 0.0 if B has no duration."""
    overlap = overlap_seconds(start_a, end_a, start_b, end_b)
    b_duration = end_b - start_b
    return overlap / b_duration if b_duration > 0 else 0.0


def ranges_overlap(start_a: float, end_a: float, start_b: float, end_b: float,
                   tolerance: float = 0.0) -> bool:
    """True if [start_a, end_a] and [start_b, end_b] overlap.

    A positive `tolerance` widens both ranges symmetrically, so near-adjacent
    ranges count as overlapping.
    """
    return start_a <= end_b + tolerance and end_a >= start_b - tolerance

"""Time utility functions.

Provides shared timestamp parsing, formatting, and adjustment functions
used across the ad detection, transcription, and chapters pipeline.
"""

from typing import List, Dict


def parse_timestamp(ts) -> float:
    """Convert timestamp value to seconds.

    Supports multiple input types and formats:
    - int/float: passed through directly (e.g., 1178.5 -> 1178.5)
    - String with 's' suffix: "1178.5s" -> 1178.5
    - Float string: "1178.5" -> 1178.5
    - HH:MM:SS.mmm (e.g., "01:23:45.678")
    - HH:MM:SS (e.g., "01:23:45")
    - MM:SS.mmm (e.g., "23:45.678")
    - MM:SS (e.g., "23:45")
    - M:SS (e.g., "3:45")

    Also handles comma as decimal separator (common in some VTT files).

    Raises:
        ValueError: If the timestamp cannot be parsed
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


def format_time(seconds: float, include_hours: bool = False) -> str:
    """Format seconds as human-readable timestamp string.

    Returns:
        Formatted timestamp (H:MM:SS.ss or M:SS.ss)
    """
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    if hours > 0 or include_hours:
        return f"{hours}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes}:{secs:05.2f}"


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as VTT/SRT timestamp (HH:MM:SS.mmm).

    Always includes hours, zero-padded to 2 digits, with 3-digit milliseconds.
    """
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def adjust_timestamp(original_time: float, ads_removed: List[Dict]) -> float:
    """Adjust a timestamp to account for removed ad segments.

    For each ad that ends before the original timestamp, subtracts the
    ad's duration. If the timestamp falls within an ad, adjusts to
    the ad's start boundary.

    Args:
        original_time: Original timestamp in seconds
        ads_removed: List of {'start': float, 'end': float} for removed ads

    Returns:
        Adjusted timestamp reflecting position in processed audio
    """
    if not ads_removed:
        return original_time

    adjustment = 0.0
    sorted_ads = sorted(ads_removed, key=lambda x: x['start'])

    for ad in sorted_ads:
        ad_start = ad.get('start', 0)
        ad_end = ad.get('end', 0)

        if ad_end <= original_time:
            # Entire ad was before our timestamp
            adjustment += (ad_end - ad_start)
        elif ad_start < original_time < ad_end:
            # Timestamp falls within an ad -- snap to ad boundary
            adjustment += (original_time - ad_start)
            break
        else:
            # Ad is after our timestamp
            break

    return max(0.0, original_time - adjustment)


def first_not_none(*values):
    """Return the first value that is not None.

    Unlike Python's `or` operator, treats 0 and 0.0 as valid values.
    This is critical for timestamps where 0.0 is a valid pre-roll position.
    """
    for v in values:
        if v is not None:
            return v
    return None

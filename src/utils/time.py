"""Time utility functions.

Provides shared timestamp parsing and formatting functions.
"""


def parse_timestamp(ts: str) -> float:
    """Convert timestamp string to seconds.

    Supports multiple formats:
    - HH:MM:SS.mmm (e.g., "01:23:45.678")
    - HH:MM:SS (e.g., "01:23:45")
    - MM:SS.mmm (e.g., "23:45.678")
    - MM:SS (e.g., "23:45")
    - M:SS (e.g., "3:45")

    Also handles comma as decimal separator (common in some VTT files).

    Args:
        ts: Timestamp string

    Returns:
        Time in seconds (float), or 0.0 if parsing fails
    """
    if not ts or not isinstance(ts, str):
        return 0.0

    # Normalize: replace comma with period for decimal
    ts = ts.strip().replace(',', '.')
    parts = ts.split(':')

    try:
        if len(parts) == 3:
            # HH:MM:SS or HH:MM:SS.mmm
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            # MM:SS or MM:SS.mmm or M:SS
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 1:
            # Just seconds
            return float(parts[0])
    except (ValueError, IndexError):
        pass

    return 0.0


def format_time(seconds: float, include_hours: bool = False) -> str:
    """Format seconds as timestamp string.

    Args:
        seconds: Time in seconds
        include_hours: If True, always include hours. If False, only include
                      hours when needed (>= 1 hour)

    Returns:
        Formatted timestamp string (HH:MM:SS.mm or MM:SS.mm)
    """
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    if hours > 0 or include_hours:
        return f"{hours}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes}:{secs:05.2f}"


def format_time_simple(seconds: float) -> str:
    """Format seconds as simple MM:SS or HH:MM:SS string (no decimals).

    Args:
        seconds: Time in seconds

    Returns:
        Formatted timestamp string
    """
    if seconds < 0:
        seconds = 0

    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

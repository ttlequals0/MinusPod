"""Shared ffmpeg subprocess helpers used by audio analysis components."""
from utils.http import redact_stored_text

SAFE_MEDIA_INPUT_ARGS = ('-nostdin', '-protocol_whitelist', 'file,pipe')
SAFE_MEDIA_PROBE_ARGS = ('-protocol_whitelist', 'file,pipe')


def ffmpeg_timeout(duration_seconds: float) -> int:
    """Capped, duration-proportional timeout for ffmpeg passes.

    60s per minute of audio plus 120s, floored at 5 minutes and capped at 20 minutes.
    """
    return min(max(300, int(duration_seconds / 60) * 60 + 120), 1200)


def decode_stderr(result) -> str:
    """Safely decode ffmpeg stderr bytes, replacing non-UTF-8 chars."""
    try:
        return result.stderr.decode('utf-8', errors='replace')
    except Exception:
        return redact_stored_text(str(result.stderr))[:20000]

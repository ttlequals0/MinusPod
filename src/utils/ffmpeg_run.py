"""Shared ffmpeg subprocess helpers used by audio analysis components."""

SAFE_MEDIA_INPUT_ARGS = ('-nostdin', '-protocol_whitelist', 'file,pipe')
SAFE_MEDIA_PROBE_ARGS = ('-protocol_whitelist', 'file,pipe')


def ffmpeg_timeout(duration_seconds: float) -> int:
    """Capped, duration-proportional timeout for ffmpeg passes.

    Scales by 60s per minute of audio with a 5-minute floor and a 20-minute
    cap, so a long episode cannot stall the pipeline indefinitely while a
    short clip still gets reasonable headroom.
    """
    return min(max(300, int(duration_seconds / 60) * 60 + 120), 1200)


def decode_stderr(result) -> str:
    """Safely decode ffmpeg stderr bytes, replacing non-UTF-8 chars."""
    try:
        return result.stderr.decode('utf-8', errors='replace')
    except Exception:
        return str(result.stderr)[:20000]

"""Leaf helpers shared by the transcriber modules."""

import io
import math
import os
import struct
import wave


def unlink_quiet(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def probe_wav_bytes(duration_s: float = 1.0, rate: int = 16000) -> bytes:
    """One second of 440 Hz tone as an in-memory WAV (mono, 16-bit).

    Used as the upload for probe_transcription_endpoint. A tone rather than silence:
    some servers reject audio whose signal level is effectively zero before
    the request ever reaches the transcription path.
    """
    n = int(duration_s * rate)
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f'<{n}h', *samples))
    return buf.getvalue()

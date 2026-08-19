"""Silence span detection via ffmpeg silencedetect (task B2, Phase B).

Runs a single ffmpeg pass with the silencedetect filter and parses the
stderr output into a list of silence spans. Used by AudioAnalyzer when
the per-feed silence_snap_enabled flag is on.
"""

import logging
import os
import re
import subprocess

from utils.audio import get_audio_duration
from utils.ffmpeg_run import ffmpeg_timeout, decode_stderr
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.audio_analysis.silence')

# Matches: [silencedetect @ 0x...] silence_start: <t>
_START_RE = re.compile(r'silence_start:\s*([\d.]+)')
# Matches: [silencedetect @ 0x...] silence_end: <t> | silence_duration: <d>
_END_RE = re.compile(r'silence_end:\s*([\d.]+).*silence_duration:\s*([\d.]+)')


class SilenceDetector:
    """Detect silence spans in an audio file using ffmpeg silencedetect."""

    def __init__(self, noise_db: float, min_silence_s: float):
        self.noise_db = noise_db
        self.min_silence_s = min_silence_s

    def detect(self, audio_path: str) -> list[dict]:
        """Return silence spans for audio_path, empty list on any failure.

        Each span is {'start': float, 'end': float, 'duration': float},
        sorted by start.
        """
        if not os.path.exists(audio_path):
            logger.warning('Silence detector: file not found: %s', audio_path)
            return []

        duration = get_audio_duration(audio_path)
        if duration is None:
            logger.warning('Silence detector: could not determine duration for %s', audio_path)
            return []

        cmd = [
            'ffmpeg', '-v', 'info', '-i', audio_path,
            '-af', f'silencedetect=noise={self.noise_db}dB:d={self.min_silence_s}',
            '-f', 'null', '-',
        ]
        timeout = ffmpeg_timeout(duration)

        try:
            result = tracked_run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error('Silence detector timed out after %ds for %s', timeout, audio_path)
            return []
        except Exception as exc:
            logger.error('Silence detector ffmpeg pass failed: %s', exc)
            return []

        if result.returncode != 0:
            logger.error(
                'Silence detector ffmpeg exited %d for %s', result.returncode, audio_path
            )
            return []

        stderr_text = decode_stderr(result)

        spans = self._parse(stderr_text, duration)
        logger.info(
            'Silence detector: %d span(s) found in %s (noise=%.1fdB, min=%.2fs)',
            len(spans), audio_path, self.noise_db, self.min_silence_s,
        )
        return spans

    def _parse(self, stderr_text: str, duration: float) -> list[dict]:
        """Parse silencedetect stderr lines into a sorted list of spans."""
        pending_start: float | None = None
        spans: list[dict] = []

        for line in stderr_text.split('\n'):
            m_end = _END_RE.search(line)
            if m_end:
                if pending_start is not None:
                    try:
                        end = float(m_end.group(1))
                        dur = float(m_end.group(2))
                        spans.append({'start': pending_start, 'end': end, 'duration': dur})
                    except (ValueError, IndexError):
                        pass
                    pending_start = None
                continue

            m_start = _START_RE.search(line)
            if m_start:
                try:
                    pending_start = float(m_start.group(1))
                except (ValueError, IndexError):
                    pending_start = None

        # Unterminated trailing silence: file ended while still silent.
        if pending_start is not None:
            end = duration
            spans.append({
                'start': pending_start,
                'end': end,
                'duration': end - pending_start,
            })

        spans.sort(key=lambda s: s['start'])
        return spans

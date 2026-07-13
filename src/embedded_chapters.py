"""Remap embedded (ID3v2 CHAP) chapters onto the post-cut timeline.

ffmpeg copies chapters from the input to the output by default, so a
processed file kept its original chapter timestamps even though the ads
between them were cut (issue #500). remove_ads probes the input's chapters,
remaps them with the applied cuts, and feeds the corrected list back into the
same ffmpeg invocation as an ffmetadata input.
"""
import json
import logging
import subprocess
from typing import Dict, List, Optional

from config import FFPROBE_TIMEOUT
from utils.subprocess_registry import tracked_run
from utils.time import adjust_timestamp, span_inside_any_cut

logger = logging.getLogger(__name__)

# Remapped chapters shorter than this are artifacts of a cut collapsing two
# boundaries onto (nearly) the same instant; a sub-second chapter carries no
# usable content.
MIN_CHAPTER_SECONDS = 1.0


def probe_chapters(audio_path: str) -> Optional[List[Dict]]:
    """Read embedded chapters via ffprobe.

    Returns [] when the file definitively has no chapters, and None when the
    probe itself failed -- callers must NOT strip chapters on None, or a
    transient ffprobe failure would silently destroy them; falling back to
    ffmpeg's default passthrough keeps them (stale but recoverable).
    """
    cmd = [
        'ffprobe', '-v', 'quiet', '-show_chapters', '-of', 'json', audio_path,
    ]
    try:
        result = tracked_run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
        if result.returncode != 0:
            logger.warning(
                f"ffprobe chapter read failed (exit {result.returncode}) for "
                f"{audio_path}; keeping ffmpeg chapter passthrough")
            return None
        chapters = json.loads(result.stdout.decode('utf-8', errors='replace')).get('chapters', [])
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.warning(f"ffprobe chapter read failed for {audio_path}: {e}")
        return None

    out = []
    for ch in chapters:
        try:
            out.append({
                'start': float(ch['start_time']),
                'end': float(ch['end_time']),
                'title': (ch.get('tags') or {}).get('title', ''),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def remap_chapters(chapters: List[Dict], cuts: List[Dict], *,
                   replacement_duration: float, new_duration: float) -> List[Dict]:
    """Project chapters onto the post-cut timeline.

    Chapters that sit entirely inside a cut are dropped (their content is
    gone). Survivors shift back by the removed time, compensated for the
    replacement audio inserted per cut. Ends are rebuilt as a chain so the
    result is always contiguous, and degenerate slivers are dropped.
    """
    kept = []
    for ch in sorted(chapters, key=lambda c: c['start']):
        if span_inside_any_cut(ch['start'], ch['end'], cuts):
            continue
        kept.append({
            'start': adjust_timestamp(ch['start'], cuts, replacement_duration),
            'title': ch['title'],
        })

    # Drop slivers first, then chain ends over the survivors so the result
    # stays contiguous (a dropped sliver's span folds into its predecessor).
    survivors = [
        ch for i, ch in enumerate(kept)
        if (kept[i + 1]['start'] if i + 1 < len(kept) else new_duration) - ch['start'] >= MIN_CHAPTER_SECONDS
    ]
    return [
        {**ch, 'end': survivors[i + 1]['start'] if i + 1 < len(survivors) else new_duration}
        for i, ch in enumerate(survivors)
    ]


def _escape(value: str) -> str:
    """ffmetadata escaping: backslash first, then the metacharacters."""
    for char in ('\\', '=', ';', '#', '\n'):
        value = value.replace(char, '\\' + char)
    return value


def render_ffmetadata(chapters: List[Dict]) -> str:
    lines = [';FFMETADATA1']
    for ch in chapters:
        lines += [
            '[CHAPTER]',
            'TIMEBASE=1/1000',
            f"START={int(round(ch['start'] * 1000))}",
            f"END={int(round(ch['end'] * 1000))}",
            f"title={_escape(ch['title'])}",
        ]
    return '\n'.join(lines) + '\n'

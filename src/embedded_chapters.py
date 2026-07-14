"""Embedded (ID3v2 CHAP) chapter handling.

Two directions, one file:

- Remap onto the post-cut timeline (issue #500): ffmpeg copies chapters from
  the input to the output by default, so a processed file kept its original
  chapter timestamps even though the ads between them were cut. remove_ads
  probes the input's chapters, remaps them with the applied cuts, and feeds
  the corrected list back into the same ffmpeg invocation as an ffmetadata
  input.
- Embed generated chapters (issue #523): players like Castro ignore the
  podcast:chapters JSON and only read chapters embedded in the file, so the
  chapters MinusPod generates are also written into the served MP3.
"""
import json
import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

from config import FFMPEG_LONG_TIMEOUT, FFPROBE_TIMEOUT
from utils.audio import get_audio_duration
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


def chapters_to_spans(chapters: List[Dict], duration: float) -> List[Dict]:
    """Podcasting 2.0 chapter list -> contiguous start/end/title spans.

    The chapters JSON only carries start times; CHAP frames need ends, so
    each chapter ends where the next begins and the last ends at the file
    duration. Chapters at or past the duration are dropped.
    """
    starts = sorted(
        (float(ch.get('startTime', 0)), ch.get('title', ''))
        for ch in chapters
    )
    starts = [(s, t) for s, t in starts if s < duration]
    ends = [s for s, _ in starts[1:]] + [duration]
    return [
        {'start': s, 'end': e, 'title': t}
        for (s, t), e in zip(starts, ends)
    ]


def embed_chapters(audio_path: str, chapters: List[Dict],
                   duration: Optional[float] = None) -> bool:
    """Write generated chapters into an MP3 as ID3v2 CHAP/CTOC frames.

    Stream-copy remux with an ffmetadata side input (the same mechanism the
    cut path uses for remapped chapters), replacing any chapters already in
    the file so the embedded set matches the podcast:chapters JSON written
    alongside it. Callers pair this with save_chapters_json; a path that
    rebuilds the audio without regenerating chapters (recut) reverts the
    embedded set to the remapped source chapters until chapters are
    regenerated.

    The rewrite lands in a per-call temp file (unique name, so concurrent
    calls on the same episode cannot corrupt each other's output) and moves
    into place with os.replace, so a concurrent download never sees a
    half-written file. The temp name deliberately avoids the ``-v*.mp3``
    episode-version glob (src/storage.py) so a crash mid-embed cannot leave a
    stray file that cleanup mistakes for an episode version. On the
    regenerate-chapters path this rewrites the already-served MP3 in place;
    os.replace changes the file's identity so conditional clients refetch,
    but a client resuming a ranged download with no If-Range validator across
    the swap can still stitch mismatched bytes -- acceptable for a manual,
    infrequent action.

    duration is the file's duration in seconds when the caller already knows
    it; omitted, it is probed.

    Returns True on success; failures leave the original file untouched.
    """
    if not duration:
        duration = get_audio_duration(audio_path)
    if not duration:
        logger.warning(f"Chapter embed skipped: no duration for {audio_path}")
        return False
    spans = chapters_to_spans(chapters, duration)
    if not spans:
        logger.warning(f"Chapter embed skipped: no usable chapters for {audio_path}")
        return False

    audio_dir = os.path.dirname(audio_path)
    base = os.path.basename(audio_path)
    # Temp files share the audio's directory (same filesystem -> atomic
    # os.replace) but use unique, non-.mp3 suffixes so they neither collide
    # between concurrent calls nor match the episode-version glob.
    meta_fd, meta_path = tempfile.mkstemp(dir=audio_dir, prefix=f'.{base}.', suffix='.ffmeta')
    tmp_fd, tmp_path = tempfile.mkstemp(dir=audio_dir, prefix=f'.{base}.', suffix='.embedtmp')
    os.close(tmp_fd)
    try:
        with os.fdopen(meta_fd, 'w', encoding='utf-8') as f:
            f.write(render_ffmetadata(spans))
        cmd = [
            'ffmpeg', '-y',
            '-i', audio_path,
            '-f', 'ffmetadata', '-i', meta_path,
            '-map', '0', '-map_metadata', '0', '-map_chapters', '1',
            '-c', 'copy', '-f', 'mp3',
            tmp_path,
        ]
        result = tracked_run(cmd, capture_output=True, timeout=FFMPEG_LONG_TIMEOUT)
        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='replace')[-500:]
            logger.warning(f"Chapter embed failed for {audio_path}: {stderr}")
            return False
        os.replace(tmp_path, audio_path)
        logger.info(f"Embedded {len(spans)} chapters into {audio_path}")
        return True
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Chapter embed failed for {audio_path}: {e}")
        return False
    finally:
        for path in (meta_path, tmp_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


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

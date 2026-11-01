"""Import the chapters and transcript an upstream feed publishes for a pass-through episode."""

import logging

from transcript_differential import fetch_upstream_transcript
from transcript_generator import TranscriptGenerator
from upstream_chapters import chapters_document, fetch_upstream_chapters
from user_agent import download_user_agent

from main_app.pipeline.shared import (db, storage)

audio_logger = logging.getLogger('podcast.audio')


def _import_chapters(slug, episode_id, url, user_agent):
    chapters = fetch_upstream_chapters(url, user_agent=user_agent)
    if chapters is None:
        audio_logger.warning(f"[{slug}:{episode_id}] Upstream chapters fetch failed; skipping")
        return None
    if not chapters:
        return 0
    storage.save_chapters_json(slug, episode_id, chapters_document(chapters))
    audio_logger.info(f"[{slug}:{episode_id}] Imported {len(chapters)} upstream chapter(s)")
    return len(chapters)


def _timed_segments(cues):
    """Fill a missing cue end with the next cue's start (start + 1.0 for the last)."""
    segments = []
    for i, cue in enumerate(cues):
        seg = dict(cue)
        if seg.get('end') is None:
            seg['end'] = cues[i + 1]['start'] if i + 1 < len(cues) else seg['start'] + 1.0
        segments.append(seg)
    return segments


def _import_transcript(slug, episode_id, url, mime, user_agent):
    transcript = fetch_upstream_transcript(url, mime, user_agent=user_agent)
    if transcript is None:
        audio_logger.warning(f"[{slug}:{episode_id}] Upstream transcript fetch or parse failed; skipping")
        return 'error'
    if transcript.timed:
        segments = _timed_segments(transcript.cues)
        generator = TranscriptGenerator()
        text = generator.generate_text(segments, [], 0)
        vtt_enabled = db.get_setting('vtt_transcripts_enabled')
        vtt = (generator.generate_vtt(segments, [], 0)
               if vtt_enabled is None or vtt_enabled.lower() == 'true' else None)
    else:
        segments, vtt = None, None
        text = '\n'.join(c['text'] for c in transcript.cues)
    db.save_processing_assets(slug, episode_id, {
        'transcript_text': text, 'transcript_vtt': vtt,
        'original_segments': segments, 'final_segments': segments,
        'transcript_source': 'upstream', 'upstream_transcript': None})
    audio_logger.info(
        f"[{slug}:{episode_id}] Imported {'timed' if transcript.timed else 'untimed'} "
        f"upstream transcript ({len(transcript.cues)} cue(s))")
    return 'timed' if transcript.timed else 'untimed'


def import_upstream_assets(slug, episode_id, episode, podcast=None) -> dict:
    """Store the publisher's chapters and transcript; never raises."""
    episode = episode or {}
    try:
        user_agent = download_user_agent(podcast)
    except Exception:
        user_agent = None
    stats = {'upstream_chapters': None, 'upstream_transcript': 'none'}
    chapters_url = episode.get('upstream_chapters_url')
    transcript_url = episode.get('upstream_transcript_url')
    if chapters_url:
        try:
            stats['upstream_chapters'] = _import_chapters(slug, episode_id, chapters_url, user_agent)
        except Exception as e:
            audio_logger.warning(f"[{slug}:{episode_id}] Upstream chapters import failed: {e}")
    if transcript_url:
        try:
            stats['upstream_transcript'] = _import_transcript(
                slug, episode_id, transcript_url,
                episode.get('upstream_transcript_type'), user_agent)
        except Exception as e:
            audio_logger.warning(f"[{slug}:{episode_id}] Upstream transcript import failed: {e}")
            stats['upstream_transcript'] = 'error'
    return stats

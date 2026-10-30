"""YouTube acquisition: yt-dlp download, captions, SponsorBlock marks, run mode."""
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from main_app.pipeline import common
from main_app.pipeline.shared import audio_processor, db, storage
from reprocess_modes import FORCE_TRANSCRIBE_MODES
from utils.time import utc_now
from youtube import captions, download, sponsorblock, ytdlp
from youtube import settings as yt_settings
from youtube.settings import YouTubeSettings, llm_on_top

audio_logger = logging.getLogger('podcast.audio')


class SponsorBlockUnavailable(Exception):
    pass


@dataclass
class YouTubeRun:
    audio_path: str
    segments: list
    markers: list
    note: str | None
    mode: str
    resolved: YouTubeSettings


def _video_id(episode_data, episode_url) -> str:
    vid = (episode_data or {}).get('youtube_video_id')
    return vid or parse_qs(urlparse(episode_url).query).get('v', [''])[0]


def acquire(slug, episode_id, episode_url, podcast, episode_data, reprocess_mode) -> YouTubeRun:
    """Get the audio, captions and SponsorBlock marks, and pick the run mode."""
    resolved = yt_settings.resolve_feed(db, podcast)
    video_id = _video_id(episode_data, episode_url)
    if not video_id:
        raise Exception('YouTube episode has no video id')
    db.update_episode_sponsorblock(slug, episode_id, youtube_video_id=video_id)
    language = (podcast or {}).get('language_override') or 'en'
    want_captions = resolved.captions_as_transcript and (
        reprocess_mode in FORCE_TRANSCRIBE_MODES or not storage.get_transcript(slug, episode_id))

    original = storage.get_original_path(slug, episode_id)
    segments = []
    if original and os.path.exists(original):
        audio_logger.info(f"[{slug}:{episode_id}] Reusing retained original audio (skipped download)")
        audio_path = common._copy_retained_original_to_temp(original)
    else:
        common._publish_status('update_job_stage', slug, episode_id, 'downloading', 5)
        with common._measure_run_stage('download'):
            try:
                result = download.download(video_id, want_captions=want_captions, language=language)
            except download.DownloadError as exc:
                if exc.needs_update:
                    audio_logger.warning(f"[{slug}:{episode_id}] yt-dlp looks outdated; updating before retry")
                    ytdlp.update_binary(storage, db)
                raise Exception(str(exc)) from exc
        work_dir = os.path.dirname(result.audio_path)
        fd, audio_path = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        try:
            shutil.move(result.audio_path, audio_path)
            if want_captions:
                segments = captions.load_segments(result.captions_path)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    try:
        duration = audio_processor.get_audio_duration(audio_path)
        marks = sponsorblock.fetch_segments(video_id)
        markers, note = sponsorblock.to_markers(marks or [], duration)
        manual = bool(reprocess_mode) or (episode_data or {}).get('reprocess_source') is not None
        exhausted = (episode_data or {}).get('sb_state') == 'exhausted'
        if markers and not llm_on_top(podcast):
            mode = 'sponsorblock'
        elif markers or resolved.llm_fallback or manual or exhausted:
            mode = 'llm'
        else:
            raise SponsorBlockUnavailable(
                'No SponsorBlock marks yet; the episode stays unpublished until marks appear')
    except BaseException:
        if os.path.exists(audio_path):
            os.unlink(audio_path)
        raise
    audio_logger.info(f"[{slug}:{episode_id}] SponsorBlock: {len(markers)} segment(s), mode={mode}, "
                      f"captions={'yes' if segments else 'no'}")
    return YouTubeRun(audio_path, segments, markers, note, mode, resolved)


def after_success(slug, episode_id, run: YouTubeRun, episode_data=None):
    """Persist the SponsorBlock watch state once the episode is published."""
    prior = (episode_data or {}).get('sb_state')
    if run.markers:
        state = 'done'
        next_check = (utc_now() + timedelta(hours=run.resolved.watch_interval_hours)).isoformat()
    elif prior in ('llm', 'exhausted'):
        state, next_check = prior, None
    else:
        state, next_check = 'llm', None
    db.update_episode_sponsorblock(
        slug, episode_id, sb_state=state, sb_next_check_at=next_check, sb_last_error=None,
        sb_segment_uuids=json.dumps(sponsorblock.uuid_set(run.markers)), sb_note=run.note)
    _cache_thumbnail(slug, episode_id, (episode_data or {}).get('artwork_url'))


def _cache_thumbnail(slug, episode_id, artwork_url):
    """Cache the video thumbnail so the served feed can emit itunes:image."""
    if not artwork_url:
        return
    try:
        if not storage.has_episode_artwork(slug, episode_id):
            storage.download_episode_artwork(slug, episode_id, artwork_url)
    except Exception as exc:
        audio_logger.debug(f"[{slug}:{episode_id}] Thumbnail cache failed: {exc}")

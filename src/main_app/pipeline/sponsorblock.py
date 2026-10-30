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
from utils.markers import parse_ad_markers
from utils.time import utc_now
from youtube import captions, download, sponsorblock, ytdlp
from youtube import settings as yt_settings
from youtube.settings import YouTubeSettings, llm_on_top

audio_logger = logging.getLogger('podcast.audio')


class SponsorBlockDeferred(Exception):
    """No usable SponsorBlock marks yet: the run steps aside and the watcher retries."""

    def __init__(self, reason, next_check_at):
        super().__init__(reason)
        self.next_check_at = next_check_at


@dataclass
class YouTubeRun:
    audio_path: str
    segments: list
    markers: list
    note: str | None
    mode: str
    resolved: YouTubeSettings


def _retry_at(episode_data, resolved) -> str:
    """Next watcher check after a deferral: probe interval while probing, else the watch interval."""
    if (episode_data or {}).get('sb_state') in ('waiting', 'probing'):
        gap = timedelta(minutes=resolved.probe_interval_minutes)
    else:
        gap = timedelta(hours=resolved.watch_interval_hours)
    return (utc_now() + gap).isoformat()


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
    language = (podcast or {}).get('language_override')
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
                    try:
                        ytdlp.update_binary(storage, db)
                    except Exception as update_exc:
                        audio_logger.warning(f"[{slug}:{episode_id}] yt-dlp update failed: {update_exc}")
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
        forced = manual or exhausted
        if markers and not llm_on_top(podcast):
            mode = 'sponsorblock'
        elif markers or forced:
            mode = 'llm'
        elif note == 'duration_mismatch' and not resolved.llm_fallback:
            # Persist the duration so the watcher sees the same mismatch and counts it as no marks.
            db.upsert_episode(slug, episode_id, original_duration=duration)
            raise SponsorBlockDeferred('SponsorBlock marks do not match the video', _retry_at(episode_data, resolved))
        elif marks is None:
            raise SponsorBlockDeferred('SponsorBlock request failed', _retry_at(episode_data, resolved))
        elif resolved.llm_fallback:
            mode = 'llm'
        else:
            raise SponsorBlockDeferred('No SponsorBlock marks yet', _retry_at(episode_data, resolved))
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


def stamp_recut(slug, episode_id, podcast):
    """After a successful recut, record the SponsorBlock uuids now in the stored markers and re-arm the watch."""
    row = db.get_episode(slug, episode_id) or {}
    stored = [m for m in (parse_ad_markers(row.get('ad_markers_json')) or []) if m.get('source') == 'sponsorblock']
    resolved = yt_settings.resolve_feed(db, podcast)
    next_check = (utc_now() + timedelta(hours=resolved.watch_interval_hours)).isoformat()
    db.update_episode_sponsorblock(slug, episode_id, sb_segment_uuids=json.dumps(sponsorblock.uuid_set(stored)),
                                   sb_next_check_at=next_check)

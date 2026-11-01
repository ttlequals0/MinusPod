"""YouTube acquisition: yt-dlp download, captions, SponsorBlock marks, run mode."""
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import timedelta

from main_app.pipeline import common, keep_rules
from main_app.pipeline.shared import audio_processor, db, sponsor_service, storage
from reprocess_modes import FORCE_TRANSCRIBE_MODES
from utils.markers import parse_ad_markers
from utils.time import overlap_ratio, utc_now, utc_now_iso
from youtube import captions, download, metadata, sponsorblock, ytdlp
from youtube import settings as yt_settings
from youtube.settings import YouTubeSettings, llm_on_top

audio_logger = logging.getLogger('podcast.audio')
MARK_COVER_RATIO = 0.5


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


def _fill_metadata(slug, episode_id, episode_data, info):
    """Backfill the description and a full publish time that flat listings lack."""
    fields = metadata.fill_fields(episode_data, info)
    db.upsert_episode(slug, episode_id, yt_metadata_at=utc_now_iso(), **fields)


def _name_marks(markers, segments):
    """Stamp each mark with the registry sponsor its caption text names, else its category label."""
    for marker in markers:
        found = None
        if marker['category'] == 'sponsor' and segments:
            text = ' '.join(seg.get('text', '') for seg in segments
                            if seg.get('end', 0) >= marker['start'] and seg.get('start', 0) <= marker['end'])
            found = sponsor_service.find_sponsor_in_text(text.strip())
        marker['sponsor'] = found or sponsorblock.SPONSORBLOCK_CATEGORY_LABELS.get(marker['category'])
        if not found:
            marker['label_sponsor'] = True


def _fetch_retained_captions(slug, episode_id, video_id, language):
    """Captions for a run that reuses retained audio; a failed fetch leaves the run captionless."""
    try:
        path = download.fetch_captions(video_id, language=language)
    except download.DownloadError as exc:
        audio_logger.warning(f"[{slug}:{episode_id}] Caption fetch failed: {exc}")
        return []
    if not path:
        return []
    try:
        return captions.load_segments(path)
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def acquire(slug, episode_id, episode_url, podcast, episode_data, reprocess_mode) -> YouTubeRun:
    """Get the audio, captions and SponsorBlock marks, and pick the run mode."""
    resolved = yt_settings.resolve_feed(db, podcast)
    video_id = metadata.video_id_of(episode_data, episode_url)
    if not video_id:
        raise Exception('YouTube episode has no video id')
    db.update_episode_sponsorblock(slug, episode_id, youtube_video_id=video_id)
    language = (podcast or {}).get('language_override')
    want_captions = resolved.captions_as_transcript and (
        reprocess_mode in FORCE_TRANSCRIBE_MODES or not storage.get_transcript(slug, episode_id))

    manual = bool(reprocess_mode) or (episode_data or {}).get('reprocess_source') is not None
    exhausted = (episode_data or {}).get('sb_state') == 'exhausted'
    forced = manual or exhausted
    if not forced and sponsorblock.is_unreachable(db):
        gap = timedelta(minutes=resolved.probe_interval_minutes)
        raise SponsorBlockDeferred('SponsorBlock is unreachable', (utc_now() + gap).isoformat())

    original = storage.get_original_path(slug, episode_id)
    segments = []
    if original and os.path.exists(original):
        audio_logger.info(f"[{slug}:{episode_id}] Reusing retained original audio (skipped download)")
        audio_path = common._copy_retained_original_to_temp(original)
        if want_captions:
            segments = _fetch_retained_captions(slug, episode_id, video_id, language)
    else:
        common._publish_status('update_job_stage', slug, episode_id, 'downloading', 5)
        with common._measure_run_stage('download'):
            try:
                result = download.download(video_id, want_captions=want_captions, language=language)
            except download.DownloadError as exc:
                if exc.needs_update:
                    audio_logger.warning(f"[{slug}:{episode_id}] yt-dlp looks outdated; updating before retry")
                    ytdlp.update_after_failure(db, audio_logger, f"[{slug}:{episode_id}] ")
                raise Exception(str(exc)) from exc
        _fill_metadata(slug, episode_id, episode_data, result.info)
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
        with common._measure_run_stage('sponsorblock'):
            marks = sponsorblock.fetch_segments(video_id)
        markers, note = sponsorblock.to_markers(marks or [], duration)
        _name_marks(markers, segments)
        no_captions = want_captions and not segments and not resolved.transcribe_fallback
        if no_captions:
            if markers:
                mode = 'sponsorblock'
            elif forced:
                mode = 'passthrough'
            else:
                raise SponsorBlockDeferred('No captions and transcription is off', _retry_at(episode_data, resolved))
            note = f'{note},no_captions' if note else 'no_captions'
        elif markers and not (llm_on_top(podcast) or reprocess_mode in ('full', 'llm')):
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


def _covered_by(ad, marks) -> bool:
    return any(overlap_ratio(m['start'], m['end'], ad['start'], ad['end']) >= MARK_COVER_RATIO for m in marks)


def merge_marks(yt_run, ads_to_remove, all_ads_with_validation, keep_ads, segment_actions):
    """Fold SponsorBlock marks into the post-validation ad lists; returns the lists plus the dropped count.

    Marks skip validation (its duration cap and refinement would alter them), a kept category
    still wins, and a mark outranks any detection it covers so learned patterns never double a row.
    """
    mark_keep, mark_cuts = keep_rules._partition_keep_ads(
        [dict(m) for m in yt_run.markers], segment_actions, None)
    kept = [ad for ad in all_ads_with_validation if not _covered_by(ad, mark_cuts)]
    dropped = len(all_ads_with_validation) - len(kept)
    if dropped:
        audio_logger.info(f"Dropped {dropped} detection(s) covered by SponsorBlock marks")
    ads_to_remove = sorted([ad for ad in ads_to_remove if not _covered_by(ad, mark_cuts)] + mark_cuts,
                           key=lambda x: x['start'])
    all_ads_with_validation = sorted(kept + mark_cuts, key=lambda x: x['start'])
    return ads_to_remove, all_ads_with_validation, keep_ads + mark_keep, dropped


def after_success(slug, episode_id, run: YouTubeRun, episode_data=None):
    """Persist the SponsorBlock watch state once the episode is published."""
    prior = (episode_data or {}).get('sb_state')
    if run.markers:
        state = 'done'
        next_check = (utc_now() + timedelta(hours=run.resolved.watch_interval_hours)).isoformat()
    elif run.mode == 'passthrough':
        state, next_check = 'exhausted', None
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

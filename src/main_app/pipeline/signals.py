"""Audio analysis, cue matching, cross-fetch differential and transcript differential."""

import json
import logging
import shutil
import tempfile
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from urllib.parse import urlparse
from audio_analysis.audio_analyzer import MIN_VOLUME_TIMEOUT
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
from differential_fetcher import (
    fetch_and_diff,
    is_likely_dai_feed,
)

from utils.time import (
    parse_iso_utc, utc_now_iso,
)

from config import (
    is_template_cue,
    resolve_transcript_differential,
    differential_fetch_effective,
    resolve_differential_fetch_mode,
)

from database.podcasts import is_local_feed
from transcript_differential import align, fetch_upstream_transcript
from splice_calibration import compute_splice_calibration
from user_agent import download_user_agent

from main_app.pipeline import common
from main_app.pipeline.shared import (db, audio_analyzer)

audio_logger = logging.getLogger('podcast.audio')


def _run_audio_analysis(slug, episode_id, audio_path, segments, force_cue_detection=False):
    """Pipeline stage: Run volume + transition detection on audio."""
    common._publish_status('update_job_stage', slug, episode_id, "pass1:analyzing", 25)
    audio_logger.info(f"[{slug}:{episode_id}] Running audio analysis")
    try:
        # Resolve the feed PK so the cue analyzer can pick a per-feed template
        # matcher when the user has marked any (issue #350).
        podcast_row = db.get_podcast_by_slug(slug)
        feed_id = podcast_row.get('id') if podcast_row else None
        result = audio_analyzer.analyze(
            audio_path,
            transcript_segments=segments,
            feed_id=feed_id,
            force_cue_detection=force_cue_detection,
            status_callback=lambda stage, progress: common._publish_status(
                'update_job_stage', slug, episode_id, stage, progress)
        )
        if result.signals:
            audio_logger.info(
                f"[{slug}:{episode_id}] Audio analysis: {len(result.signals)} signals "
                f"in {result.analysis_time_seconds:.1f}s"
            )
        if result.errors:
            for err in result.errors:
                audio_logger.warning(f"[{slug}:{episode_id}] Audio analysis warning: {err}")

        # Attach per-feed calibration (spec 2.2) before persisting so the
        # stored payload and all downstream consumers see the same dict.
        # The detector stamps a cold_start placeholder; this replaces it.
        if result.splice_evidence is not None:
            result.splice_evidence['calibration'] = compute_splice_calibration(
                db, slug, exclude_episode_id=episode_id)
        db.save_episode_audio_analysis(slug, episode_id, json.dumps(result.to_dict()))
        return result
    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Audio analysis failed: {e}")
        db.clear_leaked_transaction(audio_logger, 'audio analysis')
        return None


def _make_validator_audio_analysis(audio_analysis_result, dai_differential):
    """Build the audio_analysis dict passed to AdValidator.

    Carries dai_differential even when Layer 2 (audio analysis) returned None
    so that a Layer 2 failure does not suppress Layer 3 (cross-fetch
    differential) corroboration. Mirrors the recut path which manually merges
    dai_differential into the stored audio_analysis dict.
    """
    if audio_analysis_result is not None:
        return audio_analysis_result.to_dict()
    if dai_differential is not None:
        return {'dai_differential': dai_differential}
    return None


def _feed_cue_matcher(podcast_id):
    """The feed's template cue matcher for differential cue fusion, or None.

    Reuses the analyzer's resolver (master toggle, per-feed templates, score
    threshold, formant profile) so both scans agree on what counts as a cue;
    the spectral fallback detector is deliberately excluded -- only precise
    template matches may anchor alignment or corroborate a differential.
    Never raises.
    """
    if podcast_id is None:
        return None
    try:
        _enabled, detector = audio_analyzer._load_cue_config(podcast_id)
    except Exception as e:
        audio_logger.warning(f"Cue matcher resolve failed (non-fatal): {e}")
        return None
    return detector if isinstance(detector, AudioCueTemplateMatcher) else None


def _template_cue_scan(matcher, path):
    """Template cue marks [{'time', 'template_id'}] found in one audio file."""
    return [
        {'time': float(s.start),
         'template_id': (s.details or {}).get('template_id')}
        for s in matcher.detect(path)
        if is_template_cue(s.details)
    ]


def _template_cue_marks(audio_analysis_result):
    """The analyzer's template cues in _template_cue_scan's shape."""
    return [
        {'time': float(s.start),
         'template_id': (s.details or {}).get('template_id')}
        for s in audio_analysis_result.get_signals_by_type('audio_cue')
        if is_template_cue(s.details)
    ]


# The differential worker needs the primary cues only once its refetch has
# downloaded, by which point audio analysis has normally finished. Bounded by
# the analysis floor timeout so a stalled analysis cannot park the worker.
PRIMARY_CUE_WAIT_SECONDS = float(MIN_VOLUME_TIMEOUT)


def _publish_primary_cues(future, audio_analysis_result) -> None:
    """Hand the original's template cues to the differential worker. A None
    result tells it nothing usable came out of analysis and to scan itself."""
    if future.done():
        return
    marks = None
    if getattr(audio_analysis_result, 'cue_scan_complete', False):
        # Called from a finally: a raise here would mask the original failure
        # and leave the differential worker waiting out its whole timeout.
        try:
            marks = _template_cue_marks(audio_analysis_result)
        except Exception as e:
            audio_logger.warning(f"Could not publish primary cues: {e}")
    future.set_result(marks)


def _run_differential_fetch(slug, episode_id, episode_url, audio_path, podcast_id,
                            dai_platform=None, podcast=None,
                            primary_cue_future=None, download_outcome=None):
    """Pipeline stage: cross-fetch differential (Layer 3).

    Runs when the per-feed flag is on, or -- when the flag is unset -- when
    the feed looks DAI-served (a detected platform or a DAI-prefix enclosure
    URL), so inserted ads are caught on the first processing without manual
    opt-in (#519). An explicit per-feed 0 still disables it.

    Returns the fetch_and_diff result dict, or None when the stage is
    skipped or an unexpected failure occurs. Never raises (except
    ProcessingCancelled): the flag read, fetch, and store are all guarded so
    nothing here can fail the episode. The pipeline continues.

    Runs on a worker thread; it must never stamp job status. The caller
    stamps pass1:differential from the main thread before starting the
    worker, so an abandoned worker (episode failed meanwhile) can never
    stamp a different job.

    `podcast`: the caller's already-fetched podcast row (avoids a redundant
    per-episode db.get_podcast_by_slug on this worker thread). None is
    treated as non-local, matching the behavior when no row is available.
    """
    if is_local_feed(podcast):
        audio_logger.debug(
            f"[{slug}:{episode_id}] Differential fetch skipped: local feed")
        return None
    try:
        explicit = resolve_differential_fetch_mode(db, podcast_id)
        if not differential_fetch_effective(
                explicit, dai_platform=dai_platform,
                dai_likely=is_likely_dai_feed([episode_url])):
            return None
        audio_logger.info(
            f"[{slug}:{episode_id}] Differential fetch: starting"
            f"{' (auto: DAI-likely feed)' if explicit is None else ''}")
        # Cue fusion (2.76.0): the refetch scan runs between download and alignment so its
        # cues both persist in the stored result and anchor that pass's probe offsets;
        # primary cues come from the concurrent analysis, so the file is scanned once per run.
        matcher = _feed_cue_matcher(podcast_id)
        cue_scan = None
        if matcher is None:
            def primary_cues():
                return []
        else:
            def primary_cues():
                shared = None
                if primary_cue_future is not None:
                    try:
                        shared = primary_cue_future.result(PRIMARY_CUE_WAIT_SECONDS)
                    except FuturesTimeoutError:
                        audio_logger.warning(
                            f"[{slug}:{episode_id}] Audio analysis has not "
                            f"produced cues in {PRIMARY_CUE_WAIT_SECONDS:.0f}s; "
                            f"scanning the primary audio for cues")
                if shared is not None:
                    return shared
                audio_logger.info(
                    f"[{slug}:{episode_id}] No shared cue scan available; "
                    f"scanning the primary audio for cues")
                try:
                    return _template_cue_scan(matcher, audio_path)
                except Exception as e:
                    audio_logger.warning(
                        f"[{slug}:{episode_id}] Primary cue scan failed "
                        f"(non-fatal): {e}")
                    return []

            def cue_scan(path):
                return _template_cue_scan(matcher, path)
        work_dir = tempfile.mkdtemp(prefix='dai_diff_')
        try:
            with common._measure_run_stage('differential'):
                result = fetch_and_diff(episode_url, audio_path, work_dir,
                                        cue_scan=cue_scan,
                                        primary_cues=primary_cues,
                                        primary_user_agent=(download_outcome or {}).get(
                                            'download_user_agent') or download_user_agent(podcast),
                                        fallback_user_agent=(download_outcome or {}).get(
                                            'fallback_user_agent'))
        except Exception as e:
            # fetch_and_diff traps expected failures itself; this guards the rest.
            audio_logger.warning(f"[{slug}:{episode_id}] Differential fetch failed: {e}")
            result = {'status': 'error', 'regions': [], 'refetch_meta': {},
                      'error': str(e)}
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        try:
            db.save_episode_dai_differential(slug, episode_id, json.dumps(result))
        except Exception as e:
            audio_logger.warning(f"[{slug}:{episode_id}] Differential store failed: {e}")
            db.clear_leaked_transaction(audio_logger, 'differential store')
        diff_count = len([r for r in result.get('regions', [])
                          if r.get('kind') == 'differential'])
        audio_logger.info(
            f"[{slug}:{episode_id}] Differential fetch: status={result.get('status')} "
            f"differential_regions={diff_count}")
        return result
    except Exception as e:
        # Outer non-fatal boundary: the flag read, status update, or mkdtemp
        # can raise outside the inner guards; the episode must not fail here.
        audio_logger.warning(f"[{slug}:{episode_id}] Differential stage failed: {e}")
        db.clear_leaked_transaction(audio_logger, 'differential stage')
        return None


TRANSCRIPT_DIFF_REUSE_SECONDS = 24 * 3600


def _reusable_transcript_diff(stored, url):
    """A stored ok result for the same URL fetched within the reuse window, else None."""
    if not stored or stored.get('status') != 'ok' or stored.get('source_url') != url:
        return None
    fetched = parse_iso_utc(stored.get('fetched_at'))
    if fetched is None or time.time() - fetched.timestamp() > TRANSCRIPT_DIFF_REUSE_SECONDS:
        return None
    return stored


def _run_transcript_diff(slug, episode_id, episode_row, segments, run_stats, *, podcast=None,
                         segments_reused=False):
    """Diff the publisher transcript; reuse results only when transcript segments were reused."""
    url = (episode_row or {}).get('upstream_transcript_url')
    payload = {'status': 'none', 'source_url': url, 'mime': None, 'timed': None,
               'coverage': None, 'spans': [], 'fetched_at': None, 'error': None}
    reused = False
    persist = True
    host = 'unknown'

    def fail(error):
        payload.update(status='error', error=error, spans=[])

    if url:
        common._publish_status('update_job_stage', slug, episode_id, "pass1:transcript_diff", 21)
    try:
        if url:
            host = urlparse(url).hostname or host
        if url and segments and resolve_transcript_differential(podcast, db):
            stored = (_reusable_transcript_diff(
                db.get_episode_upstream_transcript(slug, episode_id), url)
                if segments_reused else None)
            if stored is not None:
                payload.update(stored)
                reused = True
            else:
                payload['fetched_at'] = utc_now_iso()
                with common._measure_run_stage('transcript_diff'):
                    try:
                        transcript = fetch_upstream_transcript(
                            url, episode_row.get('upstream_transcript_type'),
                            user_agent=download_user_agent(podcast))
                        if transcript is None:
                            fail('fetch or parse failed')
                        else:
                            result = align(segments, transcript)
                            payload.update(status=result['status'], mime=transcript.mime,
                                           timed=result['timed'],
                                           coverage=result['coverage'],
                                           spans=result['spans'])
                    except Exception as e:
                        fail(str(e))
        elif url:
            # Toggle off or no segments this run: never clobber a stored result.
            persist = False
        else:
            # Nothing to record unless an earlier result would otherwise go stale.
            stored = db.get_episode_upstream_transcript(slug, episode_id)
            persist = bool(stored) and stored.get('status') != 'none'
    except Exception as e:
        audio_logger.warning(f"[{slug}:{episode_id}] Transcript diff stage failed: {e}")
        db.clear_leaked_transaction(audio_logger, 'transcript diff stage')
        if url:
            fail(str(e))
        else:
            # Without a URL a failed read must not overwrite the stored payload.
            persist = False
    if persist and not reused:
        try:
            db.save_episode_upstream_transcript(slug, episode_id, payload)
        except Exception as e:
            audio_logger.warning(f"[{slug}:{episode_id}] Transcript diff store failed: {e}")
            db.clear_leaked_transaction(audio_logger, 'transcript diff store')
    coverage = payload.get('coverage')
    run_stats['transcript_diff'] = {'status': payload['status'], 'coverage': coverage,
                                    'spans': len(payload.get('spans') or [])}
    if url:
        audio_logger.info(
            f"[{slug}:{episode_id}] Transcript diff: status={payload['status']} "
            f"coverage={'n/a' if coverage is None else f'{coverage:.2f}'} "
            f"spans={run_stats['transcript_diff']['spans']} "
            f"source={host}{' (reused)' if reused else ''}")
    return payload

"""Chapter remapping, chapter publishing and asset generation."""

import logging
import threading
from contextlib import contextmanager
from ad_chapters import (
    ID3_CHAPTER_SOURCE_KEY,
    merge_ad_chapters, public_chapters, refresh_keep_like_markers,
    resolve_ad_chapter_config, strip_ad_chapters,
)

from audio_processor import get_replacement_duration
from cancel import (
    ProcessingCancelled, ProcessingOwnershipLost,
)

from utils.audio import get_audio_duration
from utils.time import (
    adjust_timestamp, merge_cut_spans, span_inside_any_cut,
)

from config import (
    CHAPTERS_MODE_AUTO,
    CHAPTERS_MODE_OFF,
    MIN_PRESERVED_CHAPTERS,
    is_pending_review, is_keep_like,
    resolve_chapters_mode,
)

from embedded_chapters import embed_chapters, probe_chapters, MIN_CHAPTER_SECONDS
from id3_chapters import ChapterTagError
from upstream_chapters import fetch_upstream_chapters
from llm_capabilities import (
    PASS_CHAPTER_GENERATION, clear_fallback,
)

from llm_client import (
    ProviderAccountChangedError, ProviderRateLimitedError,
)

from rate_limit_hold import (
    hold_message,
    hold_queue_for_provider_limit,
)

from user_agent import download_user_agent
from utils.constants import EpisodeStatus

from main_app.pipeline import common
from main_app.pipeline.shared import (db, storage)

audio_logger = logging.getLogger('podcast.audio')


def _unadjust_timestamp(processed_time, cuts, replacement_duration=0.0):
    """Inverse of utils.time.adjust_timestamp: project a processed-timeline
    timestamp back onto the original (pre-cut) timeline. All values are
    seconds; cuts are in original-episode coordinates. A timestamp that falls
    inside a replacement beep has no original content behind it and maps to
    the cut's start (the point the beep replaced).

    Each span's own 'replacement_duration' (see utils.time.merge_cut_spans)
    wins over the `replacement_duration` argument, which is only the
    fallback for spans that omit it (legacy persisted cuts)."""
    removed = 0.0
    replaced = 0.0
    for start, end, _n_spans, total_replacement in merge_cut_spans(
            cuts, default_replacement=replacement_duration):
        beep_block_start = start - removed + replaced
        if processed_time < beep_block_start:
            break
        if processed_time < beep_block_start + total_replacement:
            return start
        removed += end - start
        replaced += total_replacement
    return processed_time + removed - replaced


def _remap_chapters_for_recut(chapters, previous_cuts, new_cuts,
                               replacement_duration, original_duration,
                               new_duration):
    """Project stored chapters-JSON chapters onto the recut timeline.

    chapters carry startTime in SECONDS (Podcasting 2.0; the ms conversion
    happens only inside embedded_chapters.render_ffmetadata) on the PREVIOUS
    processed timeline. previous_cuts and new_cuts are both in
    original-episode coordinates, so each start goes previous-processed ->
    original (inverse of the previous cut adjustment) -> recut timeline
    (adjust_timestamp with the new applied cuts).

    Mirrors embedded_chapters.remap_chapters' policy: a chapter whose whole
    span (start to next chapter's start, in original coordinates) sits inside
    a new cut is dropped -- its span folds into its predecessor -- and a
    remapped chapter left closer than MIN_CHAPTER_SECONDS to its successor is
    dropped as a degenerate sliver. Titles, urls, and any other keys are
    carried through untouched."""
    ordered = sorted(chapters, key=lambda ch: float(ch.get('startTime', 0)))
    orig_starts = [
        _unadjust_timestamp(float(ch.get('startTime', 0)), previous_cuts,
                            replacement_duration)
        for ch in ordered
    ]
    remapped = []
    for i, ch in enumerate(ordered):
        orig_start = orig_starts[i]
        orig_end = (orig_starts[i + 1] if i + 1 < len(ordered)
                    else original_duration)
        if span_inside_any_cut(orig_start, orig_end, new_cuts):
            continue
        remapped.append(
            (adjust_timestamp(orig_start, new_cuts, replacement_duration), ch))
    survivors = [
        (start, ch) for i, (start, ch) in enumerate(remapped)
        if (remapped[i + 1][0] if i + 1 < len(remapped) else new_duration)
        - start >= MIN_CHAPTER_SECONDS
    ]
    out = []
    for start, ch in survivors:
        # Same output shape as ChaptersGenerator: integer seconds, minimum 1.
        new_start = max(1, int(round(start)))
        if out and new_start <= out[-1]['startTime']:
            continue
        out.append({**ch, 'startTime': new_start})
    return out


def _ad_chapter_count(chapters):
    return len(chapters) - len(strip_ad_chapters(chapters))


def _publisher_chapter_entries(publisher):
    entries = []
    for index, chapter in enumerate(publisher):
        entry = {key: value for key, value in chapter.items()
                 if key in ('title', 'url', 'img', '_id3_id', '_id3_end')}
        entry['startTime'] = (round(chapter['start'], 3) if chapter.get('_id3_id')
                              else max(1, int(round(chapter['start']))))
        entry['title'] = chapter.get('title') or f'Chapter {index + 1}'
        image = chapter.get('_id3_image')
        if image is not None:
            entry['_id3_image'] = image
        entries.append(entry)
    return entries


def _publish_chapters(slug, episode_id, chapters_json, merged, all_cuts,
                      audio_path, audio_duration, embed, label, pending_assets):
    """Validate embedded chapters before staging their JSON and cut map."""
    if embed and audio_path:
        embedded = embed_chapters(str(audio_path), public_chapters(merged, for_embedding=True),
                                  duration=audio_duration)
        if not embedded and any(ch.get('_id3_id') for ch in merged):
            raise ChapterTagError('Publisher chapters could not be embedded')
    pending_assets.update(chapters={**chapters_json, 'chapters': merged},
                          applied_cuts=all_cuts or [])
    audio_logger.info(
        f"[{slug}:{episode_id}] {label}, {_ad_chapter_count(merged)} ad "
        f"chapter entries")


def _remap_stored_chapters(slug, episode_id, all_cuts, replacement_duration,
                            previous_cuts, original_duration,
                            audio_path=None, audio_duration=None,
                            markers=None, podcast_row=None, *, pending_assets):
    """Stage recut chapters, refusing unsafe publisher metadata changes."""
    rich_source = False
    try:
        chapters_json = storage.get_chapters_json(slug, episode_id)
        stored = (chapters_json or {}).get('chapters') or []
        # Stale ad chapters are rebuilt from the recut's markers, never remapped.
        chapters = strip_ad_chapters(stored)
        rich_source = ((chapters_json or {}).get(ID3_CHAPTER_SOURCE_KEY) == 'id3'
                       or any(ch.get('_id3_id') for ch in chapters))
        source_entries = []
        if rich_source:
            publisher = probe_chapters(str(audio_path)) if audio_path else None
            if publisher is None:
                raise ChapterTagError('Publisher chapter metadata is unavailable')
            source_entries = _publisher_chapter_entries([ch for ch in publisher if ch.get('_id3_id')])
            chapters = [ch for ch in chapters if not ch.get('_id3_id')]
            chapters_json = {**(chapters_json or {}), ID3_CHAPTER_SOURCE_KEY: 'id3'}
        had_ads = len(stored) != len(chapters)
        # had_ads still writes: the stale entries must leave the JSON and ID3
        # even when nothing replaces them.
        nothing_stored = not chapters and not had_ads and not rich_source
        if nothing_stored and not markers:
            return
        ad_config = resolve_ad_chapter_config(
            db, podcast_row or db.get_podcast_by_slug(slug))
        if nothing_stored and ad_config is None:
            return
        # One resolved duration for BOTH the JSON sliver filter and the ID3
        # embed, so the served and embedded chapter sets trim against the same
        # tail bound. Prefer the caller's known value, then a probe of the
        # rendered file, then the arithmetic projection.
        resolved_duration = audio_duration
        if resolved_duration is None and audio_path:
            resolved_duration = get_audio_duration(audio_path)
        if resolved_duration is None and original_duration:
            resolved_duration = adjust_timestamp(
                original_duration, all_cuts, replacement_duration)
        if previous_cuts is None:
            # No authoritative previous cuts: the topic chapters keep their old
            # timestamps, but the ad entries are rebuilt from this recut's own
            # cut list instead of being left on the previous timeline.
            merged = merge_ad_chapters(sorted(chapters + source_entries, key=lambda ch: ch['startTime']), markers, all_cuts or [],
                                       resolved_duration, replacement_duration,
                                       ad_config)
            if merged == stored:
                return
            if audio_path and not embed_chapters(
                    str(audio_path), public_chapters(merged, for_embedding=True),
                    duration=resolved_duration):
                if any(ch.get('_id3_id') for ch in merged):
                    raise ChapterTagError('Publisher chapters could not be embedded')
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Chapter embed failed after recut; "
                    f"keeping previous chapters JSON and embedded ID3")
                return
            # Chapters alone: all_cuts is not the list these topic chapters sit
            # on, so it must not become the authoritative one.
            updated_chapters = {**(chapters_json or {'version': '1.2.0'}), 'chapters': merged}
            pending_assets['chapters'] = updated_chapters
            if rich_source and not chapters:
                pending_assets['applied_cuts'] = all_cuts or []
            audio_logger.info(
                f"[{slug}:{episode_id}] Rebuilt {_ad_chapter_count(merged)} ad "
                f"chapter entries without a remap (no authoritative previous cuts)")
            return
        if not original_duration:
            audio_logger.warning(
                f"[{slug}:{episode_id}] No original duration for chapter "
                f"remap; keeping previous chapters JSON")
            return
        remapped = _remap_chapters_for_recut(
            chapters, previous_cuts, all_cuts or [],
            replacement_duration, original_duration, resolved_duration)
        remapped = sorted(remapped + source_entries, key=lambda ch: ch['startTime'])
        merged = merge_ad_chapters(remapped, markers, all_cuts or [],
                                   resolved_duration, replacement_duration,
                                   ad_config)
        if not merged and not had_ads and not rich_source:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Chapter remap swallowed every "
                f"chapter; keeping previous chapters JSON")
            return
        # Embed FIRST, save JSON only after it succeeds: if the embed fails,
        # the served JSON and the embedded ID3 must stay the OLD, matching set
        # (issue #523 was the reverse order -- new JSON, stale ID3). This is a
        # set replacement of the ffmpeg cut step's remapped source chapters,
        # not a second remap: embed_chapters writes the timestamps as-is and
        # returns False for ffmpeg/OS errors; unsafe source metadata raises.
        if audio_path:
            if not embed_chapters(str(audio_path), public_chapters(merged, for_embedding=True),
                                  duration=resolved_duration):
                if rich_source:
                    raise ChapterTagError('Publisher chapters could not be embedded')
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Chapter embed failed after recut; "
                    f"keeping previous chapters JSON and embedded ID3")
                return
        # The remapped JSON lives on the recut timeline defined by all_cuts;
        # both persist in ONE DB write so a failure can never pair fresh
        # chapters with a stale authoritative cut list (that pairing makes
        # the NEXT remap unproject through the wrong previous cuts).
        updated_chapters = {**(chapters_json or {'version': '1.2.0'}), 'chapters': merged}
        pending_assets.update(chapters=updated_chapters, applied_cuts=all_cuts or [])
        audio_logger.info(
            f"[{slug}:{episode_id}] Remapped {len(chapters)} stored "
            f"chapter(s) -> {len(merged)} onto the recut timeline "
            f"(no AI call)")
    except ChapterTagError:
        raise
    except Exception as e:
        if rich_source:
            raise ChapterTagError('Publisher chapter assets could not be prepared') from e
        audio_logger.warning(
            f"[{slug}:{episode_id}] Failed to remap stored chapters after "
            f"recut; keeping previous chapters JSON: {e}")


_embed_locks = {}
_embed_locks_guard = threading.Lock()


@contextmanager
def _episode_embed_lock(key):
    """Serialize chapter embeds per episode; the entry is dropped once idle."""
    with _embed_locks_guard:
        lock, waiters = _embed_locks.get(key, (threading.Lock(), 0))
        _embed_locks[key] = (lock, waiters + 1)
    try:
        with lock:
            yield
    finally:
        with _embed_locks_guard:
            lock, waiters = _embed_locks[key]
            if waiters > 1:
                _embed_locks[key] = (lock, waiters - 1)
            else:
                del _embed_locks[key]


def rebuild_ad_chapters(slug, episode_id, markers, episode=None) -> bool:
    """Rebuild ad chapters from `markers` without touching the audio cut.

    episode, when given, is the already-loaded episodes row. Returns True when
    the stored chapter set changed. Never raises.
    """
    try:
        if episode is None:
            episode = db.get_episode(slug, episode_id)
        if not episode or episode.get('status') != EpisodeStatus.PROCESSED.value:
            return False
        chapters_json = (storage.get_chapters_json(slug, episode_id)
                         or {'version': '1.2.0', 'chapters': []})
        current = chapters_json.get('chapters') or []
        has_ad_entries = len(strip_ad_chapters(current)) != len(current)
        # A keep marker could refresh to mark below if the feed's action for
        # its category changed since this marker was stamped.
        could_add = any(is_keep_like(m.get('action_applied')) or is_pending_review(m)
                        for m in markers or [])
        # Nothing stored to clear and no marker that could produce an entry:
        # skip the podcast row and settings reads entirely.
        if not has_ad_entries and not could_add:
            return False
        ad_config = resolve_ad_chapter_config(db, db.get_podcast_by_slug(slug))
        # Disabled with nothing to strip: no file probe, no write.
        if ad_config is None and not has_ad_entries:
            return False
        # Not persisted: refreshed only for this rebuild's chapters, not
        # written back to ad_markers_json.
        markers = refresh_keep_like_markers(markers, ad_config.actions) if ad_config else markers
        cuts = storage.get_applied_cuts(slug, episode_id)
        if cuts is None:
            # Unknown cut list, not an empty one: ad spans would be placed at
            # original-episode offsets. Leave the stored set alone.
            audio_logger.info(
                f"[{slug}:{episode_id}] Ad chapter rebuild skipped: no "
                f"authoritative applied cuts persisted")
            return False
        path = storage.get_episode_path(slug, episode_id,
                                        version=episode.get('processed_version'))
        exists = path.exists()
        duration = episode.get('new_duration')
        if duration is None and exists:
            duration = get_audio_duration(str(path))
        merged = merge_ad_chapters(current, markers, cuts, duration,
                                   get_replacement_duration(), ad_config)
        if merged == current:
            return False

        with _episode_embed_lock((slug, episode_id)):
            # Embed first: a failed embed must leave the served JSON matching
            # the ID3 already in the file.
            if exists and not embed_chapters(str(path), public_chapters(merged, for_embedding=True),
                                             duration=duration):
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Ad chapter embed failed; "
                    f"keeping previous chapters")
                return False
            storage.save_chapters_json(slug, episode_id,
                                       {**chapters_json, 'chapters': merged})
            common._refresh_rss_for_slug(slug, episode_id)
            audio_logger.info(
                f"[{slug}:{episode_id}] Rebuilt ad chapters "
                f"({len(merged)} entries, no AI call)")
            return True
    except Exception as e:
        audio_logger.warning(
            f"[{slug}:{episode_id}] Ad chapter rebuild failed: {e}")
        return False


def _generate_assets(slug, episode_id, segments, all_cuts, episode_description,
                      podcast_name, episode_title, regenerate_chapters=True,
                      audio_path=None, audio_duration=None,
                      previous_cuts=None, original_duration=None,
                      podcast_row=None, run_stats=None, markers=None):
    """Pipeline stage: Generate VTT transcript and chapters.

    regenerate_chapters=False skips the chapter step, whose topic-boundary
    detection is the one LLM call here. Recut uses it to stay AI-free; the
    existing chapters are kept but remapped onto the new cut list (pure
    arithmetic via _remap_stored_chapters, using previous_cuts and
    original_duration) and can still be refreshed with the manual Regenerate
    Chapters action.

    audio_path, when given, is the final processed MP3; generated chapters
    are also embedded into it as ID3v2 frames for players that ignore the
    podcast:chapters JSON (issue #523). audio_duration is its duration in
    seconds, saving the embed a re-probe when the caller already knows it.

    podcast_row, when given, is the already-fetched podcasts row (the main
    pipeline fetches it once for resolve_feed_processing_mode); passing it
    avoids a second get_podcast_by_slug aggregate query just to resolve the
    chapters mode and the ad-chapter config. None (e.g. the recut call site)
    falls back to fetching it here.

    original_duration, when given, also gates and feeds the 'auto'-mode
    upstream podcast:chapters JSON fetch (issue #560 follow-up): with it
    unset, that fetch is skipped and an embedded-chapter shortfall falls
    straight through to generation, same as before this source existed.

    run_stats, when given, is the caller's run_stats dict; if chapter
    generation degrades to a fallback set, chapters_degraded and
    chapters_degraded_reason are recorded there.

    markers, when given, is the ad/segment marker list used to build
    topic-boundary hints for the generator's prompt (see chapters_generator.
    build_segment_hints, AI-generation branch only) and to rebuild the ad
    chapters on every chapter-producing path.
    """
    from transcript_generator import TranscriptGenerator
    from chapters_generator import ChaptersGenerator
    pending_assets = {}
    publish_assets = True
    preserving_source = False
    try:
        vtt_enabled = db.get_setting('vtt_transcripts_enabled')
        transcript_gen = TranscriptGenerator()

        # Each cut is replaced by the beep, so post-cut timestamps shift by
        # (cut - beep) per cut, not the full cut length.
        replacement_duration = get_replacement_duration()

        # Successful asset publication includes final segments even when VTT is disabled.
        final_segments = transcript_gen.compute_final_segments(segments, all_cuts, replacement_duration)
        pending_assets['final_segments'] = final_segments

        if vtt_enabled is None or vtt_enabled.lower() == 'true':
            vtt_content = transcript_gen.generate_vtt(segments, all_cuts, replacement_duration)
            if vtt_content and len(vtt_content) > 10:
                pending_assets['transcript_vtt'] = vtt_content
                audio_logger.info(f"[{slug}:{episode_id}] Generated VTT transcript")

        processed_text = transcript_gen.generate_text(segments, all_cuts, replacement_duration)
        if processed_text:
            pending_assets['transcript_text'] = processed_text

        chapters_enabled = db.get_setting('chapters_enabled')
        if not regenerate_chapters:
            audio_logger.info(f"[{slug}:{episode_id}] Skipping chapter regeneration (no AI call)")
            _remap_stored_chapters(slug, episode_id, all_cuts,
                                   replacement_duration, previous_cuts,
                                   original_duration,
                                   audio_path=audio_path,
                                   audio_duration=audio_duration,
                                   markers=markers, podcast_row=podcast_row, pending_assets=pending_assets)
        elif chapters_enabled is None or chapters_enabled.lower() == 'true':
            if podcast_row is None:
                podcast_row = db.get_podcast_by_slug(slug)
            chapters_mode = resolve_chapters_mode(podcast_row, db=db)
            if chapters_mode == CHAPTERS_MODE_OFF:
                audio_logger.info(f"[{slug}:{episode_id}] Chapters mode 'off'; skipping chapter step")
                return
            ad_config = resolve_ad_chapter_config(db, podcast_row)
            # 'auto' probes the PROCESSED file: the ffmpeg cut step already
            # remapped publisher ID3 CHAP frames onto the cut timeline
            # (audio_processor.py), so a probe here gives the remapped list
            # for free with no extra work. 'generate' never probes, so it
            # always falls through to the generator below regardless of what
            # publisher chapters exist.
            publisher = []
            if chapters_mode == CHAPTERS_MODE_AUTO and audio_path:
                publisher = probe_chapters(str(audio_path))
                if publisher is None:
                    # Probe failed (e.g. transient ffprobe error), not "the
                    # file definitively has no chapters" (embedded_chapters.
                    # probe_chapters). Falling through to the generate+embed
                    # path below would overwrite the ID3 frames the cut step
                    # already wrote correctly, the exact failure mode issue
                    # #500 prevents at the ffmpeg layer. Skip the chapter
                    # step this run instead of guessing.
                    audio_logger.warning(
                        f"[{slug}:{episode_id}] Chapter probe failed after "
                        f"cut; skipping chapter step this run")
                    return
            preserving_source = any(ch.get('_id3_id') for ch in publisher)
            if len(publisher) >= MIN_PRESERVED_CHAPTERS or preserving_source:
                entries = _publisher_chapter_entries(publisher)
                chapters_json = {'version': '1.2.0', 'chapters': entries}
                if preserving_source:
                    chapters_json[ID3_CHAPTER_SOURCE_KEY] = 'id3'
                base = chapters_json['chapters']
                merged = merge_ad_chapters(base, markers, all_cuts or [],
                                           audio_duration, replacement_duration,
                                           ad_config)
                # The cut step already embedded the publisher frames, so the
                # re-embed only runs when ad entries were added on top.
                _publish_chapters(
                    slug, episode_id, chapters_json, merged, all_cuts,
                    audio_path, audio_duration, merged != base,
                    f"Preserved {len(publisher)} publisher chapter(s) (no AI call)", pending_assets=pending_assets)
                return
            # Embedded chapters came up short. Some feeds publish chapters
            # only as a separate podcast:chapters JSON file (issue #560
            # follow-up), captured at RSS refresh as
            # episodes.upstream_chapters_url; try fetching that next, before
            # falling back to generation. The probe above must not risk
            # overwriting correctly-embedded ID3 frames on a failed read, so
            # it skips the run instead. A fetch failure here (None) has no
            # such risk, so it is deliberately let through to the generator: a
            # bad or unreachable remote file must not block chapters
            # outright, only lose the chance to preserve the publisher's own
            # set.
            if chapters_mode == CHAPTERS_MODE_AUTO and original_duration:
                episode_row = db.get_episode(slug, episode_id)
                upstream_url = (episode_row or {}).get('upstream_chapters_url')
                if upstream_url:
                    fetched = fetch_upstream_chapters(upstream_url, user_agent=download_user_agent(podcast_row))
                    if fetched is not None:
                        remapped = _remap_chapters_for_recut(
                            fetched, [], all_cuts or [], replacement_duration,
                            original_duration, audio_duration)
                        if len(remapped) >= MIN_PRESERVED_CHAPTERS:
                            chapters_json = {
                                'version': '1.2.0',
                                'chapters': [
                                    {**ch, 'title': ch.get('title') or f"Chapter {i + 1}"}
                                    for i, ch in enumerate(remapped)
                                ],
                            }
                            merged = merge_ad_chapters(
                                chapters_json['chapters'], markers,
                                all_cuts or [], audio_duration,
                                replacement_duration, ad_config)
                            # Unlike the embedded-preserve path above, the
                            # served file has no chapter frames yet, so this
                            # always embeds.
                            _publish_chapters(
                                slug, episode_id, chapters_json, merged,
                                all_cuts, audio_path, audio_duration, True,
                                f"Preserved {len(remapped)} upstream JSON "
                                f"chapter(s) (no AI call)", pending_assets=pending_assets)
                            return
            chapters_gen = None
            chapter_setup_failed = False
            with common._measure_run_stage('chapters'):
                try:
                    chapters_gen = ChaptersGenerator()
                    clear_fallback(episode_id, PASS_CHAPTER_GENERATION)
                    chapters = chapters_gen.generate_chapters(
                        segments,
                        episode_description=episode_description,
                        ads_removed=all_cuts,
                        podcast_name=podcast_name,
                        episode_title=episode_title,
                        episode_id=episode_id,
                        replacement_duration=replacement_duration,
                        segment_markers=markers,
                        slug=slug,
                    )
                except ProviderRateLimitedError as e:
                    # The audio is already cut, so hold the queue and publish ad
                    # chapters only rather than failing the run.
                    hold_until = None
                    try:
                        hold_until = hold_queue_for_provider_limit(
                            db, e, slug=slug, episode_id=episode_id,
                            podcast_name=podcast_name, phase='chapters')
                    except Exception:
                        audio_logger.exception(
                            f"[{slug}:{episode_id}] Failed to record the rate-limit hold")
                    chapters = None
                    chapters_gen.chapters_degraded = True
                    chapters_gen.chapters_degradation_reason = hold_message(hold_until, e)
                except (ProviderAccountChangedError, ProcessingCancelled,
                        ProcessingOwnershipLost):
                    raise
                except Exception:
                    audio_logger.exception(
                        f"[{slug}:{episode_id}] Chapter generation setup failed")
                    chapters = None
                    chapter_setup_failed = True
            chapters_degraded = chapter_setup_failed or bool(
                chapters_gen and chapters_gen.chapters_degraded)
            chapters_degraded_reason = (
                'Chapter generation failed. Check Settings > AI Models and try again.'
                if chapter_setup_failed else
                chapters_gen.chapters_degradation_reason if chapters_gen else None)
            if run_stats is not None and chapters_degraded:
                run_stats['chapters_degraded'] = True
                run_stats['chapters_degraded_reason'] = chapters_degraded_reason
            topic = (chapters or {}).get('chapters') or []
            merged = merge_ad_chapters(topic, markers, all_cuts or [],
                                       audio_duration, replacement_duration,
                                       ad_config)
            if merged:
                _publish_chapters(
                    slug, episode_id, chapters or {'version': '1.2.0'}, merged,
                    all_cuts, audio_path, audio_duration, True,
                    f"Generated {len(topic)} chapters", pending_assets=pending_assets)
    except (ProviderAccountChangedError, ProcessingCancelled, ProcessingOwnershipLost, ChapterTagError):
        publish_assets = False
        raise
    except Exception as e:
        if preserving_source:
            publish_assets = False
            raise ChapterTagError('Publisher chapter assets could not be prepared') from e
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to generate Podcasting 2.0 assets: {e}")

    finally:
        if publish_assets and pending_assets:
            for chapter in pending_assets.get('chapters', {}).get('chapters', []):
                image = chapter.pop('_id3_image', None)
                if image is not None:
                    image_url = storage.save_chapter_image(slug, episode_id, image)
                    if image_url:
                        chapter['img'] = image_url
            db.save_processing_assets(slug, episode_id, pending_assets)

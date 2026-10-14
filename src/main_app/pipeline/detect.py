"""Pass 1 ad detection, boundary refinement, validation and confidence gating."""

import logging
from ad_detector import (
    refine_ad_boundaries, snap_early_ads_to_zero, merge_same_sponsor_ads,
    merge_ads_across_short_content_gaps,
    extend_ad_boundaries_by_content,
)

from ad_detector.cue_boundary_snap import snap_ad_boundaries_to_cues
from ad_detector.cue_pair_ads import synthesize_ads_from_cue_pairs
from ad_detector.cue_telemetry import build_cue_detection_records
from ad_detector.silence_boundary_snap import snap_ad_boundaries_to_silence
from audio_analysis.cue_threshold_suggest import near_miss_streak_suggestion
from config import (
    resolve_max_boundary_shift,
    MIN_CONTENT_BETWEEN_ADS_SECONDS,
    AUDIO_CUE_PAIR_CONFIDENCE, AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS,
    AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES,
    HOLD_REASON_NO_CUE,
    is_cue_backed, resolve_feed_cue_settings,
    resolve_silence_snap_tunables,
    resolve_max_ad_duration,
    resolve_max_ad_duration_confirmed,
    VETO_MIN_CUT_SECONDS,
    resolve_splice_veto_enabled,
    ModelNotConfiguredError,
    coerce_bool_setting,
)

from database.settings import registry_get_default
from llm_capabilities import (
    PASS_AD_DETECTION_1, clear_fallback,
)

from llm_client import (
    is_auth_error, LimitExceededError,
    ProviderRateLimitedError,
    ProviderRequestRejectedError,
)

from utils.errors import ServiceUnavailableError

from webhook_service import fire_cue_template_quiet_event

from main_app.pipeline import common
from main_app.pipeline.shared import (db, storage, ad_detector, sponsor_service)

audio_logger = logging.getLogger('podcast.audio')


def _exclude_opening_ads(ads, seconds):
    """Drop markers that begin inside the configured opening window."""
    return [ad for ad in ads if float(ad.get('start', 0)) >= seconds] if seconds > 0 else ads


def _detect_ads_first_pass(ctx, segments, audio_path,
                            skip_patterns, audio_analysis_result,
                            progress_callback, cancel_event=None,
                            positional_prior_hint="", dai_differential=None,
                            keep_content=None, skip_llm=False,
                            force_create_from_pairs=False,
                            strict_pair_roles=False, episode_duration=0.0,
                            run_stats=None, recurrence_spans=None,
                            action_map=None, transcript_spans=None):
    """Pipeline stage: Run first-pass Claude ad detection.

    ``keep_content``: None lets the detector resolve the per-feed mode from
    the DB at detection time (the pipeline passes None so a detection_mode
    toggle during the minutes-long download/transcription window is
    honored); True/False forces the mode without a DB read.
    ``skip_llm``/``force_create_from_pairs``/``strict_pair_roles``: cue_only
    preset plumbing. ``episode_duration`` backstops the cue-pair fraction
    guard when transcription (and its segment list) is skipped.
    ``run_stats``: caller's run_stats dict; stamped with detection_degraded
    when pass 1 fails but publishes on pattern/cross-fetch markers alone.
    ``recurrence_spans``: optional cross-episode text-recurrence spans
    (hushpod adoption); rendered per-window, pass-1 only.
    ``action_map``: the run's resolved category actions, shared with detection.
    ``transcript_spans``: upstream transcript gaps for detector stage 2.6.

    Returns (first_pass_ads, first_pass_count, ad_result).
    """
    slug = ctx.slug
    episode_id = ctx.episode_id
    common._publish_status('update_job_stage', slug, episode_id, "pass1:detecting", 50)
    clear_fallback(episode_id, PASS_AD_DETECTION_1)

    ad_result = ad_detector.process_transcript(
        segments,
        audio_path=audio_path,
        skip_patterns=skip_patterns,
        progress_callback=progress_callback,
        audio_analysis=audio_analysis_result,
        dai_differential=dai_differential,
        cancel_event=cancel_event,
        ctx=ctx,
        positional_prior_hint=positional_prior_hint,
        recurrence_spans=recurrence_spans,
        keep_content=keep_content,
        skip_llm=skip_llm,
        action_map=action_map,
        transcript_spans=transcript_spans,
    )
    storage.save_ads_json(slug, episode_id, ad_result, pass_number=1)

    ad_detection_status = ad_result.get('status', 'success')
    first_pass_ads = ad_result.get('ads', [])

    if ad_detection_status == 'failed':
        error_msg = ad_result.get('error', 'Unknown error')
        audio_logger.error(f"[{slug}:{episode_id}] Ad detection failed: {error_msg}")
        # Each typed re-raise below marks the stage failed first; the type is
        # what _handle_processing_failure routes on.
        typed = None
        if ad_result.get('rate_limited_hold'):
            # Held 429 (#696): defers the episode and pauses the queue until
            # the provider's reset.
            typed = ProviderRateLimitedError(
                f"Ad detection failed: {error_msg}",
                retry_after_seconds=float(ad_result.get('retry_after_seconds') or 0),
                provider_key=ad_result.get('provider_key'),
                credential_slot=ad_result.get('credential_slot'),
                manual=ad_result.get('manual', False),
                phase='detection',
            )
        elif ad_result.get('connectivity'):
            # Endpoint unreachable rather than a bad response, so the offline
            # queue (#482) can defer instead of failing the episode.
            typed = ServiceUnavailableError('llm', f"Ad detection failed: {error_msg}")
        elif ad_result.get('limit_exceeded'):
            # Terminal limit error, not the stringified 429 text that
            # re-classifies as transient (#491).
            typed = LimitExceededError(f"Ad detection failed: {error_msg}")
        elif ad_result.get('model_not_configured'):
            # A bare Exception would default to transient and burn the retry
            # ladder. error_msg is already the exact resolver message.
            typed = ModelNotConfiguredError('claude_model', error_msg)
        elif ad_result.get('provider_rejected'):
            typed = ProviderRequestRejectedError(
                f"Ad detection failed: {error_msg}",
                status_code=ad_result.get('last_error_status'),
            )
        if typed is not None:
            db.upsert_episode(slug, episode_id, ad_detection_status='failed')
            raise typed
        # Degraded continue: a transient, non-auth failure that still left
        # pattern/cross-fetch markers publishes those instead of failing the
        # episode. Auth-class failures and zero markers still raise.
        # A partial window failure (some windows answered) is excluded: the
        # provider is working well enough that a retry will likely complete,
        # so publishing pattern-only cuts permanently would discard the LLM
        # markers the successful windows found. Only a total outage degrades.
        windows_total = ad_result.get('windows_total') or 0
        windows_failed = ad_result.get('windows_failed') or 0
        partial_window_failure = 0 < windows_failed < windows_total
        classification_error = Exception(error_msg)
        if (first_pass_ads and not partial_window_failure
                and common.is_transient_error(classification_error)
                and not is_auth_error(classification_error)):
            sanitized = ' '.join(error_msg.split())[:300]
            audio_logger.warning(
                f"[{slug}:{episode_id}] Ad detection degraded: publishing "
                f"{len(first_pass_ads)} pattern/cross-fetch marker(s) ({sanitized})")
            db.upsert_episode(slug, episode_id, ad_detection_status='failed',
                              detection_degraded=sanitized)
            if run_stats is not None:
                run_stats['detection_degraded'] = sanitized
            return first_pass_ads, len(first_pass_ads), ad_result
        db.upsert_episode(slug, episode_id, ad_detection_status='failed')
        raise Exception(f"Ad detection failed: {error_msg}")

    db.upsert_episode(slug, episode_id, ad_detection_status='success')

    if first_pass_ads:
        total_ad_time = sum(ad['end'] - ad['start'] for ad in first_pass_ads)
        audio_logger.info(f"[{slug}:{episode_id}] First pass: Detected {len(first_pass_ads)} ads ({total_ad_time/60:.1f} min)")
    else:
        audio_logger.info(f"[{slug}:{episode_id}] First pass: No ads detected")

    # Resolve per-feed cue knobs (one DB read; falls back to global then default).
    podcast_id = ctx.podcast_id
    cue_settings = resolve_feed_cue_settings(db, podcast_id)
    snap_confidence = cue_settings['snap_confidence']
    snap_lead = cue_settings['snap_lead']
    snap_lag = cue_settings['snap_lag']
    allow_transition = cue_settings['transition_snap_enabled']

    # Cue-pair ad synthesis (opt-in): when the LLM missed a break that the cue
    # matcher bracketed with two high-confidence cues, materialize a synthetic
    # ad spanning the pair so downstream cuts include the missed break. Off by
    # default because it breaks the "cue is supporting evidence only" contract;
    # enable per the audio_cue_create_from_pairs setting once the matcher is
    # trusted. The reviewer still evaluates every synthesized ad (issue #350).
    cue_pair_skip_diagnostics = {}
    if audio_analysis_result and (cue_settings['create_from_pairs'] or force_create_from_pairs):
        try:
            updated, cue_pair_skip_diagnostics = synthesize_ads_from_cue_pairs(
                first_pass_ads, audio_analysis_result,
                min_confidence=db.get_setting_float('audio_cue_pair_confidence', AUDIO_CUE_PAIR_CONFIDENCE),
                min_break_s=cue_settings['pair_min_break'],
                max_break_s=cue_settings['pair_max_break'],
                total_duration=(episode_duration if episode_duration > 0
                                else segments[-1]['end'] if segments else 0.0),
                max_break_fraction=cue_settings['pair_max_break_fraction'],
                orient_window_s=db.get_setting_float('audio_cue_pair_orient_window_seconds', AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS),
                strict_roles=strict_pair_roles,
            )
            added = len(updated) - len(first_pass_ads)
            if added:
                audio_logger.info(
                    f"[{slug}:{episode_id}] Cue pair: synthesised {added} ad(s) "
                    f"from unmatched cue pairs"
                )
            first_pass_ads = updated
        except Exception as e:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Cue pair synthesis skipped: {e}"
            )

    # Snap both ad edges to nearby audio cues when the analyzer flagged any.
    # Capped by the reviewer's max_boundary_shift setting so a misfiring cue
    # cannot warp the boundary beyond what the user has authorised. Implicitly
    # gated: there are no audio_cue signals unless cue detection ran, so this
    # is a no-op when the master toggle is off (issue #350).
    # Edge snapshot before snap, for telemetry edge distances.
    pre_snap_ads = ([{'start': a.get('start'), 'end': a.get('end')} for a in first_pass_ads]
                    if audio_analysis_result else [])
    if first_pass_ads and audio_analysis_result:
        try:
            max_shift = resolve_max_boundary_shift(db)
            # Mutates first_pass_ads in place (edges + cue_snap metadata).
            snap_ad_boundaries_to_cues(
                first_pass_ads, audio_analysis_result, max_shift,
                min_confidence=snap_confidence,
                snap_lead_s=snap_lead,
                snap_lag_s=snap_lag,
                allow_transition=allow_transition,
            )
        except Exception as e:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Cue boundary snap skipped: {e}"
            )

    # Snap ad edges to nearby silence spans (per-feed opt-in, Phase B task B3).
    # Duration-only by design; ignores compute_applied_cuts trust exceptions.
    silence_spans = audio_analysis_result.silence_spans if audio_analysis_result else []
    if first_pass_ads and silence_spans:
        try:
            # Use tunables the analyzer already resolved; fall back only when
            # audio_analysis_result is absent (defensive, not a normal path).
            cached = getattr(audio_analysis_result, 'silence_tunables', None)
            silence_tunables = cached if cached is not None else resolve_silence_snap_tunables(db)
            snap_ad_boundaries_to_silence(
                first_pass_ads, silence_spans,
                max_distance_s=silence_tunables['max_distance_seconds'],
                min_silence_s=silence_tunables['min_duration_seconds'],
            )
        except Exception as e:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Silence boundary snap skipped: {e}"
            )

    # Record per-cue detection telemetry (advisory only; measures pre-snap edges so distances reflect original LLM boundaries).
    if audio_analysis_result and podcast_id:
        try:
            records = build_cue_detection_records(
                first_pass_ads, audio_analysis_result,
                pre_snap_ads=pre_snap_ads,
                pair_skip_diagnostics=cue_pair_skip_diagnostics,
                snap_confidence=snap_confidence,
                snap_lead_s=snap_lead,
                snap_lag_s=snap_lag,
            )
            db.record_cue_detections(podcast_id, episode_id, records)
        except Exception as e:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Cue detection telemetry skipped: {e}"
            )

    return first_pass_ads, len(first_pass_ads), ad_result


def _suggest_cue_thresholds(slug, episode_id, podcast_id, audio_analysis_result):
    """Warn when a template keeps missing by a hair, naming the threshold that would
    work. Returns {template_id: suggested}; advisory, nothing is applied."""
    silent = [tpl for tpl in
              getattr(audio_analysis_result, 'cue_templates_debug', None) or []
              if not tpl.get('match_count')]
    if not silent:
        return {}
    try:
        peaks = db.cue_template_episode_peaks(
            podcast_id, [tpl['id'] for tpl in silent],
            AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES)
    except Exception as e:
        audio_logger.warning(
            f"[{slug}:{episode_id}] Cue threshold suggestion skipped: {e}")
        return {}
    suggestions = {}
    for tpl in silent:
        episode_peaks = peaks.get(tpl['id']) or []
        suggested = near_miss_streak_suggestion(
            tpl.get('eff_threshold'), episode_peaks)
        if suggested is None:
            continue
        suggestions[tpl['id']] = suggested
        scores = ", ".join(
            f"{best:.3f}"
            for best, _ in episode_peaks[:AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES])
        audio_logger.warning(
            f"[{slug}:{episode_id}] Cue template {tpl['id']} ({tpl.get('label')!r}) "
            f"peaked at {scores} across the last "
            f"{AUDIO_CUE_SUGGEST_NEAR_MISS_EPISODES} episodes without ever "
            f"clearing its {tpl['eff_threshold']:.3f} threshold; a threshold of "
            f"{suggested:.2f} would have matched all of them")
    return suggestions


def _window_stats(total, failed, losses=None):
    """Per-pass window coverage, plus what the lost windows were lost to."""
    stats = {'total': total, 'failed': failed or 0}
    if losses:
        stats['failureClasses'] = dict(losses)
    return stats


def _quiet_templates_to_notify(activity, enabled_template_ids):
    """Quiet cue-template activity rows whose template is currently enabled."""
    return [a for a in activity if a['quiet'] and a['templateId'] in enabled_template_ids]


def _notify_quiet_cue_templates(slug, podcast_name, podcast_id, cue_templates,
                                threshold_suggestions=None):
    """Fire Cue Template Quiet for each enabled template gone quiet on this feed.

    ``threshold_suggestions`` maps template id to the threshold its near-miss
    streak would have needed, so the alert says what to change.
    Best-effort (issue #599): a notification failure must not break the run.
    """
    try:
        activity = db.cue_template_recent_activity(podcast_id)
        templates = {t['id']: t for t in cue_templates}
        enabled_ids = {tid for tid, t in templates.items() if t.get('enabled')}
        for a in _quiet_templates_to_notify(activity, enabled_ids):
            fire_cue_template_quiet_event(
                slug, podcast_name, a['templateId'],
                templates.get(a['templateId'], {}).get('label'), a['lastMatchAt'],
                suggested_threshold=(threshold_suggestions or {}).get(
                    a['templateId']))
    except Exception as e:
        audio_logger.warning(f"[{slug}] Cue template quiet check skipped: {e}")


def _vad_gap_enabled(db) -> bool:
    """Read the vad_gap_detection_enabled setting (default True)."""
    value = db.get_setting('vad_gap_detection_enabled')
    if value is None:
        return True
    return str(value).strip().lower() != 'false'


def _refine_boundaries(all_ads, segments, db=None, false_positive_corrections=None,
                       podcast_name=None, keep_ads=None):
    """Apply the boundary refinement pipeline. Returns updated list.

    ``false_positive_corrections`` are threaded to the filler-gap merge so it
    never collapses a span the user rejected (merging would dilute the
    validator's overlap ratio). ``podcast_name`` keeps the host's own site out
    of the harvested sponsor tokens. ``keep_ads`` (partitioned out of
    ``all_ads`` upstream) still act as extension barriers: a cut must not
    grow into a span the feed keeps.
    """
    if all_ads and segments:
        all_ads = refine_ad_boundaries(all_ads, segments)
    if all_ads and segments:
        all_ads = extend_ad_boundaries_by_content(all_ads, segments,
                                                  podcast_name=podcast_name,
                                                  barriers=all_ads + list(keep_ads or []))
    if all_ads:
        all_ads = snap_early_ads_to_zero(all_ads)
    min_content = common._setting_float(db, 'min_content_between_ads_seconds',
                                 MIN_CONTENT_BETWEEN_ADS_SECONDS,
                                 allow_zero=True) if db else MIN_CONTENT_BETWEEN_ADS_SECONDS
    if all_ads and segments:
        # Same threshold as the filler-gap pass below: one operator setting
        # decides how much speech makes a gap real show content.
        all_ads = merge_same_sponsor_ads(all_ads, segments,
                                         podcast_name=podcast_name,
                                         min_content_seconds=min_content)
    if all_ads:
        all_ads = merge_ads_across_short_content_gaps(
            all_ads, segments or [],
            min_content_seconds=min_content,
            false_positive_corrections=false_positive_corrections,
        )
    return all_ads


def _apply_heuristic_rolls(slug, episode_id, all_ads, segments, podcast_name,
                            episode_duration, skip_patterns, db):
    """Append heuristic pre/post-roll and VAD-gap ads to ``all_ads`` in place."""
    if not segments:
        return
    from roll_detector import detect_preroll, detect_postroll
    preroll_ad = detect_preroll(segments, all_ads, podcast_name=podcast_name,
                                skip_patterns=skip_patterns)
    if preroll_ad:
        all_ads.append(preroll_ad)
        audio_logger.info(f"[{slug}:{episode_id}] Heuristic pre-roll: 0.0s-{preroll_ad['end']:.1f}s")

    postroll_ad = detect_postroll(segments, all_ads, episode_duration=episode_duration, skip_patterns=skip_patterns)
    if postroll_ad:
        all_ads.append(postroll_ad)
        audio_logger.info(f"[{slug}:{episode_id}] Heuristic post-roll: {postroll_ad['start']:.1f}s-{postroll_ad['end']:.1f}s")

    # VAD-gap detection (head/mid/tail)
    if _vad_gap_enabled(db):
        from vad_gap_detector import detect_vad_gaps
        vad_gap_ads = detect_vad_gaps(
            segments, all_ads, episode_duration,
            start_min_seconds=common._setting_float(db, 'vad_gap_start_min_seconds', 3.0),
            mid_min_seconds=common._setting_float(db, 'vad_gap_mid_min_seconds', 8.0),
            tail_min_seconds=common._setting_float(db, 'vad_gap_tail_min_seconds', 3.0),
        )
        if vad_gap_ads:
            all_ads.extend(vad_gap_ads)
            audio_logger.info(
                f"[{slug}:{episode_id}] VAD gap detector: {len(vad_gap_ads)} gap(s) marked"
            )


def _load_user_corrections(slug, episode_id, db):
    """Load FP and confirmed corrections for the episode and log counts."""
    podcast = db.get_podcast_by_slug(slug)
    podcast_id = podcast['id'] if podcast else None
    false_positive_corrections = db.get_false_positive_corrections(
        podcast_id, episode_id) if podcast_id is not None else []
    if false_positive_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(false_positive_corrections)} false positive corrections")

    confirmed_corrections = db.get_confirmed_corrections(
        podcast_id, episode_id) if podcast_id is not None else []
    if confirmed_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(confirmed_corrections)} confirmed corrections")

    return false_positive_corrections, confirmed_corrections


def _gate_validation_by_confidence(slug, episode_id, validation_ads, min_cut_confidence,
                                    cue_gate_enabled=False):
    """Apply ACCEPT/REJECT/REVIEW confidence gating. Returns (ads_to_remove, low_confidence_count)."""
    ads_to_remove = []
    low_confidence_count = 0
    for ad in validation_ads:
        validation = ad.get('validation', {})
        decision = validation.get('decision')
        if decision == 'REJECT':
            ad['was_cut'] = False
            continue
        if decision == 'ACCEPT':
            ad['was_cut'] = True
            ads_to_remove.append(ad)
            continue
        # Held ads are high-confidence REVIEWs; they must never be cut.
        if ad.get('held_for_review'):
            ad['was_cut'] = False
            audio_logger.info(
                f"[{slug}:{episode_id}] Holding ad for review "
                f"({ad.get('hold_reason', 'unknown')}): "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s"
            )
            continue
        confidence = validation.get('adjusted_confidence', ad.get('confidence', 1.0))
        if confidence < min_cut_confidence:
            low_confidence_count += 1
            ad['was_cut'] = False
            audio_logger.info(
                f"[{slug}:{episode_id}] Keeping REVIEW ad in audio: "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s ({confidence:.0%} < {min_cut_confidence:.0%})"
            )
            continue
        # REVIEW fall-through guard: the validator's rounding keeps a REVIEW ad
        # below threshold here, so this is belt-and-suspenders for any REVIEW ad
        # that reaches the gate at/above threshold (e.g. non-validator inputs) --
        # on a cue-gated feed hold it instead of cutting when it has no cue.
        if cue_gate_enabled and not is_cue_backed(ad):
            ad['was_cut'] = False
            ad['held_for_review'] = True
            ad['hold_reason'] = HOLD_REASON_NO_CUE
            audio_logger.info(
                f"[{slug}:{episode_id}] Holding REVIEW ad (no cue evidence): "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s"
            )
            continue
        ad['was_cut'] = True
        ads_to_remove.append(ad)
    return ads_to_remove, low_confidence_count


def _build_validator(episode_duration, segments, episode_description, *,
                     false_positive_corrections, min_cut_confidence,
                     max_ad_duration_override, cue_gate_enabled,
                     confirmed_corrections=None, positional_prior=None,
                     splice_veto=True, podcast_id=None, podcast_name=None,
                     cue_only_safety=None, cue_unproven_template_ids=None,
                     transcript_spans=None):
    """Single construction point for AdValidator; owns the splice-veto
    settings reads. Per-site differences are stated by the callers:

    - pass 1 passes positional_prior (original-timeline zones);
    - pass 2 sets splice_veto=False because there is no audio analysis in
      pass 2, so the veto could never corroborate a splice, and omits
      positional_prior/confirmed_corrections (processed-audio coordinates);
    - recut passes everything except positional_prior.
    """
    from ad_validator import AdValidator
    max_ad_duration = resolve_max_ad_duration(db, podcast_id)
    max_ad_duration_confirmed = resolve_max_ad_duration_confirmed(db)
    splice_kwargs = {}
    if splice_veto:
        splice_kwargs = {
            'splice_veto_enabled': resolve_splice_veto_enabled(
                db, podcast_id,
                db.get_setting_bool('splice_veto_enabled', default=coerce_bool_setting(
                    registry_get_default('splice_veto_enabled')))),
            'veto_min_cut_seconds': db.get_setting_float('veto_min_cut_seconds',
                                                         VETO_MIN_CUT_SECONDS),
        }
    return AdValidator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence,
        positional_prior=positional_prior,
        max_ad_duration_override=max_ad_duration_override,
        cue_gate_enabled=cue_gate_enabled,
        differential_corr_max=db.get_setting_float(
            'differential_measured_corr_max',
            registry_get_default('differential_measured_corr_max')),
        sponsor_service=sponsor_service,
        max_ad_duration=max_ad_duration,
        max_ad_duration_confirmed=max_ad_duration_confirmed,
        cue_only_safety=cue_only_safety,
        cue_unproven_template_ids=cue_unproven_template_ids,
        podcast_name=podcast_name,
        transcript_spans=transcript_spans,
        **splice_kwargs,
    )


def _refine_and_validate(slug, episode_id, all_ads, segments, audio_path,
                          episode_description, episode_duration, min_cut_confidence,
                          podcast_name, skip_patterns=False, positional_prior=None,
                          max_ad_duration_override=None, cue_gate_enabled=False,
                          audio_analysis=None, podcast_id=None, keep_ads=None,
                          cue_only_safety=None, cue_unproven_template_ids=None,
                          apply_heuristic_rolls=True, segment_actions=None, *,
                          corrections, transcript_spans=None):
    """Pipeline stage: Refine ad boundaries, detect rolls, validate, gate by confidence.

    ``keep_ads`` are the keep-partitioned markers, passed so boundary
    extension treats them as barriers even though they left ``all_ads``.
    ``cue_only_safety``/``cue_unproven_template_ids`` pass through to the
    validator; both None outside cue_only runs. ``apply_heuristic_rolls``
    is False under cue_only, where cuts must come only from cue and
    pattern-DB evidence. ``segment_actions`` is the resolved category ->
    action map, passed to the validator's merge step so it never folds
    ads whose categories resolve to different actions.

    Returns (ads_to_remove, all_ads_with_validation).
    """
    # Load corrections first: the filler-gap merge needs the FP ranges so it
    # does not collapse a span the user rejected.
    false_positive_corrections, confirmed_corrections = corrections

    # Boundary refinement
    all_ads = _refine_boundaries(all_ads, segments, db=db,
                                 false_positive_corrections=false_positive_corrections,
                                 podcast_name=podcast_name,
                                 keep_ads=keep_ads)

    # Heuristic pre/post-roll detection
    if apply_heuristic_rolls:
        # cue_only cuts only from cue and pattern evidence.
        _apply_heuristic_rolls(slug, episode_id, all_ads, segments, podcast_name,
                                episode_duration, skip_patterns, db)

    # Validation
    if not all_ads:
        return [], []

    validator = _build_validator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence,
        positional_prior=positional_prior,
        max_ad_duration_override=max_ad_duration_override,
        cue_gate_enabled=cue_gate_enabled,
        podcast_id=podcast_id,
        podcast_name=podcast_name,
        cue_only_safety=cue_only_safety,
        cue_unproven_template_ids=cue_unproven_template_ids,
        transcript_spans=transcript_spans,
    )
    validation_result = validator.validate(
        all_ads, audio_analysis=audio_analysis, actions_map=segment_actions)

    audio_logger.info(
        f"[{slug}:{episode_id}] Validation: "
        f"{validation_result.accepted} accepted, "
        f"{validation_result.reviewed} review, "
        f"{validation_result.rejected} rejected"
    )

    # Confidence gating: ACCEPT = cut, REJECT = keep, REVIEW = threshold check
    ads_to_remove, low_confidence_count = _gate_validation_by_confidence(
        slug, episode_id, validation_result.ads, min_cut_confidence,
        cue_gate_enabled=cue_gate_enabled,
    )

    all_ads_with_validation = validation_result.ads
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

    rejected_count = validation_result.rejected
    if rejected_count > 0 or low_confidence_count > 0:
        audio_logger.info(
            f"[{slug}:{episode_id}] Kept in audio: {rejected_count} rejected, "
            f"{low_confidence_count} low-confidence (<{min_cut_confidence:.0%})"
        )

    return ads_to_remove, all_ads_with_validation

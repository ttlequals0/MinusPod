"""Ad detection using Claude API with configurable prompts and model.

Package layout:
- ``boundaries`` -- pure functions that refine/merge/dedupe detected ads
- ``prompts`` -- prompt template constants, windowing, and JSON-response parsing
- this ``__init__`` -- the ``AdDetector`` class plus re-exports of every name
  external callers (production and tests) imported from the pre-split module
"""
import logging
import time
from typing import List, Dict, Optional

from cancel import _check_cancel
from llm_client import (
    get_llm_client, get_api_key, LLMClient,
    is_retryable_error,
    get_llm_timeout, get_llm_max_retries,
    get_effective_provider, model_matches_provider,
)
from utils.llm_call import call_llm_for_window
from utils.prompt import format_sponsor_block, render_prompt
from utils.text import get_transcript_text_for_range
from utils.time import first_not_none, overlap_ratio

from config import (
    MIN_TYPICAL_AD_DURATION, MIN_SPONSOR_READ_DURATION,
    MAX_REALISTIC_SIGNAL, MIN_OVERLAP_TOLERANCE,
    MAX_AD_DURATION_WINDOW, WINDOW_SIZE_SECONDS, WINDOW_OVERLAP_SECONDS,
    PATTERN_CORRECTION_OVERLAP_THRESHOLD,
    DEFAULT_AD_DETECTION_MODEL,
    resolve_stage_tunables,
)
from llm_capabilities import PASS_AD_DETECTION_1, PASS_AD_DETECTION_2
from sponsor_service import SponsorService
from utils.constants import (
    INVALID_SPONSOR_VALUES, STRUCTURAL_FIELDS,
    SPONSOR_PRIORITY_FIELDS, SPONSOR_PATTERN_KEYWORDS,
    INVALID_SPONSOR_CAPTURE_WORDS, NON_BRAND_WORDS, NOT_AD_CLASSIFICATIONS,
    KNOWN_SHORT_BRANDS, canonical_sponsor,
)

# Re-exports: every symbol the pre-split ``ad_detector`` module exposed at
# the top level. Production code and tests do ``from ad_detector import X``
# for any of these; the package must keep that contract.
from .boundaries import (
    EARLY_AD_SNAP_THRESHOLD,
    AD_START_PHRASES,
    AD_END_PHRASES,
    _NON_BRAND_WORDS,
    refine_ad_boundaries,
    snap_early_ads_to_zero,
    extend_ad_boundaries_by_content,
    _text_has_ad_content,
    extract_sponsor_names,
    _extract_ad_keywords,
    _find_keyword_region,
    validate_ad_timestamps,
    _unpack_region,
    get_uncovered_portions,
    merge_same_sponsor_ads,
    deduplicate_window_ads,
)
from .prompts import (
    USER_PROMPT_TEMPLATE,
    WINDOW_STEP_SECONDS,
    create_windows,
    format_window_prompt,
    get_static_system_prompt,
    parse_ads_from_response,
    extract_json_ads_array,
    extract_json_object,
    _find_json_array_candidates,
)

logger = logging.getLogger('podcast.claude')


class AdDetector:
    """Detect advertisements in podcast transcripts using Claude API.

    Detection pipeline (3-stage):
    1. Audio fingerprint matching - identifies identical DAI-inserted ads
    2. Text pattern matching - identifies repeated sponsor reads via TF-IDF
    3. Claude API - analyzes remaining content for unknown ads

    The first two stages are essentially free (no API costs) and can detect
    ads that have been seen before across episodes.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_api_key()
        if not self.api_key:
            logger.warning("No LLM API key found")
        self._llm_client_override: Optional[LLMClient] = None

        # Dependency attributes. Previously these were lazy @property
        # accessors guarded by ``if self._x is None``; the lazy form gave
        # us no real benefit beyond letting tests construct a detector
        # without an on-disk DB. To preserve that test-only convenience
        # without paying the per-access function call in hot paths, we
        # initialise them to None here and have ``initialize_client``
        # (called at the start of every real detection run) build them
        # eagerly. Tests that need stubs overwrite these attributes
        # after construction.
        self.db = None
        self.audio_fingerprinter = None
        self.text_pattern_matcher = None
        self.pattern_service = None
        self.sponsor_service = None

    @property
    def _llm_client(self) -> Optional[LLMClient]:
        """Current LLM client. Reads through ``get_llm_client`` on every access
        so that provider/base-URL changes via the settings API take effect
        immediately without restarting the worker."""
        if self._llm_client_override is not None:
            return self._llm_client_override
        if not self.api_key:
            return None
        return get_llm_client()

    @_llm_client.setter
    def _llm_client(self, value: Optional[LLMClient]) -> None:
        self._llm_client_override = value

    def _ensure_deps(self):
        """Build the dependency objects (db, fingerprinter, matchers,
        services) once, on demand. Called by initialize_client() at the
        top of every real detection run, and by the low-level getters
        (get_model / get_system_prompt / etc.) so that callers reaching
        in through the API still see real DB values rather than the
        try/except fallback path.

        Construction is one-shot: subsequent calls are a no-op once
        self.db is non-None. Tests that need stubs overwrite the
        attributes after construction and the ``self.db is not None``
        guard preserves those stubs.
        """
        if self.db is not None:
            return
        from database import Database
        from audio_fingerprinter import AudioFingerprinter
        from text_pattern_matcher import TextPatternMatcher
        from pattern_service import PatternService
        self.db = Database()
        self.audio_fingerprinter = AudioFingerprinter(db=self.db)
        self.text_pattern_matcher = TextPatternMatcher(db=self.db)
        self.pattern_service = PatternService(db=self.db)
        self.sponsor_service = SponsorService(db=self.db)

    def initialize_client(self):
        """Surface LLM client init errors at the start of a detection run.

        Also eagerly constructs the dependency objects on first call so
        the rest of the detection pipeline can access plain attributes
        without @property indirection.
        """
        self._ensure_deps()

        if not self.api_key:
            return
        try:
            client = get_llm_client()
            logger.info(f"LLM client initialized: {client.get_provider_name()}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            raise

    def get_available_models(self) -> List[Dict]:
        """Get list of available models from LLM provider.

        The underlying ``self._llm_client.list_models()`` already caches
        per-provider with a 5-minute TTL (see ``_model_list_cache`` in
        llm_client.py); no extra wrapping here. Ensures currently-configured
        models always appear in the list, even if the API doesn't advertise
        them.
        """
        try:
            self.initialize_client()
            if not self._llm_client:
                return []

            models = self._llm_client.list_models()
            model_list = [
                {'id': m.id, 'name': m.name, 'created': m.created}
                for m in models
            ]
            return self._ensure_configured_models_present(model_list)
        except Exception as e:
            logger.error(f"Could not fetch models from API: {e}")
            return []

    def _ensure_configured_models_present(self, models_list: List[Dict]) -> List[Dict]:
        """Ensure currently-configured models always appear in the model list.

        If the API/wrapper doesn't advertise a model that's actively configured
        (e.g., set as first pass or verification model), inject it so the settings UI
        shows it and doesn't lose the selection.

        Only injects models that plausibly belong to the current provider to avoid
        stale model IDs from a previous provider polluting the dropdown (e.g.
        claude-* models lingering after switching to Ollama).
        """
        existing_ids = {m['id'] for m in models_list}
        configured_models = []
        try:
            configured_models.append(self.get_model())
            configured_models.append(self.get_verification_model())
        except Exception:
            pass

        provider = get_effective_provider()

        for model_id in configured_models:
            if model_id and model_id not in existing_ids:
                if not model_matches_provider(model_id, provider):
                    logger.debug(
                        f"Skipping configured model '{model_id}' -- "
                        f"does not match current provider '{provider}'"
                    )
                    continue
                logger.info(f"Added configured model '{model_id}' to model list")
                models_list.insert(0, {
                    'id': model_id,
                    'name': model_id,
                    'created': None
                })
                existing_ids.add(model_id)

        return models_list

    def get_model(self) -> str:
        """Get configured model from database or default."""
        self._ensure_deps()
        try:
            model = self.db.get_setting('claude_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not load model from DB: {e}")
        return DEFAULT_AD_DETECTION_MODEL

    def get_verification_model(self) -> str:
        """Get verification pass model from database or fall back to first pass model."""
        self._ensure_deps()
        try:
            model = self.db.get_setting('verification_model')
            if model:
                return model
        except Exception:
            pass
        return self.get_model()

    def get_system_prompt(self) -> str:
        """Get system prompt from database or default, with dynamic sponsors substituted."""
        self._ensure_deps()
        try:
            prompt = self.db.get_setting('system_prompt')
            if prompt:
                return self._render_with_sponsors(prompt)
        except Exception as e:
            logger.warning(f"Could not load system prompt from DB: {e}")

        from utils.constants import DEFAULT_SYSTEM_PROMPT
        return self._render_with_sponsors(DEFAULT_SYSTEM_PROMPT)

    def get_verification_prompt(self) -> str:
        """Get verification prompt from database or default, with dynamic sponsors substituted."""
        self._ensure_deps()
        try:
            prompt = self.db.get_setting('verification_prompt')
            if prompt:
                return self._render_with_sponsors(prompt)
        except Exception:
            pass
        from database import DEFAULT_VERIFICATION_PROMPT
        return self._render_with_sponsors(DEFAULT_VERIFICATION_PROMPT)

    def _get_sponsor_list_safely(self) -> str:
        """Pull the dynamic sponsor list, returning empty string on any error."""
        try:
            if not self.sponsor_service:
                return ""
            return self.sponsor_service.get_claude_sponsor_list() or ""
        except Exception as e:
            logger.warning(f"Could not load dynamic sponsor list: {e}")
            return ""

    def _render_with_sponsors(self, prompt: str) -> str:
        """Substitute ``{sponsor_database}`` in a prompt with the dynamic sponsor block.

        Prompts without the placeholder get no sponsor content (the user
        opted out by editing the placeholder away).
        """
        sponsor_block = format_sponsor_block(self._get_sponsor_list_safely())
        return render_prompt(prompt, sponsor_database=sponsor_block)

    def _get_podcast_sponsor_history(self, podcast_slug: str) -> str:
        """Get previously detected sponsor names for a podcast from ad_patterns.

        Returns a formatted string for inclusion in the description section,
        or empty string if no sponsors found.
        """
        if not podcast_slug:
            return ""
        try:
            patterns = self.db.get_ad_patterns(podcast_id=podcast_slug)
            sponsors = set()
            for p in patterns:
                sponsor = p.get('sponsor')
                if sponsor and sponsor.lower() not in ('unknown', 'advertisement detected', ''):
                    sponsors.add(sponsor)
            if sponsors:
                sponsor_list = ', '.join(sorted(sponsors))
                return f"Previously detected sponsors for this podcast: {sponsor_list}\n"
        except Exception as e:
            logger.warning(f"Could not fetch sponsor history for {podcast_slug}: {e}")
        return ""

    def get_user_prompt_template(self) -> str:
        """Get user prompt template (hardcoded, not configurable)."""
        return USER_PROMPT_TEMPLATE

    def _call_llm_for_window(self, *, model, system_prompt, prompt, llm_timeout,
                              max_retries, slug, episode_id, window_label, pass_name):
        """Thin wrapper over utils.llm_call.call_llm_for_window with per-stage tunables.

        ``pass_name`` selects which config keys (DETECTION_* vs VERIFICATION_*) supply
        the temperature/max_tokens/reasoning values, and is forwarded to the LLM
        client for per-pass fallback flag scoping.
        """
        if pass_name == PASS_AD_DETECTION_1:
            prefix = 'detection'
        elif pass_name == PASS_AD_DETECTION_2:
            prefix = 'verification'
        else:
            raise ValueError(f"Unknown pass_name for ad_detector: {pass_name!r}")

        max_tokens, temperature, reasoning = resolve_stage_tunables(prefix)

        return call_llm_for_window(
            llm_client=self._llm_client,
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            llm_timeout=llm_timeout,
            max_retries=max_retries,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning,
            slug=slug,
            episode_id=episode_id,
            window_label=window_label,
            pass_name=pass_name,
        )


    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None, episode_description: str = None,
                   podcast_description: str = None,
                   progress_callback=None,
                   audio_analysis=None) -> Optional[Dict]:
        """Detect ad segments using Claude API with sliding window approach.

        Processes transcript in overlapping windows to ensure ads at chunk
        boundaries are not missed. Windows are 10 minutes with 3 minute overlap.

        Args:
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress
        """
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        if not segments:
            logger.warning(f"[{slug}:{episode_id}] No transcript segments, skipping ad detection")
            return {"ads": [], "status": "no_segments", "error": "Empty transcript"}

        try:
            self.initialize_client()

            # Pre-detect non-English segments as automatic ads (DAI in other languages)
            foreign_language_ads = self._detect_foreign_language_ads(segments, slug, episode_id)
            if foreign_language_ads:
                logger.info(f"[{slug}:{episode_id}] Auto-detected {len(foreign_language_ads)} "
                           f"non-English segments as ads")

            # Create overlapping windows from transcript
            windows = create_windows(segments)
            total_duration = segments[-1]['end'] if segments else 0

            logger.info(f"[{slug}:{episode_id}] Processing {len(windows)} windows "
                       f"({WINDOW_SIZE_SECONDS/60:.0f}min size, {WINDOW_OVERLAP_SECONDS/60:.0f}min overlap) "
                       f"for {total_duration/60:.1f}min episode")

            # Get prompts and model
            system_prompt = self.get_system_prompt()
            model = self.get_model()

            logger.info(f"[{slug}:{episode_id}] Using model: {model}")
            logger.debug(f"[{slug}:{episode_id}] System prompt ({len(system_prompt)} chars)")

            # Prepare description section (shared across windows)
            description_section = ""
            if podcast_description:
                description_section = f"Podcast Description:\n{podcast_description}\n\n"
                logger.info(f"[{slug}:{episode_id}] Including podcast description ({len(podcast_description)} chars)")
            if episode_description:
                description_section += f"Episode Description (this describes the actual content topics discussed; it may also list episode sponsors):\n{episode_description}\n"
                logger.info(f"[{slug}:{episode_id}] Including episode description ({len(episode_description)} chars)")

            # Add podcast-specific sponsor history from ad_patterns
            sponsor_history = self._get_podcast_sponsor_history(slug)
            if sponsor_history:
                description_section += sponsor_history
                logger.info(f"[{slug}:{episode_id}] Including sponsor history: {sponsor_history.strip()}")

            all_window_ads = []
            all_raw_responses = []
            failed_windows = 0
            last_error = None
            llm_timeout = get_llm_timeout()
            max_retries = get_llm_max_retries()

            # Instantiate audio signal formatter if audio analysis available
            audio_enforcer = None
            if audio_analysis:
                from audio_enforcer import AudioEnforcer
                audio_enforcer = AudioEnforcer()

            # Process each window
            for i, window in enumerate(windows):
                # Report progress for each window (keeps UI indicator alive)
                if progress_callback:
                    # First pass: 50-80% range (detecting phase)
                    progress = 50 + int((i / max(len(windows), 1)) * 30)
                    progress_callback(f"detecting:{i+1}/{len(windows)}", progress)

                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                transcript_lines = [
                    f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
                    for seg in window_segments
                ]
                audio_context = audio_enforcer.format_for_window(
                    audio_analysis, window_start, window_end
                ) if audio_enforcer else ""

                prompt = format_window_prompt(
                    podcast_name=podcast_name,
                    episode_title=episode_title,
                    description_section=description_section,
                    transcript_lines=transcript_lines,
                    window_index=i,
                    total_windows=len(windows),
                    window_start=window_start,
                    window_end=window_end,
                    audio_context=audio_context,
                )

                logger.info(f"[{slug}:{episode_id}] Window {i+1}/{len(windows)}: "
                           f"{window_start/60:.1f}-{window_end/60:.1f}min, {len(window_segments)} segments")

                response, last_error = self._call_llm_for_window(
                    model=model, system_prompt=system_prompt, prompt=prompt,
                    llm_timeout=llm_timeout, max_retries=max_retries,
                    slug=slug, episode_id=episode_id,
                    window_label=f"Window {i+1}",
                    pass_name=PASS_AD_DETECTION_1,
                )
                if response is None:
                    failed_windows += 1
                    logger.error(
                        f"[{slug}:{episode_id}] Window {i+1}/{len(windows)} failed after all retries, "
                        f"skipping (error: {last_error})"
                    )
                    continue

                # Parse response (LLMResponse.content is already extracted text)
                response_text = response.content
                all_raw_responses.append(f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}")

                preview = response_text[:500] + ('...' if len(response_text) > 500 else '')
                logger.info(f"[{slug}:{episode_id}] Window {i+1} LLM response ({len(response_text)} chars): {preview}")

                # Parse ads from response
                window_ads = parse_ads_from_response(response_text, slug, episode_id, sponsor_service=self.sponsor_service)

                # Validate timestamps against actual transcript content
                # (catches Claude hallucinating ad positions)
                window_ads = validate_ad_timestamps(
                    window_ads, window_segments, window_start, window_end
                )

                # Filter ads to window bounds - Claude sometimes hallucinates start=0.0
                # when no ads found, speculating about "beginning of episode"
                # MIN_OVERLAP_TOLERANCE, MAX_AD_DURATION_WINDOW imported from config.py

                valid_window_ads = []
                for ad in window_ads:
                    duration = ad['end'] - ad['start']
                    in_window = (ad['start'] >= window_start - MIN_OVERLAP_TOLERANCE and
                                 ad['start'] <= window_end + MIN_OVERLAP_TOLERANCE)
                    reasonable_length = duration <= MAX_AD_DURATION_WINDOW

                    if in_window and reasonable_length:
                        valid_window_ads.append(ad)
                    else:
                        logger.warning(
                            f"[{slug}:{episode_id}] Window {i+1} rejected ad: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({duration:.0f}s) - "
                            f"{'outside window' if not in_window else 'too long'}"
                        )

                window_ads = valid_window_ads
                logger.info(f"[{slug}:{episode_id}] Window {i+1} found {len(window_ads)} ads")

                all_window_ads.extend(window_ads)

            if failed_windows > 0:
                logger.warning(
                    f"[{slug}:{episode_id}] {failed_windows}/{len(windows)} windows "
                    f"failed during detection"
                )
            if failed_windows >= len(windows):
                # Surface last error so the caller can detect rate-limit vs generic failure (#238).
                last_err_type = type(last_error).__name__ if last_error else 'Unknown'
                last_err_status = getattr(last_error, 'status_code', None)
                parts = [f"All {len(windows)} detection windows failed (last error: {last_err_type}"]
                if last_err_status:
                    parts.append(f", status={last_err_status}")
                if last_error:
                    parts.append(f": {last_error}")
                parts.append(")")
                return {
                    "ads": [],
                    "status": "failed",
                    "error": "".join(parts),
                    "retryable": True,
                    "last_error_type": last_err_type,
                    "last_error_status": last_err_status,
                }

            # Deduplicate ads across windows
            final_ads = deduplicate_window_ads(all_window_ads)

            # Merge in foreign language ads (auto-detected non-English segments)
            if foreign_language_ads:
                final_ads = self._merge_detection_results(final_ads + foreign_language_ads)
                logger.info(f"[{slug}:{episode_id}] Merged {len(foreign_language_ads)} foreign language ads")

            total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
            logger.info(f"[{slug}:{episode_id}] Total after dedup: {len(final_ads)} ads ({total_ad_time/60:.1f} min)")

            for ad in final_ads:
                logger.info(f"[{slug}:{episode_id}] Ad: {ad['start']:.1f}s-{ad['end']:.1f}s "
                           f"({ad['end']-ad['start']:.0f}s) end_text='{(ad.get('end_text') or '')[:50]}'")

            return {
                "ads": final_ads,
                "status": "success",
                "raw_response": "\n\n".join(all_raw_responses),
                "prompt": f"Processed {len(windows)} windows",
                "model": model
            }

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Ad detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": is_retryable_error(e)}

    def process_transcript(self, segments: List[Dict], podcast_name: str = "Unknown",
                          episode_title: str = "Unknown", slug: str = None,
                          episode_id: str = None, episode_description: str = None,
                          audio_path: str = None,
                          podcast_id: str = None,
                          skip_patterns: bool = False,
                          podcast_description: str = None,
                          podcast_tags: Optional[set] = None,
                          progress_callback=None,
                          audio_analysis=None,
                          cancel_event=None,
                          *,
                          ctx=None) -> Dict:
        """Process transcript for ad detection using three-stage pipeline.

        Pipeline stages:
        1. Audio fingerprint matching (if audio_path provided)
        2. Text pattern matching
        3. Claude API for remaining segments

        Args:
            segments: Transcript segments
            podcast_name: Name of podcast
            episode_title: Title of episode
            slug: Podcast slug
            episode_id: Episode ID
            episode_description: Episode description
            audio_path: Path to audio file for fingerprinting
            podcast_id: Podcast ID for pattern scoping
            skip_patterns: If True, skip stages 1 & 2 (pattern DB), go directly to Claude
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress
            cancel_event: Optional threading.Event for cooperative cancellation
            ctx: Optional EpisodeContext supplying the immutable per-episode
                 fields (slug, episode_id, podcast_name, etc.). When provided,
                 its fields override the matching positional/keyword args.

        Returns:
            Dict with ads, status, and detection metadata
        """
        if ctx is not None:
            slug = ctx.slug
            episode_id = ctx.episode_id
            podcast_name = ctx.podcast_name
            episode_title = ctx.episode_title
            # Pattern scoping inside this method uses the slug, regardless of
            # the ctx.podcast_id (which is the integer DB PK and means
            # something different to downstream reviewers).
            podcast_id = ctx.slug
            podcast_description = ctx.podcast_description
            episode_description = ctx.episode_description
            podcast_tags = ctx.podcast_tags
        all_ads = []
        pattern_matched_regions = []  # Regions covered by pattern matching
        detection_stats = {
            'fingerprint_matches': 0,
            'text_pattern_matches': 0,
            'claude_matches': 0,
            'skip_patterns': skip_patterns
        }

        if skip_patterns:
            logger.info(f"[{slug}:{episode_id}] Full analysis mode: Skipping pattern DB (stages 1 & 2)")

        # Get false positive corrections for this episode to prevent re-proposing rejected ads
        false_positive_regions = []
        false_positive_texts = []
        if not skip_patterns and self.db:
            try:
                false_positive_regions = self.db.get_false_positive_corrections(episode_id)
                if false_positive_regions:
                    logger.debug(f"[{slug}:{episode_id}] Found {len(false_positive_regions)} false positive regions to exclude")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to get false positive corrections: {e}")

            # Get cross-episode false positive texts for content matching
            try:
                fp_entries = self.db.get_podcast_false_positive_texts(slug)
                false_positive_texts = [e['text'] for e in fp_entries if e.get('text')]
                if false_positive_texts:
                    logger.debug(f"[{slug}:{episode_id}] Loaded {len(false_positive_texts)} cross-episode false positive texts")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to get cross-episode false positives: {e}")

        # Stage 1: Audio Fingerprint Matching (skip if skip_patterns=True)
        if not skip_patterns and audio_path and self.audio_fingerprinter and self.audio_fingerprinter.is_available():
            try:
                logger.info(f"[{slug}:{episode_id}] Stage 1: Audio fingerprint matching")
                fp_matches = self.audio_fingerprinter.find_matches(audio_path, cancel_event=cancel_event)

                fp_added = 0
                for match in fp_matches:
                    # Skip if this region was previously marked as false positive
                    if self._is_region_covered(match.start, match.end, [(fp['start'], fp['end']) for fp in false_positive_regions]):
                        logger.debug(f"[{slug}:{episode_id}] Skipping fingerprint match {match.start:.1f}s-{match.end:.1f}s (false positive)")
                        continue

                    # Build reason with pattern reference
                    if match.sponsor:
                        reason = f"{match.sponsor} (pattern #{match.pattern_id})"
                    else:
                        reason = f"Pattern #{match.pattern_id} (fingerprint)"

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': reason,
                        'sponsor': match.sponsor,
                        'detection_stage': 'fingerprint',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append({
                        'start': match.start,
                        'end': match.end,
                        'pattern_id': match.pattern_id
                    })
                    fp_added += 1

                    # Record pattern match for metrics and promotion
                    if self.pattern_service and match.pattern_id:
                        self.pattern_service.record_pattern_match(match.pattern_id, episode_id)

                detection_stats['fingerprint_matches'] = fp_added
                if fp_matches:
                    logger.info(f"[{slug}:{episode_id}] Fingerprint stage found {len(fp_matches)} ads")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Fingerprint matching failed: {e}")

        # Cancel check between stages
        _check_cancel(cancel_event, slug, episode_id)

        # Stage 2: Text Pattern Matching (skip if skip_patterns=True)
        if not skip_patterns and self.text_pattern_matcher and self.text_pattern_matcher.is_available():
            try:
                logger.info(f"[{slug}:{episode_id}] Stage 2: Text pattern matching")
                text_matches = self.text_pattern_matcher.find_matches(
                    segments,
                    podcast_id=podcast_id,
                    podcast_tags=podcast_tags,
                )

                tp_added = 0
                for match in text_matches:
                    # Skip if already covered by fingerprint match
                    if self._is_region_covered(match.start, match.end, pattern_matched_regions):
                        continue

                    # Skip if this region was previously marked as false positive
                    if self._is_region_covered(match.start, match.end, [(fp['start'], fp['end']) for fp in false_positive_regions]):
                        logger.debug(f"[{slug}:{episode_id}] Skipping text pattern match {match.start:.1f}s-{match.end:.1f}s (false positive)")
                        continue

                    # Build reason with pattern reference
                    if match.sponsor:
                        reason = f"{match.sponsor} (pattern #{match.pattern_id})"
                    else:
                        reason = f"Pattern #{match.pattern_id} ({match.match_type})"

                    ad = {
                        'start': match.start,
                        'end': match.end,
                        'confidence': match.confidence,
                        'reason': reason,
                        'sponsor': match.sponsor,
                        'detection_stage': 'text_pattern',
                        'pattern_id': match.pattern_id
                    }
                    all_ads.append(ad)
                    pattern_matched_regions.append({
                        'start': match.start,
                        'end': match.end,
                        'pattern_id': match.pattern_id
                    })
                    tp_added += 1

                    # Record pattern match for metrics and promotion
                    if self.pattern_service and match.pattern_id:
                        self.pattern_service.record_pattern_match(match.pattern_id, episode_id)

                detection_stats['text_pattern_matches'] = tp_added
                if text_matches:
                    logger.info(f"[{slug}:{episode_id}] Text pattern stage found {len(text_matches)} ads")
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Text pattern matching failed: {e}")

        # Cancel check between stages
        _check_cancel(cancel_event, slug, episode_id)

        # Stage 3: Claude API for remaining content
        logger.info(f"[{slug}:{episode_id}] Stage 3: Claude API detection")

        # If we found pattern matches, we can potentially skip Claude for covered regions
        # For now, we still run Claude on full transcript but mark pattern-detected regions
        result = self.detect_ads(
            segments, podcast_name, episode_title, slug, episode_id, episode_description,
            podcast_description=podcast_description,
            progress_callback=progress_callback,
            audio_analysis=audio_analysis
        )

        if result is None:
            result = {"ads": [], "status": "failed", "error": "Detection failed", "retryable": True}

        # Merge Claude detections with pattern matches
        claude_ads = result.get('ads', [])
        cross_episode_skipped = 0
        fp_pairs = [(fp['start'], fp['end']) for fp in false_positive_regions]

        # Duration feedback: update pattern avg_duration from Claude's more accurate boundaries
        updated_patterns = set()
        for ad in claude_ads:
            for region in pattern_matched_regions:
                pid = region.get('pattern_id')
                if not pid or pid in updated_patterns:
                    continue
                overlap = self._compute_overlap(
                    ad['start'], ad['end'],
                    region['start'], region['end']
                )
                if overlap >= PATTERN_CORRECTION_OVERLAP_THRESHOLD:
                    observed_duration = ad['end'] - ad['start']
                    if self.pattern_service:
                        self.pattern_service.update_duration(
                            pid, observed_duration
                        )
                        updated_patterns.add(pid)

        for ad in claude_ads:
            uncovered_portions = get_uncovered_portions(ad, pattern_matched_regions)

            if not uncovered_portions:
                logger.debug(f"[{slug}:{episode_id}] Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s "
                             f"fully covered by patterns")
                continue

            # Log if ad was trimmed (not returned as-is)
            if not (len(uncovered_portions) == 1
                    and uncovered_portions[0]['start'] == ad['start']
                    and uncovered_portions[0]['end'] == ad['end']):
                for portion in uncovered_portions:
                    logger.info(f"[{slug}:{episode_id}] Preserved uncovered portion: "
                                f"{portion['start']:.1f}s-{portion['end']:.1f}s "
                                f"(from Claude ad {ad['start']:.1f}s-{ad['end']:.1f}s)")

            for portion in uncovered_portions:
                # Mirrors the same-episode false-positive region check that
                # stages 1 and 2 already apply.
                if fp_pairs and self._is_region_covered(
                    portion['start'], portion['end'], fp_pairs,
                ):
                    logger.debug(
                        f"[{slug}:{episode_id}] Skipping Claude portion "
                        f"{portion['start']:.1f}s-{portion['end']:.1f}s "
                        f"(same-episode false positive)"
                    )
                    continue

                # Cross-episode false positive check
                if false_positive_texts and self.text_pattern_matcher:
                    ad_text = self._get_segment_text(segments, portion['start'], portion['end'])
                    if ad_text and len(ad_text) >= 50:
                        is_fp, similarity = self.text_pattern_matcher.matches_false_positive(
                            ad_text, false_positive_texts
                        )
                        if is_fp:
                            logger.info(f"[{slug}:{episode_id}] Skipping portion "
                                        f"{portion['start']:.1f}s-{portion['end']:.1f}s "
                                        f"(cross-episode false positive, similarity={similarity:.2f})")
                            cross_episode_skipped += 1
                            continue

                portion['detection_stage'] = 'claude'
                all_ads.append(portion)

        if cross_episode_skipped > 0:
            logger.info(f"[{slug}:{episode_id}] Skipped {cross_episode_skipped} detections due to cross-episode false positives")

        detection_stats['claude_matches'] = len([a for a in all_ads if a.get('detection_stage') == 'claude'])

        # Sort by start time
        all_ads.sort(key=lambda x: x['start'])

        # Merge overlapping ads
        all_ads = self._merge_detection_results(all_ads)

        # Log detection summary
        total = len(all_ads)
        fp_count = detection_stats['fingerprint_matches']
        tp_count = detection_stats['text_pattern_matches']
        cl_count = detection_stats['claude_matches']
        logger.info(
            f"[{slug}:{episode_id}] Detection complete: {total} ads "
            f"(fingerprint: {fp_count}, text: {tp_count}, claude: {cl_count})"
        )

        # Pattern learning moved to main.py (after validation sets was_cut)

        result['ads'] = all_ads
        result['detection_stats'] = detection_stats
        return result

    def _is_region_covered(self, start: float, end: float,
                           covered_regions: list) -> bool:
        """Check if a time region is substantially covered by existing detections."""
        for region in covered_regions:
            cov_start, cov_end = _unpack_region(region)
            if self._compute_overlap(cov_start, cov_end, start, end) > 0.5:
                return True
        return False

    @staticmethod
    def _compute_overlap(a_start, a_end, b_start, b_end):
        """Return fraction of region B covered by region A (0.0-1.0)."""
        return overlap_ratio(a_start, a_end, b_start, b_end)

    def _get_segment_text(self, segments: List[Dict], start: float, end: float) -> str:
        """Extract transcript text within a time range."""
        text_parts = []
        for seg in segments:
            # Include segment if it overlaps with the requested range
            if seg.get('end', 0) >= start and seg.get('start', 0) <= end:
                text_parts.append(seg.get('text', ''))
        return ' '.join(text_parts).strip()

    def _extract_sponsor_from_reason(self, reason: str) -> Optional[str]:
        """Extract sponsor name from ad detection reason using known sponsors DB.

        Args:
            reason: Ad detection reason text (e.g., "ZipRecruiter host-read sponsor segment")

        Returns:
            Extracted sponsor name (normalized) or None
        """
        if not reason or not self.sponsor_service:
            return None

        # Reject garbage reason values before extraction
        reason_lower = reason.lower().strip()
        if reason_lower in INVALID_SPONSOR_VALUES or len(reason_lower) < 2:
            logger.debug(f"Rejecting invalid reason for sponsor extraction: '{reason}'")
            return None

        # Use sponsor service to find canonical sponsor name from DB
        sponsor = self.sponsor_service.find_sponsor_in_text(reason)
        if sponsor:
            # Validate extracted sponsor
            sponsor_lower = sponsor.lower().strip()
            if sponsor_lower in INVALID_SPONSOR_VALUES or len(sponsor_lower) < 2:
                logger.debug(f"Rejecting invalid extracted sponsor: '{sponsor}'")
                return None
            return sponsor
        return None

    def _ad_passes_learning_filters(self, ad: Dict, min_confidence: float) -> bool:
        """Apply basic eligibility filters before sponsor resolution.

        Returns True if the ad should proceed to sponsor extraction.
        Filters: was_cut, detection_stage == 'claude', confidence floor,
        and stricter confidence for long (>90s) detections.
        """
        # Only learn from ads that were actually removed
        if not ad.get('was_cut', False):
            logger.debug(f"Skipping pattern for uncut ad: {ad['start']:.1f}s-{ad['end']:.1f}s")
            return False

        # Only learn from Claude detections (not fingerprint/text pattern)
        if ad.get('detection_stage') != 'claude':
            return False

        # Require high confidence
        confidence = ad.get('confidence', 0)
        if confidence < min_confidence:
            return False

        # For longer detections, require higher confidence to avoid learning
        # from merged multi-ad spans which contaminate patterns
        duration = ad['end'] - ad['start']
        if duration > 90:  # > 90 seconds
            if confidence < 0.92:  # Require very high confidence for long ads
                logger.debug(
                    f"Skipping pattern for long ad ({duration:.0f}s) with "
                    f"confidence {confidence:.2f} (threshold 0.92 for >90s ads)"
                )
                return False

        return True

    def _resolve_sponsor_for_learning(self, ad: Dict) -> Optional[str]:
        """Resolve a usable sponsor name from an ad via 4-tier lookup.

        Tier 1: sponsor DB lookup on raw sponsor field
        Tier 2: sponsor DB lookup on reason text
        Tier 3: extract from reason via regex patterns
        Tier 4: use raw sponsor if it looks valid

        Returns the canonical sponsor name, or None if no usable sponsor.
        """
        sponsor = None
        raw_sponsor = ad.get('sponsor')
        reason_text = ad.get('reason', '')

        # Tier 1: sponsor DB lookup on raw sponsor field
        if raw_sponsor and self.sponsor_service:
            sponsor = self.sponsor_service.find_sponsor_in_text(raw_sponsor)

        # Tier 2: sponsor DB lookup on reason text
        if not sponsor and reason_text and self.sponsor_service:
            sponsor = self.sponsor_service.find_sponsor_in_text(reason_text)

        # Tier 3: extract from reason via regex patterns
        if not sponsor:
            sponsor = self._extract_sponsor_from_reason(reason_text)

        # Tier 4: use raw sponsor if it looks valid
        if not sponsor and raw_sponsor:
            raw_lower = raw_sponsor.lower().strip()
            if raw_lower not in INVALID_SPONSOR_VALUES and len(raw_lower) >= 2:
                sponsor = raw_sponsor

        if not sponsor:
            return None

        return canonical_sponsor(sponsor)

    def _sponsor_blocked_by_gates(
        self, sponsor: str, active_pattern_sponsors: set
    ) -> bool:
        """Apply Gate A (prefix-of-known) and Gate B (unknown short single word).

        Returns True if the sponsor should be rejected.
        """
        # Gate A: reject sponsors that are strict prefixes of known sponsors
        if self.sponsor_service:
            sponsor_lower = sponsor.lower()
            all_sponsors = self.sponsor_service.get_sponsors()
            for s in all_sponsors:
                known = s['name'].lower()
                if known != sponsor_lower and known.startswith(sponsor_lower + ' '):
                    logger.info(f"Skipping pattern: '{sponsor}' is prefix of '{s['name']}'")
                    return True

        # Gate B: reject single short words for unknown sponsors.
        # "Known" means the sponsor is in the sponsor registry, has an
        # existing active pattern, or is in the curated short-brand seed.
        words = sponsor.strip().split()
        if len(words) == 1 and len(sponsor.strip()) < 6:
            sponsor_lower = sponsor.strip().lower()
            is_known = (
                (self.sponsor_service and self.sponsor_service.find_sponsor_in_text(sponsor))
                or sponsor_lower in active_pattern_sponsors
                or sponsor_lower in KNOWN_SHORT_BRANDS
            )
            if not is_known:
                logger.info(f"Skipping pattern for unknown short sponsor: '{sponsor}'")
                return True

        return False

    def _create_pattern_and_fingerprint(
        self, ad: Dict, segments: List[Dict], sponsor: str,
        podcast_id: str, episode_id: Optional[str], audio_path: Optional[str]
    ) -> bool:
        """Create a text pattern and optional audio fingerprint for an ad.

        Returns True if a pattern was successfully created.
        """
        try:
            pattern_id = self.text_pattern_matcher.create_pattern_from_ad(
                segments=segments,
                start=ad['start'],
                end=ad['end'],
                sponsor=sponsor,
                scope='podcast',
                podcast_id=podcast_id,
                episode_id=episode_id
            )

            if pattern_id:
                logger.info(
                    f"Created pattern {pattern_id} from Claude detection: "
                    f"{ad['start']:.1f}s-{ad['end']:.1f}s, sponsor={sponsor}"
                )

                # Store audio fingerprint alongside the text pattern
                if audio_path and self.audio_fingerprinter and self.audio_fingerprinter.is_available():
                    try:
                        self.audio_fingerprinter.store_fingerprint(
                            pattern_id=pattern_id,
                            audio_path=audio_path,
                            start=ad['start'],
                            end=ad['end']
                        )
                    except Exception as fp_e:
                        logger.debug(f"Could not store fingerprint for pattern {pattern_id}: {fp_e}")
                return True
        except Exception as e:
            logger.warning(f"Failed to create pattern from detection: {e}")
        return False

    def learn_from_detections(
        self, ads: List[Dict], segments: List[Dict], podcast_id: str,
        episode_id: str = None, audio_path: str = None
    ) -> int:
        """Create patterns from high-confidence Claude detections.

        This enables automatic pattern learning so the system improves over time.
        Only learns from Claude detections with high confidence and sponsor info.

        Args:
            ads: List of detected ads with confidence and detection_stage
            segments: Transcript segments for text extraction
            podcast_id: Podcast slug for scoping patterns
            episode_id: Episode ID for tracking pattern origin
            audio_path: Path to audio file for fingerprint storage

        Returns:
            Number of patterns created
        """
        if not self.text_pattern_matcher:
            return 0

        patterns_created = 0
        min_confidence = 0.85  # Only learn from high-confidence detections

        # Preload active pattern sponsors once so Gate B doesn't do N queries.
        try:
            active_pattern_sponsors = self.db.get_active_pattern_sponsors() if self.db else set()
        except Exception:
            active_pattern_sponsors = set()

        for ad in ads:
            if not self._ad_passes_learning_filters(ad, min_confidence):
                continue

            sponsor = self._resolve_sponsor_for_learning(ad)
            if not sponsor:
                continue

            if self._sponsor_blocked_by_gates(sponsor, active_pattern_sponsors):
                continue

            if self._create_pattern_and_fingerprint(
                ad, segments, sponsor, podcast_id, episode_id, audio_path
            ):
                patterns_created += 1

        if patterns_created > 0:
            logger.info(f"Learned {patterns_created} new patterns from detections")

        return patterns_created

    def _detect_foreign_language_ads(
        self, segments: List[Dict], slug: str = None, episode_id: str = None
    ) -> List[Dict]:
        """Auto-detect non-English segments as ads (DAI in other languages).

        Non-English segments (Spanish, etc.) are almost always dynamically inserted
        ads from ad networks targeting specific demographics. These should be
        automatically flagged as ads.

        Args:
            segments: Transcript segments with optional is_foreign_language flag
            slug: Podcast slug for logging
            episode_id: Episode ID for logging

        Returns:
            List of ad markers for foreign language segments
        """
        foreign_ads = []

        # Find consecutive foreign language segments and merge them
        current_ad_start = None
        current_ad_end = None

        for seg in segments:
            if seg.get('is_foreign_language'):
                if current_ad_start is None:
                    # Start new foreign language region
                    current_ad_start = seg['start']
                # Extend region
                current_ad_end = seg['end']
            else:
                # Not foreign language - close any open region
                if current_ad_start is not None:
                    duration = current_ad_end - current_ad_start
                    # Only flag regions longer than 5 seconds
                    if duration >= 5.0:
                        foreign_ads.append({
                            'start': current_ad_start,
                            'end': current_ad_end,
                            'confidence': 0.95,  # High confidence for language detection
                            'reason': 'Non-English language segment (likely DAI ad)',
                            'detection_stage': 'language',
                            'end_text': '[Foreign language content]'
                        })
                        logger.info(
                            f"[{slug}:{episode_id}] Foreign language ad: "
                            f"{current_ad_start:.1f}s-{current_ad_end:.1f}s ({duration:.1f}s)"
                        )
                    current_ad_start = None
                    current_ad_end = None

        # Close final region if needed
        if current_ad_start is not None:
            duration = current_ad_end - current_ad_start
            if duration >= 5.0:
                foreign_ads.append({
                    'start': current_ad_start,
                    'end': current_ad_end,
                    'confidence': 0.95,
                    'reason': 'Non-English language segment (likely DAI ad)',
                    'detection_stage': 'language',
                    'end_text': '[Foreign language content]'
                })

        return foreign_ads

    def _merge_detection_results(self, ads: List[Dict]) -> List[Dict]:
        """Merge overlapping ads from different detection stages."""
        if not ads:
            return []

        # Sort by start time
        ads = sorted(ads, key=lambda x: x['start'])

        merged = [ads[0].copy()]
        for current in ads[1:]:
            last = merged[-1]

            # Check for overlap (within 3 seconds)
            if current['start'] <= last['end'] + 3.0:
                # Merge - prefer pattern-detected metadata
                if current['end'] > last['end']:
                    last['end'] = current['end']

                # Keep higher confidence
                if current.get('confidence', 0) > last.get('confidence', 0):
                    last['confidence'] = current['confidence']

                # Prefer pattern detection stage over claude
                stage_priority = {'fingerprint': 0, 'text_pattern': 1, 'claude': 2}
                if stage_priority.get(current.get('detection_stage'), 2) < stage_priority.get(last.get('detection_stage'), 2):
                    last['detection_stage'] = current['detection_stage']
                    last['pattern_id'] = current.get('pattern_id')
                    if current.get('sponsor'):
                        last['sponsor'] = current['sponsor']
                # Prefer the more descriptive reason
                current_reason = current.get('reason', '')
                last_reason = last.get('reason', '')
                if len(current_reason) > len(last_reason):
                    last['reason'] = current_reason
            else:
                merged.append(current.copy())

        return merged

    def run_verification_detection(self, segments: List[Dict],
                                    podcast_name: str = "Unknown",
                                    episode_title: str = "Unknown",
                                    slug: str = None, episode_id: str = None,
                                    episode_description: str = None,
                                    podcast_description: str = None,
                                    progress_callback=None,
                                    audio_analysis=None) -> Dict:
        """Run ad detection with the verification prompt on processed audio.

        Uses the same sliding window approach as detect_ads() but with the
        verification system prompt and verification model setting.

        Args:
            segments: Transcript segments from re-transcribed processed audio
            podcast_name: Name of podcast
            episode_title: Title of episode
            slug: Podcast slug
            episode_id: Episode ID
            episode_description: Episode description
            podcast_description: Podcast-level description for context
            progress_callback: Optional callback(stage, percent) to report progress
        """
        if not self.api_key:
            logger.warning("Skipping verification detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            windows = create_windows(segments)
            total_duration = segments[-1]['end'] if segments else 0

            logger.info(f"[{slug}:{episode_id}] Verification: Processing {len(windows)} windows "
                       f"for {total_duration/60:.1f}min processed audio")

            system_prompt = self.get_verification_prompt()
            model = self.get_verification_model()

            logger.info(f"[{slug}:{episode_id}] Verification using model: {model}")

            # Prepare description section
            description_section = ""
            if podcast_description:
                description_section = f"Podcast Description:\n{podcast_description}\n\n"
            if episode_description:
                description_section += (
                    f"Episode Description (this describes the actual content topics discussed; "
                    f"it may also list episode sponsors):\n{episode_description}\n"
                )

            sponsor_history = self._get_podcast_sponsor_history(slug)
            if sponsor_history:
                description_section += sponsor_history

            all_window_ads = []
            all_raw_responses = []
            failed_windows = 0
            last_error = None
            llm_timeout = get_llm_timeout()
            max_retries = get_llm_max_retries()

            # Instantiate audio signal formatter if audio analysis available
            audio_enforcer = None
            if audio_analysis:
                from audio_enforcer import AudioEnforcer
                audio_enforcer = AudioEnforcer()

            for i, window in enumerate(windows):
                if progress_callback:
                    progress = 85 + int((i / max(len(windows), 1)) * 10)
                    progress_callback(f"detecting:{i+1}/{len(windows)}", progress)

                window_segments = window['segments']
                window_start = window['start']
                window_end = window['end']

                transcript_lines = [
                    f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
                    for seg in window_segments
                ]
                audio_context = audio_enforcer.format_for_window(
                    audio_analysis, window_start, window_end
                ) if audio_enforcer else ""

                prompt = format_window_prompt(
                    podcast_name=podcast_name,
                    episode_title=episode_title,
                    description_section=description_section,
                    transcript_lines=transcript_lines,
                    window_index=i,
                    total_windows=len(windows),
                    window_start=window_start,
                    window_end=window_end,
                    audio_context=audio_context,
                )

                logger.info(f"[{slug}:{episode_id}] Verification Window {i+1}/{len(windows)}: "
                           f"{window_start/60:.1f}-{window_end/60:.1f}min")

                response, last_error = self._call_llm_for_window(
                    model=model, system_prompt=system_prompt, prompt=prompt,
                    llm_timeout=llm_timeout, max_retries=max_retries,
                    slug=slug, episode_id=episode_id,
                    window_label=f"Verification Window {i+1}",
                    pass_name=PASS_AD_DETECTION_2,
                )
                if response is None:
                    failed_windows += 1
                    logger.error(
                        f"[{slug}:{episode_id}] Verification Window {i+1}/{len(windows)} "
                        f"failed after all retries, skipping (error: {last_error})"
                    )
                    continue

                response_text = response.content
                all_raw_responses.append(
                    f"=== Window {i+1} ({window_start/60:.1f}-{window_end/60:.1f}min) ===\n{response_text}"
                )

                preview = response_text[:500] + ('...' if len(response_text) > 500 else '')
                logger.info(f"[{slug}:{episode_id}] Verification Window {i+1} LLM response ({len(response_text)} chars): {preview}")

                window_ads = parse_ads_from_response(response_text, slug, episode_id, sponsor_service=self.sponsor_service)

                # Filter to window bounds
                valid_window_ads = []
                for ad in window_ads:
                    duration = ad['end'] - ad['start']
                    in_window = (ad['start'] >= window_start - MIN_OVERLAP_TOLERANCE and
                                 ad['start'] <= window_end + MIN_OVERLAP_TOLERANCE)
                    reasonable_length = duration <= MAX_AD_DURATION_WINDOW

                    if in_window and reasonable_length:
                        valid_window_ads.append(ad)
                    else:
                        logger.warning(
                            f"[{slug}:{episode_id}] Verification Window {i+1} rejected ad: "
                            f"{ad['start']:.1f}s-{ad['end']:.1f}s ({duration:.0f}s)"
                        )

                for ad in valid_window_ads:
                    ad['detection_stage'] = 'verification'

                logger.info(f"[{slug}:{episode_id}] Verification Window {i+1} found {len(valid_window_ads)} ads")
                all_window_ads.extend(valid_window_ads)

            if failed_windows > 0:
                logger.warning(
                    f"[{slug}:{episode_id}] {failed_windows}/{len(windows)} windows "
                    f"failed during verification"
                )
            if failed_windows >= len(windows):
                # Surface last error so the caller can detect rate-limit vs generic failure (#238).
                last_err_type = type(last_error).__name__ if last_error else 'Unknown'
                last_err_status = getattr(last_error, 'status_code', None)
                parts = [f"All {len(windows)} verification windows failed (last error: {last_err_type}"]
                if last_err_status:
                    parts.append(f", status={last_err_status}")
                if last_error:
                    parts.append(f": {last_error}")
                parts.append(")")
                return {
                    "ads": [],
                    "status": "failed",
                    "error": "".join(parts),
                    "retryable": True,
                    "last_error_type": last_err_type,
                    "last_error_status": last_err_status,
                }

            final_ads = deduplicate_window_ads(all_window_ads)

            for ad in final_ads:
                ad['detection_stage'] = 'verification'

            if final_ads:
                total_ad_time = sum(ad['end'] - ad['start'] for ad in final_ads)
                logger.info(f"[{slug}:{episode_id}] Verification total: {len(final_ads)} ads "
                           f"({total_ad_time/60:.1f} min)")
            else:
                logger.info(f"[{slug}:{episode_id}] Verification: No additional ads found")

            return {
                "ads": final_ads,
                "status": "success",
                "raw_response": "\n\n".join(all_raw_responses),
                "prompt": f"Verification: Processed {len(windows)} windows",
                "model": model
            }

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Verification detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": is_retryable_error(e)}


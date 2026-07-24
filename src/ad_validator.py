"""Post-detection validation for ad markers."""
import re
import logging
from typing import ClassVar, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from config import (
    MIN_AD_DURATION, SHORT_AD_WARN, LONG_AD_WARN, MAX_AD_DURATION,
    MAX_AD_DURATION_CONFIRMED, LOW_CONFIDENCE,
    REJECT_CONFIDENCE, HIGH_CONFIDENCE_OVERRIDE, PRE_ROLL, MID_ROLL_1,
    POST_ROLL, MAX_AD_PERCENTAGE, MAX_ADS_PER_5MIN,
    MERGE_GAP_THRESHOLD, MAX_SILENT_GAP,
    HOLD_REASON_MAX_DURATION, HOLD_REASON_NO_CUE,
    HOLD_REASON_NO_SPLICE, VETO_MIN_CUT_SECONDS,
    HOLD_REASON_UNCORROBORATED_TAIL,
    HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
    SPLICE_CORROBORATION_WINDOW_SECONDS,
    CORRECTION_MATCH_MIN_COVERAGE,
    is_cue_backed,
)
from utils.markers import mark_distinct_merge
from utils.text import extract_text_from_segments
from utils.time import overlap_ratio

logger = logging.getLogger(__name__)


class Decision(Enum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


@dataclass
class ValidationResult:
    """Results from ad validation."""
    ads: List[Dict]
    accepted: int = 0
    reviewed: int = 0
    rejected: int = 0
    warnings: List[str] = field(default_factory=list)
    corrections: List[str] = field(default_factory=list)


class AdValidator:
    """Validates and corrects ad detection results.

    Runs after Claude ad detection but before audio processing to:
    - Catch errors (invalid boundaries, suspicious durations)
    - Flag low-confidence detections for review
    - Auto-correct common issues (merge close ads, clamp boundaries)
    - Verify ads against transcript content
    """

    # Thresholds imported from config.py:
    # MIN_AD_DURATION, SHORT_AD_WARN, LONG_AD_WARN, MAX_AD_DURATION,
    # MAX_AD_DURATION_CONFIRMED, LOW_CONFIDENCE,
    # REJECT_CONFIDENCE, HIGH_CONFIDENCE_OVERRIDE, PRE_ROLL, MID_ROLL_*,
    # POST_ROLL, MAX_AD_PERCENTAGE, MAX_ADS_PER_5MIN, MERGE_GAP_THRESHOLD

    # Sponsor patterns for verification
    SPONSOR_PATTERNS = re.compile(
        r'betterhelp|athletic\s*greens|ag1|squarespace|nordvpn|'
        r'expressvpn|hellofresh|audible|masterclass|ziprecruiter|'
        r'raycon|manscaped|stamps\.com|indeed|linkedin|'
        r'casper|helix|brooklinen|bombas|calm|headspace|'
        r'better\s*help|honey|simplisafe|wix|shopify|'
        r'bluechew|roman|hims|keeps|factor|noom|'
        r'magic\s*spoon|athletic\s*brewing|liquid\s*iv',
        re.IGNORECASE
    )

    AD_SIGNAL_PATTERNS = re.compile(
        r'promo\s*code|use\s+code\s+\w+|\.com\/\w+|'
        r'percent\s+off|free\s+(trial|shipping)|'
        r'link\s+in\s+(the\s+)?(show\s+)?notes|'
        r'sponsored\s+by|brought\s+to\s+you|'
        r'check\s+(them\s+)?out\s+at|visit\s+\w+\.com|'
        r'download\s+(the\s+)?app|sign\s+up\s+(today|now)',
        re.IGNORECASE
    )

    VAGUE_REASONS: ClassVar[List[str]] = [
        'advertisement', 'ad detected', 'sponsor', 'promotional content',
        'possible ad', 'likely ad', 'advertisement segment'
    ]

    # Patterns that indicate Claude determined this is NOT an ad
    # The second branch only matches "(show|episode|regular|actual) content" when
    # preceded by assertion verbs (is, appears to be, etc.) or at start-of-string.
    # This avoids false positives on phrases like "transition from show content".
    NOT_AD_PATTERNS = re.compile(
        r'not\s+an?\s+(ad|advertisement|sponsor|promo|commercial)|'
        r'(?:^|(?:is|appears\s+to\s+be|seems\s+like|contains)\s+)(episode|show|regular|actual)\s+content|'
        r'this\s+is\s+(not|n\'t)\s+|'
        r'does\s+not\s+appear\s+to\s+be|'
        r'no\s+(ad|advertisement|sponsor)|'
        r'false\s+positive',
        re.IGNORECASE
    )

    # Audio corroboration (ad-splice-detection Layer 1): stored-analysis
    # signals within this window of a marker edge corroborate the marker.
    CORROBORATION_WINDOW_S = 5.0
    CORROBORATION_MIN_VOLUME_STEP_DB = 12.0
    TAIL_EOF_WINDOW_S = 5.0  # marker end this close to EOF counts as tail

    def __init__(self, episode_duration: float, segments: List[Dict] = None,
                 episode_description: str = None,
                 false_positive_corrections: List[Dict] = None,
                 confirmed_corrections: List[Dict] = None,
                 min_cut_confidence: float = 0.80,
                 positional_prior=None,
                 max_ad_duration_override: float = None,
                 cue_gate_enabled: bool = False,
                 splice_veto_enabled: bool = True,
                 veto_min_cut_seconds: float = VETO_MIN_CUT_SECONDS,
                 differential_corr_max: float = 0.60):
        """Initialize validator.

        Args:
            episode_duration: Total episode duration in seconds
            segments: List of transcript segments with 'start', 'end', 'text' keys
            episode_description: Episode description (may contain sponsor info)
            false_positive_corrections: List of dicts with 'start' and 'end' keys
                                        for user-marked false positives to auto-reject
            confirmed_corrections: List of dicts with 'start' and 'end' keys
                                   for user-confirmed ads to auto-accept
            min_cut_confidence: Minimum confidence to auto-accept (user's slider value)
            positional_prior: Optional PositionalPrior with this feed's learned
                              ad-break zones; replaces the global position boosts
            max_ad_duration_override: Per-feed cap in seconds; None = no cap
            cue_gate_enabled: When True, ads without cue evidence are held for review
            differential_corr_max: Resolved differential_measured_corr_max
                setting (threaded by the caller; the validator has no db
                handle). A differential region corroborates a marker only
                when its measured corr is <= this value.
        """
        self.episode_duration = episode_duration
        self.segments = segments or []
        self.episode_description = episode_description or ""
        self.description_sponsors = self._extract_sponsors_from_description()
        self.false_positive_corrections = false_positive_corrections or []
        self.confirmed_corrections = confirmed_corrections or []
        self.min_cut_confidence = min_cut_confidence
        self.positional_prior = positional_prior
        self.max_ad_duration_override = max_ad_duration_override
        self.cue_gate_enabled = cue_gate_enabled
        self.splice_veto_enabled = splice_veto_enabled
        self.veto_min_cut_seconds = veto_min_cut_seconds
        self.differential_corr_max = differential_corr_max
        self._audio_analysis = None

        if self.false_positive_corrections:
            logger.info(f"Loaded {len(self.false_positive_corrections)} false positive corrections")
        if self.confirmed_corrections:
            logger.info(f"Loaded {len(self.confirmed_corrections)} confirmed corrections")
        if self.positional_prior is not None:
            logger.info(f"Using learned positional prior: "
                        f"{len(self.positional_prior.zones)} zones")

    def _extract_sponsors_from_description(self) -> set:
        """Extract sponsor names from episode description.

        Looks for sponsors in:
        - <strong>Sponsors:</strong> sections with <a href="..."> links
        - URL patterns like domain.com/code
        - Known sponsor patterns

        Returns:
            Set of lowercase sponsor names
        """
        sponsors = set()
        if not self.episode_description:
            return sponsors

        description = self.episode_description.lower()

        # Extract domains from href URLs (e.g., "bitwarden.com/twit" -> "bitwarden")
        href_pattern = re.compile(r'href=["\']?(?:https?://)?(?:www\.)?([a-z0-9-]+)\.(?:com|io|co|net|org)', re.IGNORECASE)
        for match in href_pattern.finditer(self.episode_description):
            domain = match.group(1).lower()
            # Skip common non-sponsor domains
            if domain not in ('redcircle', 'twitter', 'instagram', 'youtube', 'facebook', 'apple', 'spotify'):
                sponsors.add(domain)

        # Check for known sponsor patterns in description text
        if self.SPONSOR_PATTERNS.search(description):
            for match in self.SPONSOR_PATTERNS.finditer(description):
                sponsor = match.group(0).lower().replace(' ', '')
                sponsors.add(sponsor)

        if sponsors:
            logger.info(f"Extracted sponsors from description: {sponsors}")

        return sponsors

    def _is_sponsor_confirmed(self, ad: Dict) -> bool:
        """Check if the ad's sponsor is confirmed in the episode description.

        Args:
            ad: Ad marker with reason field

        Returns:
            True if sponsor name from ad matches a sponsor in description
        """
        if not self.description_sponsors:
            return False

        # Extract sponsor from ad reason
        reason = ad.get('reason', '').lower()

        # Check for direct matches with description sponsors
        for sponsor in self.description_sponsors:
            if sponsor in reason:
                logger.info(f"Sponsor '{sponsor}' confirmed in description for ad: {ad.get('reason', '')[:50]}")
                return True

        # Also check transcript text in ad range for sponsor mentions
        ad_text = self._get_text_in_range(ad['start'], ad['end']).lower()
        for sponsor in self.description_sponsors:
            if sponsor in ad_text:
                logger.info(f"Sponsor '{sponsor}' found in ad transcript, confirmed in description")
                return True

        return False

    def _overlaps_corrections(self, corrections: List[Dict], start: float, end: float,
                               overlap_threshold: float = CORRECTION_MATCH_MIN_COVERAGE) -> bool:
        """Check if a time range overlaps with any correction in the given list.

        Args:
            corrections: List of correction dicts with 'start' and 'end' keys
            start: Segment start time in seconds
            end: Segment end time in seconds
            overlap_threshold: Minimum overlap ratio to consider a match (0.0-1.0)

        Returns:
            True if segment overlaps significantly with any correction
        """
        if not corrections:
            return False

        segment_duration = end - start
        if segment_duration < 0.001:
            logger.warning(f"Skipping overlap check for near-zero duration segment: {segment_duration}")
            return False

        for corr in corrections:
            # Ratio is (segment AND correction) / segment_duration
            # i.e. fraction of the segment covered by the correction.
            ratio = overlap_ratio(corr['start'], corr['end'], start, end)
            if ratio >= overlap_threshold:
                return True

        return False

    def _overlaps_false_positive(self, start: float, end: float,
                                  overlap_threshold: float = CORRECTION_MATCH_MIN_COVERAGE) -> bool:
        """Check if a time range overlaps with any user-marked false positive."""
        return self._overlaps_corrections(self.false_positive_corrections, start, end, overlap_threshold)

    def _overlaps_confirmed(self, start: float, end: float,
                            overlap_threshold: float = CORRECTION_MATCH_MIN_COVERAGE) -> bool:
        """Check if a time range overlaps with any user-confirmed correction."""
        return self._overlaps_corrections(self.confirmed_corrections, start, end, overlap_threshold)

    def _matching_confirmed(self, start: float, end: float,
                            overlap_threshold: float = CORRECTION_MATCH_MIN_COVERAGE) -> Optional[Dict]:
        """Return a user-confirmed correction covering >= threshold of the
        range, or None. Mirrors _overlaps_confirmed but yields the match so
        the caller can honor a trimmed approval's confirmed_span. A trimmed
        approval wins over a plain confirm covering the same range -- it is
        the more specific user intent and must not be shadowed."""
        segment_duration = end - start
        if segment_duration < 0.001:
            return None
        plain = None
        for corr in self.confirmed_corrections:
            if overlap_ratio(corr['start'], corr['end'], start, end) >= overlap_threshold:
                if corr.get('confirmed_span'):
                    return corr
                if plain is None:
                    plain = corr
        return plain

    def validate(self, ads: List[Dict],
                 audio_analysis: Optional[Dict] = None) -> ValidationResult:
        """Validate all ads and return results.

        Args:
            ads: List of ad markers from detection
            audio_analysis: Stored audio-analysis dict for the episode
                (AudioAnalysisResult.to_dict() shape); used to corroborate
                heuristic markers with measured audio evidence

        Returns:
            ValidationResult with validated ads and statistics
        """
        self._audio_analysis = audio_analysis

        if not ads:
            return ValidationResult(ads=[])

        result = ValidationResult(ads=[])

        # Make SHALLOW copies to avoid modifying originals. Callers rely on
        # this staying shallow: _validate_verification_ads attaches an
        # _orig_twin reference that must survive into the validated output.
        ads = [ad.copy() for ad in ads]

        # Step 1: Auto-correct boundaries
        ads = self._clamp_boundaries(ads, result)

        # Step 2: Remove invalid ads (start >= end after clamping)
        ads = [ad for ad in ads if ad['end'] > ad['start']]

        # Step 3: Merge tiny gaps
        ads = self._merge_close_ads(ads, result)

        # Step 3.5: Extend trailing ad to end of episode if close
        ads = self._extend_trailing_ad(ads, result)

        # Step 4: Validate each ad
        for ad in ads:
            validated = self._validate_ad(ad)
            result.ads.append(validated)

            decision = validated.get('validation', {}).get('decision', 'REVIEW')
            if decision == Decision.ACCEPT.value:
                result.accepted += 1
            elif decision == Decision.REVIEW.value:
                result.reviewed += 1
            else:
                result.rejected += 1

        # Step 5: Check overall density
        self._check_ad_density(result)

        # Log summary
        logger.info(
            f"Validation complete: {result.accepted} accepted, "
            f"{result.reviewed} review, {result.rejected} rejected"
        )
        if result.corrections:
            logger.info(f"Corrections applied: {len(result.corrections)}")
        if result.warnings:
            for warning in result.warnings:
                logger.warning(f"Validation warning: {warning}")

        return result

    def _validate_ad(self, ad: Dict) -> Dict:
        """Validate a single ad marker.

        Args:
            ad: Ad marker dict with start, end, confidence, reason

        Returns:
            Ad marker with 'validation' field added
        """
        flags = []
        corrections = []
        confidence = ad.get('confidence', 1.0)

        # Pop stale held/corroboration state -- re-derived on every pass.
        ad.pop('held_for_review', None)
        ad.pop('hold_reason', None)
        ad.pop('corroborated_by', None)

        duration = ad['end'] - ad['start']
        position = ad['start'] / self.episode_duration if self.episode_duration > 0 else 0

        # Check for user-marked false positives first (highest priority)
        if self._overlaps_false_positive(ad['start'], ad['end']):
            flags.append("INFO: User marked as false positive")
            logger.info(
                f"Auto-rejecting segment {ad['start']:.1f}s-{ad['end']:.1f}s: "
                f"overlaps with user-marked false positive"
            )
            # Return early with REJECT decision
            ad['validation'] = {
                'decision': Decision.REJECT.value,
                'adjusted_confidence': 0.0,
                'original_confidence': ad.get('confidence', 1.0),
                'flags': flags,
                'corrections': corrections
            }
            return ad

        # Check for user-confirmed corrections (second priority)
        confirmed = self._matching_confirmed(ad['start'], ad['end'])
        if confirmed is not None:
            # A trimmed approval confirmed only a sub-span as ad. Pull a
            # boundary inward only when it falls in a trimmed-out zone (inside
            # the reviewed original bounds but outside the approved span) so
            # the content the user explicitly kept is never re-cut; parts of
            # the detection beyond the reviewed bounds are new territory and
            # are left alone.
            span = confirmed.get('confirmed_span')
            auto_accept = True
            if span:
                new_start, new_end = ad['start'], ad['end']
                if confirmed['start'] <= new_start < span['start']:
                    new_start = span['start']
                if span['end'] < new_end <= confirmed['end']:
                    new_end = span['end']
                if new_end <= new_start:
                    # The detection lies entirely inside user-kept content;
                    # do not auto-accept -- let normal validation judge it.
                    auto_accept = False
                elif (new_start, new_end) != (ad['start'], ad['end']):
                    logger.info(
                        f"Clamping confirmed segment {ad['start']:.1f}s-{ad['end']:.1f}s "
                        f"to user-approved span {new_start:.1f}s-{new_end:.1f}s"
                    )
                    flags.append("INFO: Clamped to user-approved span")
                    ad['start'] = new_start
                    ad['end'] = new_end
            if auto_accept:
                flags.append("INFO: User confirmed as ad")
                logger.info(
                    f"Auto-accepting segment {ad['start']:.1f}s-{ad['end']:.1f}s: "
                    f"overlaps with user-confirmed correction"
                )
                ad['validation'] = {
                    'decision': Decision.ACCEPT.value,
                    'adjusted_confidence': 1.0,
                    'original_confidence': ad.get('confidence', 1.0),
                    'flags': flags,
                    'corrections': corrections
                }
                return ad

        # Duration checks
        if duration < MIN_AD_DURATION:
            flags.append(f"ERROR: Very short ({duration:.1f}s)")
        elif duration < SHORT_AD_WARN:
            flags.append(f"WARN: Short duration ({duration:.1f}s)")

        # Check if sponsor is confirmed in episode description
        sponsor_confirmed = self._is_sponsor_confirmed(ad)
        max_duration = MAX_AD_DURATION_CONFIRMED if sponsor_confirmed else MAX_AD_DURATION

        if duration > max_duration:
            flags.append(f"ERROR: Very long ({duration:.1f}s)")
        elif duration > LONG_AD_WARN:
            if sponsor_confirmed:
                flags.append(f"INFO: Long ({duration:.1f}s) but sponsor confirmed in description")
            else:
                flags.append(f"WARN: Long duration ({duration:.1f}s)")

        # Confidence checks (on original confidence)
        if confidence < REJECT_CONFIDENCE:
            flags.append(f"ERROR: Very low confidence ({confidence:.2f})")
        elif confidence < LOW_CONFIDENCE:
            flags.append(f"WARN: Low confidence ({confidence:.2f})")

        # Position heuristics - adjust confidence
        confidence = self._apply_position_boost(confidence, position)

        # Reason quality - adjust confidence
        confidence = self._check_reason_quality(ad, confidence, flags)

        # Transcript verification - adjust confidence
        confidence = self._verify_in_transcript(ad, confidence, flags)

        # Make decision based on adjusted confidence and flags
        decision = self._make_decision(confidence, flags, duration)

        # Apply per-feed hold rules after the base decision.
        decision = self._apply_hold_rules(ad, decision, confidence, flags, duration)

        ad['validation'] = {
            'decision': decision.value,
            'adjusted_confidence': round(confidence, 3),
            'original_confidence': ad.get('confidence', 1.0),
            'flags': flags,
            'corrections': corrections
        }

        return ad

    def _apply_position_boost(self, confidence: float, position: float) -> float:
        """Boost confidence for typical ad positions.

        When a learned positional prior is present it replaces the global
        zones entirely: positions are only boosted where this feed's own
        history supports it, and never outside.

        Args:
            confidence: Current confidence score
            position: Position in episode (0.0 - 1.0)

        Returns:
            Adjusted confidence
        """
        if self.positional_prior is not None:
            # Adjacent zones can overlap by their margins; take the strongest.
            boosts = [zone.boost for zone in self.positional_prior.zones
                      if zone.low <= position <= zone.high]
            if boosts:
                return min(1.0, confidence + max(boosts))
            return confidence

        if PRE_ROLL[0] <= position <= PRE_ROLL[1]:
            # Pre-roll is very common - strong boost
            return min(1.0, confidence + 0.10)
        elif POST_ROLL[0] <= position <= POST_ROLL[1]:
            # Post-roll is common
            return min(1.0, confidence + 0.05)
        elif MID_ROLL_1[0] <= position <= MID_ROLL_1[1]:
            # Mid-roll positions are common
            return min(1.0, confidence + 0.05)
        return confidence

    def _check_reason_quality(self, ad: Dict, confidence: float,
                               flags: List[str]) -> float:
        """Adjust confidence based on reason quality.

        Args:
            ad: Ad marker
            confidence: Current confidence
            flags: List to append warnings to

        Returns:
            Adjusted confidence
        """
        reason = ad.get('reason', '').lower()

        # Check if reason indicates this is NOT an ad - auto-reject
        if self.NOT_AD_PATTERNS.search(reason):
            flags.append("ERROR: Reason indicates not an ad")
            logger.info(f"Auto-rejecting segment: reason indicates not an ad: {reason[:100]}")
            return 0.0  # Force rejection

        # Vague reason = penalize
        if any(vague in reason for vague in self.VAGUE_REASONS):
            flags.append("WARN: Vague reason")
            return max(0.0, confidence - 0.1)

        # Sponsor name in reason = boost
        if self.SPONSOR_PATTERNS.search(reason):
            return min(1.0, confidence + 0.1)

        return confidence

    def _verify_in_transcript(self, ad: Dict, confidence: float,
                               flags: List[str]) -> float:
        """Verify ad content appears in transcript.

        For ``detection_stage == 'vad_gap'`` markers without sponsor or
        ad-signal corroboration in range, clamps confidence below
        ``self.min_cut_confidence`` so the marker routes to REVIEW.

        Args:
            ad: Ad marker
            confidence: Current confidence
            flags: List to append warnings to

        Returns:
            Adjusted confidence
        """
        if not self.segments:
            # No segments: check vad_gap corroboration early. Untranscribed
            # audio can never show transcript signals (TWiT 1091 catch-22).
            if ad.get('detection_stage') == 'vad_gap':
                source = self._audio_corroboration_source(ad)
                if source is not None:
                    ad['corroborated_by'] = source
                    flags.append(f"INFO: Audio corroboration ({source})")
                else:
                    # No corroboration found - clamp confidence to force REVIEW
                    confidence = min(confidence, max(0.0, self.min_cut_confidence - 0.01))
            return confidence

        # Get transcript text for ad time range
        ad_text = self._get_text_in_range(ad['start'], ad['end'])

        if not ad_text:
            flags.append("WARN: No transcript text in ad range")
            # Tail markers with no transcript text defer corroboration check to
            # _apply_hold_rules via direct _audio_corroboration_source call.
            return confidence

        # Check for sponsor names
        if self.SPONSOR_PATTERNS.search(ad_text):
            return min(1.0, confidence + 0.1)

        # Check for ad signals
        if self.AD_SIGNAL_PATTERNS.search(ad_text):
            return min(1.0, confidence + 0.05)

        # No signals found - only flag if not already high confidence
        if confidence < 0.85:
            flags.append("WARN: No ad signals in transcript")

        # vad_gap markers: check audio corroboration when text exists but
        # patterns don't match. Set corroborated_by if source found, else clamp.
        if ad.get('detection_stage') == 'vad_gap':
            source = self._audio_corroboration_source(ad)
            if source is not None:
                ad['corroborated_by'] = source
                flags.append(f"INFO: Audio corroboration ({source})")
            else:
                # No corroboration found - clamp confidence to force REVIEW
                confidence = min(confidence, max(0.0, self.min_cut_confidence - 0.01))

        # Verify end_text exists in transcript
        end_text = ad.get('end_text', '')
        if end_text and len(end_text) > 5:
            if end_text.lower() not in ad_text.lower():
                flags.append("WARN: end_text not found in transcript")
                return max(0.0, confidence - 0.05)

        return confidence

    def _splice_events(self) -> List[Dict]:
        """Events from the stored splice_evidence payload, [] when absent."""
        payload = (self._audio_analysis or {}).get('splice_evidence') or {}
        return payload.get('events') or []

    def _splice_calibrated(self) -> bool:
        """True when this feed's splice calibration status is 'calibrated'
        (spec 2.3c); cold-start feeds corroborate but never veto."""
        payload = (self._audio_analysis or {}).get('splice_evidence') or {}
        return payload.get('calibration', {}).get('status') == 'calibrated'

    def _audio_corroboration_source(self, ad: Dict) -> Optional[str]:
        """Return the strongest stored-audio evidence source near the ad's
        boundaries, or None.

        Layer 1 checks DAI transition pairs and >= 12 dB volume anomalies
        within +-5s of the ad start or end. Layer 2 adds splice_evidence
        events (+-3s); Layer 3 adds dai_differential region overlap.
        """
        analysis = self._audio_analysis
        if not analysis:
            return None

        window = self.CORROBORATION_WINDOW_S

        def near_edge(t):
            return (abs(t - ad['start']) <= window
                    or abs(t - ad['end']) <= window)

        volume_hit = False
        for sig in analysis.get('signals') or []:
            if not (near_edge(sig.get('start', 0.0)) or near_edge(sig.get('end', 0.0))):
                continue
            if sig.get('signal_type') == 'dai_transition_pair':
                return 'transition_pair'
            if sig.get('signal_type') in ('volume_increase', 'volume_decrease'):
                deviation = (sig.get('details') or {}).get('deviation_db') or 0.0
                if abs(deviation) >= self.CORROBORATION_MIN_VOLUME_STEP_DB:
                    volume_hit = True
        if volume_hit:
            return 'volume_anomaly'

        # Layer 2: a splice-evidence event within +-3.0s of either boundary
        # (spec 2.3a). Tighter window than the transition/volume checks
        # because splice events are sharply localized.
        for event in self._splice_events():
            time = event.get('time')
            if time is None:
                continue
            end_time = event.get('end_time')
            end_time = end_time if end_time is not None else time
            for edge in (ad.get('start'), ad.get('end')):
                if edge is None:
                    continue
                if (time - SPLICE_CORROBORATION_WINDOW_SECONDS <= edge
                        <= end_time + SPLICE_CORROBORATION_WINDOW_SECONDS):
                    return 'splice_evidence'

        # Layer 3: a cross-fetch differential region overlapping the marker is
        # the strongest corroboration class (audio proven to differ). Same
        # measured-corr gate as candidate minting (2.76.0): a high-corr
        # "differential" mostly matched across fetches and proves nothing;
        # legacy stored regions (corr hard-coded 0.0) still corroborate.
        diff = (self._audio_analysis or {}).get('dai_differential') or {}
        for region in diff.get('regions', []):
            if region.get('kind') != 'differential':
                continue
            corr = region.get('corr')
            if (not isinstance(corr, (int, float))
                    or corr > self.differential_corr_max):
                continue
            if (float(region['start_s']) < float(ad.get('end', 0.0))
                    and float(region['end_s']) > float(ad.get('start', 0.0))):
                return 'dai_differential'
        return None

    def _get_text_in_range(self, start: float, end: float) -> str:
        """Get transcript text within time range.

        Delegates to utils.text.extract_text_from_segments.
        """
        return extract_text_from_segments(self.segments, start, end)

    def _make_decision(self, confidence: float, flags: List[str],
                        duration: float = 0.0) -> Decision:
        """Decide ACCEPT/REVIEW/REJECT based on confidence and flags.

        Args:
            confidence: Adjusted confidence score
            flags: List of flags/warnings
            duration: Ad duration in seconds (for high-confidence override check)

        Returns:
            Decision enum value
        """
        has_errors = any('ERROR' in f for f in flags)
        has_long_error = any('Very long' in f for f in flags)

        # High confidence (>0.9) overrides long-duration errors up to 15 minutes
        if has_long_error and confidence >= HIGH_CONFIDENCE_OVERRIDE:
            if duration <= MAX_AD_DURATION_CONFIRMED:
                logger.info(
                    f"Accepting long ad ({duration:.1f}s) due to high confidence ({confidence:.2f})"
                )
                return Decision.ACCEPT

        # ERROR flags or very low confidence -> always reject
        if has_errors or confidence < REJECT_CONFIDENCE:
            return Decision.REJECT

        # Use user's slider threshold instead of hardcoded 0.85/0.60. Compare at
        # the same precision stored in adjusted_confidence so the downstream gate
        # (which reads the rounded value) can never re-cut a REVIEW ad.
        if round(confidence, 3) >= self.min_cut_confidence:
            return Decision.ACCEPT
        else:
            return Decision.REVIEW

    def _apply_hold_rules(self, ad: Dict, decision: Decision, confidence: float,
                          flags: List[str], duration: float) -> Decision:
        """Apply per-feed hold rules after the base decision.

        A held ad gets decision=REVIEW with held_for_review=True so the gate
        keeps it in the audio. Returns the (possibly updated) decision.
        """
        # Rule 1: max duration override.
        if self.max_ad_duration_override is not None and duration > self.max_ad_duration_override:
            if decision == Decision.ACCEPT:
                self._mark_held(ad, flags, HOLD_REASON_MAX_DURATION)
                return Decision.REVIEW
            if decision == Decision.REJECT and confidence >= REJECT_CONFIDENCE:
                # Only hold duration-only rejects -- leave low-confidence junk
                # and non-duration errors (NOT_AD etc.) as plain REJECT.
                duration_flags = [f for f in flags if 'ERROR' in f]
                if all('Very long' in f for f in duration_flags) and duration_flags:
                    self._mark_held(ad, flags, HOLD_REASON_MAX_DURATION)
                    return Decision.REVIEW

        # Rule 5: uncorroborated cross-fetch differential (#541) -> held for
        # review, never solo-cut. Ordered before the cue gate so the hold
        # carries its specific reason. Corroborated regions had the flag
        # cleared in _merge_detection_results and cut normally.
        if (decision != Decision.REJECT
                and ad.get('detection_stage') == 'dai_differential'
                and ad.get('differential_uncorroborated')):
            self._mark_held(ad, flags, HOLD_REASON_DIFFERENTIAL_UNCORROBORATED)
            return Decision.REVIEW

        # Rule 2: cue-gated approval. Only applies to ACCEPT after rule 1.
        # is_cue_backed treats manual markers as exempt (human decision).
        if self.cue_gate_enabled and decision == Decision.ACCEPT:
            if not is_cue_backed(ad):
                self._mark_held(ad, flags, HOLD_REASON_NO_CUE)
                return Decision.REVIEW

        # Rule 3: zero-splice-evidence veto (spec 2.3c). A long LLM/pattern
        # cut with no splice event inside the span or near either edge goes
        # to the review queue instead of shipping silently. Calibrated feeds
        # only: cold-start evidence corroborates but never vetoes.
        if (self.splice_veto_enabled and decision == Decision.ACCEPT
                and duration >= self.veto_min_cut_seconds
                and ad.get('detection_stage') in ('claude', 'text_pattern')
                and self._splice_calibrated()
                and self._audio_corroboration_source(ad) is None):
            self._mark_held(ad, flags, HOLD_REASON_NO_SPLICE)
            return Decision.REVIEW

        # Rule 4: an uncorroborated vad_gap marker at the episode tail must
        # surface in the pending-review queue instead of shipping silently.
        # Fires on REVIEW (clamp already routed it there) AND on ACCEPT (the
        # empty-text early-return left confidence unclamped; 0.80 >= threshold
        # = ACCEPT but there is no stored evidence to justify the cut). A
        # corroborated tail (source not None) cuts regardless of decision.
        # Calls the corroboration helper directly so tails with no transcript
        # text in range still count stored audio evidence.
        if (decision in (Decision.REVIEW, Decision.ACCEPT)
                and ad.get('detection_stage') == 'vad_gap'
                and self.episode_duration > 0
                and self.episode_duration - ad['end'] <= self.TAIL_EOF_WINDOW_S
                and self._audio_corroboration_source(ad) is None):
            self._mark_held(ad, flags, HOLD_REASON_UNCORROBORATED_TAIL)
            return Decision.REVIEW

        return decision

    def _mark_held(self, ad: Dict, flags: List[str], reason: str) -> None:
        """Set held_for_review state on the ad dict and append a flag entry."""
        ad['held_for_review'] = True
        ad['hold_reason'] = reason
        flags.append(f"INFO: Held for review ({reason})")
        logger.info(
            f"Holding ad {ad['start']:.1f}s-{ad['end']:.1f}s for review: {reason}"
        )

    def _clamp_boundaries(self, ads: List[Dict],
                          result: ValidationResult) -> List[Dict]:
        """Clamp ad boundaries to valid range.

        Args:
            ads: List of ad markers
            result: ValidationResult to record corrections

        Returns:
            Ads with clamped boundaries
        """
        for ad in ads:
            if ad['start'] < 0:
                original = ad['start']
                ad['start'] = 0
                result.corrections.append(f"Clamped negative start {original:.1f}s to 0")

            if self.episode_duration > 0 and ad['end'] > self.episode_duration:
                original = ad['end']
                ad['end'] = self.episode_duration
                result.corrections.append(
                    f"Clamped end {original:.1f}s to duration {self.episode_duration:.1f}s"
                )
        return ads

    def _extend_trailing_ad(self, ads: List[Dict],
                            result: ValidationResult,
                            max_gap: float = 30.0) -> List[Dict]:
        """Extend the last ad to the end of episode if close enough.

        DAI post-roll ads often end slightly before the actual episode end.
        If an ad ends within max_gap seconds of the episode end, extend it.

        Args:
            ads: List of ad markers
            result: ValidationResult to record corrections
            max_gap: Maximum gap (seconds) to extend. Default 30s.

        Returns:
            Ads with potentially extended trailing ad
        """
        if not ads or self.episode_duration <= 0:
            return ads

        # Sort by start time and get last ad
        sorted_ads = sorted(ads, key=lambda x: x['start'])
        last_ad = sorted_ads[-1]

        gap_to_end = self.episode_duration - last_ad['end']

        # Only extend if gap is positive and within threshold
        if 0 < gap_to_end <= max_gap:
            original_end = last_ad['end']
            last_ad['end'] = self.episode_duration
            result.corrections.append(
                f"Extended trailing ad from {original_end:.1f}s to episode end "
                f"({self.episode_duration:.1f}s) - gap was {gap_to_end:.1f}s"
            )
            logger.info(
                f"Extended trailing ad to episode end: {original_end:.1f}s -> "
                f"{self.episode_duration:.1f}s (gap: {gap_to_end:.1f}s)"
            )

        return ads

    def _has_speech_in_range(self, start: float, end: float) -> bool:
        """Check if any transcript segments contain speech in the given range."""
        if not self.segments:
            return True  # Assume speech if no segments available
        for seg in self.segments:
            seg_start = seg.get('start', 0)
            seg_end = seg.get('end', 0)
            if seg_start < end and seg_end > start:
                text = seg.get('text', '').strip()
                if text and len(text) > 1:
                    return True
        return False

    def _merge_close_ads(self, ads: List[Dict],
                         result: ValidationResult) -> List[Dict]:
        """Merge ads with tiny gaps.

        Args:
            ads: List of ad markers
            result: ValidationResult to record corrections

        Returns:
            Merged ads
        """
        if len(ads) < 2:
            return ads

        sorted_ads = sorted(ads, key=lambda x: x['start'])
        merged = [sorted_ads[0].copy()]

        for current in sorted_ads[1:]:
            last = merged[-1]
            gap = current['start'] - last['end']

            # #541, generalized: never merge across a held/not-held boundary
            # (any hold reason) -- the fold would hold the real ad or cut
            # the held span, and on an auto-approve recut it would grow the
            # marker past its trimmed confirm so the confirmed_span clamp
            # never fires and trimmed-out audio gets cut.
            if (bool(last.get('differential_uncorroborated'))
                    != bool(current.get('differential_uncorroborated'))
                    or bool(last.get('held_for_review'))
                    != bool(current.get('held_for_review'))):
                merged.append(current.copy())
                continue

            if 0 <= gap < MERGE_GAP_THRESHOLD:
                # Always merge small gaps (< 5s)
                mark_distinct_merge(last, current)
                last['end'] = max(last['end'], current['end'])
                if current.get('reason') and current['reason'] != last.get('reason'):
                    last['reason'] = f"{last.get('reason', '')} + {current['reason']}"
                if current.get('confidence', 0) > last.get('confidence', 0):
                    last['confidence'] = current['confidence']
                # A merged span containing any previously-cut audio stays cut.
                if current.get('_saved_was_cut'):
                    last['_saved_was_cut'] = True
                result.corrections.append(f"Merged ads with {gap:.1f}s gap")
            elif 0 <= gap < MAX_SILENT_GAP and not self._has_speech_in_range(last['end'], current['start']):
                # Merge larger gaps if no speech in between
                mark_distinct_merge(last, current)
                last['end'] = max(last['end'], current['end'])
                if current.get('reason') and current['reason'] != last.get('reason'):
                    last['reason'] = f"{last.get('reason', '')} + {current['reason']}"
                if current.get('confidence', 0) > last.get('confidence', 0):
                    last['confidence'] = current['confidence']
                if current.get('_saved_was_cut'):
                    last['_saved_was_cut'] = True
                result.corrections.append(f"Merged ads across {gap:.1f}s silent gap")
            else:
                merged.append(current.copy())

        return merged

    def _check_ad_density(self, result: ValidationResult) -> None:
        """Check overall ad density for suspicious patterns.

        Args:
            result: ValidationResult to add warnings to
        """
        if not result.ads or self.episode_duration <= 0:
            return

        # Calculate total ad time (excluding rejected)
        total_ad_time = sum(
            ad['end'] - ad['start'] for ad in result.ads
            if ad.get('validation', {}).get('decision') != Decision.REJECT.value
        )

        ad_percentage = total_ad_time / self.episode_duration

        if ad_percentage > MAX_AD_PERCENTAGE:
            result.warnings.append(
                f"High ad density: {ad_percentage:.1%} of episode "
                f"({total_ad_time:.0f}s of {self.episode_duration:.0f}s)"
            )

        # Check ads per 5-minute window
        for window_start in range(0, int(self.episode_duration), 300):
            window_end = min(window_start + 300, int(self.episode_duration))
            ads_in_window = sum(
                1 for ad in result.ads
                if ad['start'] >= window_start and ad['start'] < window_end
                and ad.get('validation', {}).get('decision') != Decision.REJECT.value
            )
            if ads_in_window > MAX_ADS_PER_5MIN:
                result.warnings.append(
                    f"Multiple ads ({ads_in_window}) in window "
                    f"{window_start // 60}-{window_end // 60} min"
                )

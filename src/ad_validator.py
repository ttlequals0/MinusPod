"""Post-detection validation for ad markers."""
import re
import logging
from typing import List, Dict
from dataclasses import dataclass, field
from enum import Enum

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

    # Duration thresholds (seconds)
    MIN_AD_DURATION = 7.0       # ERROR if less (quick mentions are ~10s minimum)
    SHORT_AD_WARN = 30.0        # WARN if less
    LONG_AD_WARN = 180.0        # WARN if more (3 min)
    MAX_AD_DURATION = 300.0     # ERROR if more (5 min)

    # Confidence thresholds (0.0 - 1.0 scale)
    HIGH_CONFIDENCE = 0.85      # Auto-accept threshold
    LOW_CONFIDENCE = 0.5        # Warn threshold
    REJECT_CONFIDENCE = 0.3     # Auto-reject threshold

    # Position windows (as % of episode duration)
    PRE_ROLL = (0.0, 0.05)      # First 5%
    MID_ROLL_1 = (0.20, 0.35)   # Common mid-roll positions
    MID_ROLL_2 = (0.45, 0.55)
    MID_ROLL_3 = (0.65, 0.80)
    POST_ROLL = (0.95, 1.0)     # Last 5%

    # Ad density limits
    MAX_AD_PERCENTAGE = 0.30    # 30% of episode is suspicious
    MAX_ADS_PER_5MIN = 1        # More than 1 ad per 5 min is suspicious

    # Gap thresholds
    MERGE_GAP_THRESHOLD = 5.0   # Merge ads within 5s

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

    VAGUE_REASONS = [
        'advertisement', 'ad detected', 'sponsor', 'promotional content',
        'possible ad', 'likely ad', 'advertisement segment'
    ]

    def __init__(self, episode_duration: float, segments: List[Dict] = None):
        """Initialize validator.

        Args:
            episode_duration: Total episode duration in seconds
            segments: List of transcript segments with 'start', 'end', 'text' keys
        """
        self.episode_duration = episode_duration
        self.segments = segments or []

    def validate(self, ads: List[Dict]) -> ValidationResult:
        """Validate all ads and return results.

        Args:
            ads: List of ad markers from detection

        Returns:
            ValidationResult with validated ads and statistics
        """
        if not ads:
            return ValidationResult(ads=[])

        result = ValidationResult(ads=[])

        # Make copies to avoid modifying originals
        ads = [ad.copy() for ad in ads]

        # Step 1: Auto-correct boundaries
        ads = self._clamp_boundaries(ads, result)

        # Step 2: Remove invalid ads (start >= end after clamping)
        ads = [ad for ad in ads if ad['end'] > ad['start']]

        # Step 3: Merge tiny gaps
        ads = self._merge_close_ads(ads, result)

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

        duration = ad['end'] - ad['start']
        position = ad['start'] / self.episode_duration if self.episode_duration > 0 else 0

        # Duration checks
        if duration < self.MIN_AD_DURATION:
            flags.append(f"ERROR: Very short ({duration:.1f}s)")
        elif duration < self.SHORT_AD_WARN:
            flags.append(f"WARN: Short duration ({duration:.1f}s)")

        if duration > self.MAX_AD_DURATION:
            flags.append(f"ERROR: Very long ({duration:.1f}s)")
        elif duration > self.LONG_AD_WARN:
            flags.append(f"WARN: Long duration ({duration:.1f}s)")

        # Confidence checks (on original confidence)
        if confidence < self.REJECT_CONFIDENCE:
            flags.append(f"ERROR: Very low confidence ({confidence:.2f})")
        elif confidence < self.LOW_CONFIDENCE:
            flags.append(f"WARN: Low confidence ({confidence:.2f})")

        # Position heuristics - adjust confidence
        confidence = self._apply_position_boost(confidence, position)

        # Reason quality - adjust confidence
        confidence = self._check_reason_quality(ad, confidence, flags)

        # Transcript verification - adjust confidence
        confidence = self._verify_in_transcript(ad, confidence, flags)

        # Make decision based on adjusted confidence and flags
        decision = self._make_decision(confidence, flags)

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

        Args:
            confidence: Current confidence score
            position: Position in episode (0.0 - 1.0)

        Returns:
            Adjusted confidence
        """
        if self.PRE_ROLL[0] <= position <= self.PRE_ROLL[1]:
            # Pre-roll is very common - strong boost
            return min(1.0, confidence + 0.10)
        elif self.POST_ROLL[0] <= position <= self.POST_ROLL[1]:
            # Post-roll is common
            return min(1.0, confidence + 0.05)
        elif any(start <= position <= end for start, end in
                 [self.MID_ROLL_1, self.MID_ROLL_2, self.MID_ROLL_3]):
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

        Args:
            ad: Ad marker
            confidence: Current confidence
            flags: List to append warnings to

        Returns:
            Adjusted confidence
        """
        if not self.segments:
            return confidence

        # Get transcript text for ad time range
        ad_text = self._get_text_in_range(ad['start'], ad['end'])

        if not ad_text:
            flags.append("WARN: No transcript text in ad range")
            return confidence

        # Check for sponsor names
        if self.SPONSOR_PATTERNS.search(ad_text):
            return min(1.0, confidence + 0.1)

        # Check for ad signals
        if self.AD_SIGNAL_PATTERNS.search(ad_text):
            return min(1.0, confidence + 0.05)

        # No signals found - only flag if not already high confidence
        if confidence < self.HIGH_CONFIDENCE:
            flags.append("WARN: No ad signals in transcript")

        # Verify end_text exists in transcript
        end_text = ad.get('end_text', '')
        if end_text and len(end_text) > 5:
            if end_text.lower() not in ad_text.lower():
                flags.append("WARN: end_text not found in transcript")
                return max(0.0, confidence - 0.05)

        return confidence

    def _get_text_in_range(self, start: float, end: float) -> str:
        """Get transcript text within time range.

        Args:
            start: Start time in seconds
            end: End time in seconds

        Returns:
            Concatenated transcript text
        """
        text_parts = []
        for seg in self.segments:
            seg_start = seg.get('start', 0)
            seg_end = seg.get('end', 0)
            # Include segment if it overlaps with the range
            if seg_end >= start and seg_start <= end:
                text_parts.append(seg.get('text', ''))
        return ' '.join(text_parts)

    def _make_decision(self, confidence: float, flags: List[str]) -> Decision:
        """Decide ACCEPT/REVIEW/REJECT based on confidence and flags.

        Args:
            confidence: Adjusted confidence score
            flags: List of flags/warnings

        Returns:
            Decision enum value
        """
        has_errors = any('ERROR' in f for f in flags)

        if has_errors or confidence < self.REJECT_CONFIDENCE:
            return Decision.REJECT
        elif confidence >= self.HIGH_CONFIDENCE and not any('WARN' in f for f in flags):
            return Decision.ACCEPT
        elif confidence >= 0.6 and not has_errors:
            return Decision.ACCEPT
        else:
            return Decision.REVIEW

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

            if 0 <= gap < self.MERGE_GAP_THRESHOLD:
                # Merge: extend last ad to cover current
                last['end'] = max(last['end'], current['end'])
                last['validation_merged'] = True
                # Combine reasons if different
                if current.get('reason') and current['reason'] != last.get('reason'):
                    last['reason'] = f"{last.get('reason', '')} + {current['reason']}"
                # Use higher confidence
                if current.get('confidence', 0) > last.get('confidence', 0):
                    last['confidence'] = current['confidence']
                result.corrections.append(f"Merged ads with {gap:.1f}s gap")
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

        if ad_percentage > self.MAX_AD_PERCENTAGE:
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
            if ads_in_window > self.MAX_ADS_PER_5MIN:
                result.warnings.append(
                    f"Multiple ads ({ads_in_window}) in window "
                    f"{window_start // 60}-{window_end // 60} min"
                )

"""
Audio signal enforcement for ad detection.

After Claude's first pass, programmatically checks whether audio signals
overlap with detected ads. Uncovered signals with ad language in the
transcript become new ads.
"""

import logging
import re
from typing import List, Dict, Optional

logger = logging.getLogger('podcast.audio_enforcer')


class AudioEnforcer:
    """
    Enforces audio signals against Claude's ad detections.

    Finds audio signals (volume anomalies, DAI transitions) not covered by
    any detected ad, checks if the overlapping transcript contains ad language,
    and creates new ad entries for uncovered signals that look promotional.
    """

    def __init__(self, sponsor_service=None):
        self.sponsor_service = sponsor_service

        # Regex patterns for ad language
        self.ad_language_patterns = [
            re.compile(p, re.IGNORECASE) for p in [
                r'\b(?:promo\s*code|coupon\s*code|discount\s*code)\b',
                r'\b(?:use\s+code)\s+\w+',
                r'\bdot\s+com\s+slash\b',
                r'\b(?:free\s+trial|sign\s+up)\b.*\b(?:at|visit)\b',
                r'\blink\s+in\s+(?:the\s+)?(?:show\s+notes|description)\b',
                r'\bbrought\s+to\s+you\s+by\b',
                r'\bsponsored\s+by\b',
                r'\bspecial\s+offer\b',
            ]
        ]

    def enforce(self, ads: List[Dict], audio_analysis, segments: List[Dict],
                slug: str = None, episode_id: str = None) -> List[Dict]:
        """Check audio signals against detected ads, create new ads for
        uncovered signals with ad language in transcript.

        Args:
            ads: List of ad dicts from first pass detection
            audio_analysis: AudioAnalysisResult with signals
            segments: Transcript segments
            slug: Podcast slug for logging
            episode_id: Episode ID for logging

        Returns:
            Updated ads list (original ads + any new enforced ads)
        """
        if not audio_analysis or not audio_analysis.signals:
            return ads

        # Find signals NOT covered by existing ads
        uncovered = self._find_uncovered_signals(ads, audio_analysis.signals)

        if not uncovered:
            return ads

        new_ads = []
        for signal in uncovered:
            # Get transcript text overlapping this signal
            text = self._get_transcript_text(segments, signal.start, signal.end)

            if signal.signal_type == 'dai_transition_pair':
                # DAI transitions: create ad if ad language present OR
                # high confidence (>=0.8) even without ad language
                if self._has_ad_language(text) or signal.confidence >= 0.8:
                    new_ads.append(self._signal_to_ad(signal, text, 'audio_enforced'))
                elif self.sponsor_service:
                    sponsor = self.sponsor_service.find_sponsor_in_text(text)
                    if sponsor:
                        new_ads.append(self._signal_to_ad(signal, text, 'audio_enforced'))
                        logger.info(f"[{slug}:{episode_id}] Enforcer: DAI transition "
                                   f"{signal.start:.1f}-{signal.end:.1f}s matched sponsor '{sponsor}'")

            elif signal.signal_type in ('volume_increase', 'volume_decrease'):
                # Volume anomalies: require BOTH ad language AND sponsor match
                if self._has_ad_language(text) and self.sponsor_service:
                    sponsor = self.sponsor_service.find_sponsor_in_text(text)
                    if sponsor:
                        new_ads.append(self._signal_to_ad(signal, text, 'audio_enforced'))
                        logger.info(f"[{slug}:{episode_id}] Enforcer: Volume anomaly "
                                   f"{signal.start:.1f}-{signal.end:.1f}s matched sponsor '{sponsor}'")

        # Extend existing ads when signal partially overlaps
        ads = self._extend_overlapping_ads(ads, audio_analysis.signals)

        if new_ads:
            logger.info(f"[{slug}:{episode_id}] Enforcer created {len(new_ads)} new ads "
                       f"from uncovered audio signals")

        return ads + new_ads

    def _find_uncovered_signals(self, ads: List[Dict], signals) -> list:
        """Find signals that don't overlap with any existing ad."""
        uncovered = []
        for signal in signals:
            covered = False
            for ad in ads:
                # Check overlap (with 5s tolerance)
                if signal.start < ad['end'] + 5 and signal.end > ad['start'] - 5:
                    covered = True
                    break
            if not covered:
                uncovered.append(signal)
        return uncovered

    def _get_transcript_text(self, segments: List[Dict], start: float, end: float) -> str:
        """Get transcript text overlapping a time range."""
        texts = []
        for seg in segments:
            if seg['start'] < end and seg['end'] > start:
                texts.append(seg.get('text', ''))
        return ' '.join(texts)

    def _has_ad_language(self, text: str) -> bool:
        """Check if text contains ad-specific language patterns."""
        if not text:
            return False
        for pattern in self.ad_language_patterns:
            if pattern.search(text):
                return True
        return False

    def _signal_to_ad(self, signal, text: str, detection_stage: str) -> Dict:
        """Convert an audio signal to an ad dict."""
        # Extract a snippet for end_text
        words = text.split()
        end_text = ' '.join(words[-10:]) if len(words) > 10 else text

        return {
            'start': signal.start,
            'end': signal.end,
            'confidence': signal.confidence,
            'reason': f'Audio enforced ({signal.signal_type}): uncovered audio signal with ad content',
            'end_text': end_text[:100],
            'detection_stage': detection_stage,
        }

    def _extend_overlapping_ads(self, ads: List[Dict], signals) -> List[Dict]:
        """Extend ad boundaries when a signal partially overlaps.

        If a signal extends beyond an ad's boundaries, widen the ad to cover
        the full signal region. Only extends by up to 30 seconds.
        """
        max_extension = 30.0

        for ad in ads:
            for signal in signals:
                # Check partial overlap
                if signal.start < ad['end'] and signal.end > ad['start']:
                    # Signal starts before ad
                    if signal.start < ad['start']:
                        extension = ad['start'] - signal.start
                        if extension <= max_extension:
                            ad['start'] = signal.start

                    # Signal ends after ad
                    if signal.end > ad['end']:
                        extension = signal.end - ad['end']
                        if extension <= max_extension:
                            ad['end'] = signal.end

        return ads

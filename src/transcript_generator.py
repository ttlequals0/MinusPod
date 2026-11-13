"""VTT transcript generator for Podcasting 2.0 support."""
import logging

from config import SEGMENT_AD_COVERAGE_THRESHOLD
from utils.time import format_vtt_timestamp, adjust_timestamp

logger = logging.getLogger(__name__)


class TranscriptGenerator:
    """Generate WebVTT transcripts from Whisper segments with timestamp adjustment."""

    def _ad_coverage(self, segment: dict, ads_removed: list[dict]) -> float:
        """Fraction of the segment covered by the union of removed-ad overlaps."""
        if not ads_removed:
            return 0.0

        seg_start = segment.get('start', 0)
        seg_end = segment.get('end', 0)
        segment_duration = seg_end - seg_start
        if segment_duration <= 0:
            return 0.0

        # Clip each ad to the segment, then merge the clipped intervals so two
        # cuts overlapping the same slice are not double-counted.
        clipped = []
        for ad in ads_removed:
            s = max(seg_start, ad.get('start', 0))
            e = min(seg_end, ad.get('end', 0))
            if e > s:
                clipped.append((s, e))
        if not clipped:
            return 0.0

        clipped.sort()
        covered = 0.0
        cur_start, cur_end = clipped[0]
        for s, e in clipped[1:]:
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                covered += cur_end - cur_start
                cur_start, cur_end = s, e
        covered += cur_end - cur_start

        return covered / segment_duration

    def _trim_ad_words(self, segment: dict, ads_removed: list[dict]) -> dict | None:
        """Drop the words of a partially-covered segment that sit inside a cut.

        Without this, a kept segment under the coverage threshold leaks the removed
        ad's text. Tests each word midpoint against the raw cut intervals (no merge
        needed: in any interval == in their union).

        Returns the segment unchanged if nothing to trim or no usable word timing,
        a rebuilt segment if words were dropped, or None if none survive.
        """
        words = segment.get('words') or []
        # Key-presence is not enough: the API path can carry JSON nulls as None.
        if not words or any(not isinstance(w.get('start'), (int, float))
                            or not isinstance(w.get('end'), (int, float))
                            for w in words):
            return segment

        kept = [
            w for w in words
            if not any(ad.get('start', 0) < (w['start'] + w['end']) / 2 < ad.get('end', 0)
                       for ad in ads_removed)
        ]
        if len(kept) == len(words):
            # Keep the original text byte-for-byte when nothing was dropped.
            return segment
        if not kept:
            return None

        return {
            'start': kept[0]['start'],
            'end': kept[-1]['end'],
            'text': ''.join(w.get('word', '') for w in kept).strip(),
        }

    def compute_final_segments(
        self,
        segments: list[dict],
        ads_removed: list[dict],
        replacement_duration: float = 0.0,
    ) -> list[dict]:
        """Filter ads and adjust timestamps, returning surviving segments as plain dicts.

        Mirrors generate_vtt/generate_text; the result is persisted as final_segments_json.
        """
        out = []
        for segment in segments:
            coverage = self._ad_coverage(segment, ads_removed)
            if coverage > SEGMENT_AD_COVERAGE_THRESHOLD:
                continue
            if coverage > 0:
                # Partially covered: trim the in-cut words so the removed
                # ad's text does not bleed into the transcript.
                segment = self._trim_ad_words(segment, ads_removed)
                if segment is None:
                    continue
            text = segment.get('text', '').strip()
            if not text:
                continue
            adjusted_start = adjust_timestamp(segment.get('start', 0), ads_removed, replacement_duration)
            adjusted_end = adjust_timestamp(segment.get('end', 0), ads_removed, replacement_duration)
            if adjusted_end <= adjusted_start:
                continue
            out.append({'start': adjusted_start, 'end': adjusted_end, 'text': text})
        return out

    def generate_vtt(
        self,
        segments: list[dict],
        ads_removed: list[dict],
        replacement_duration: float = 0.0,
    ) -> str:
        """Generate VTT transcript with adjusted timestamps.

        Args:
            segments: Whisper segments with start, end, text
            ads_removed: Removed ad markers with start, end
        """
        if not segments:
            logger.warning("No segments provided for VTT generation")
            return "WEBVTT\n"

        lines = ["WEBVTT", ""]

        final = self.compute_final_segments(segments, ads_removed, replacement_duration)
        for seg in final:
            start_ts = format_vtt_timestamp(seg['start'])
            end_ts = format_vtt_timestamp(seg['end'])
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(seg['text'])
            lines.append("")

        logger.info(f"Generated VTT with {len(final)} cues from {len(segments)} segments")

        return "\n".join(lines)

    def generate_text(
        self,
        segments: list[dict],
        ads_removed: list[dict],
        replacement_duration: float = 0.0,
    ) -> str:
        """Generate plain text transcript with adjusted timestamps.

        Format: [HH:MM:SS.sss --> HH:MM:SS.sss] text
        """
        if not segments:
            return ""

        final = self.compute_final_segments(segments, ads_removed, replacement_duration)
        lines = [
            f"[{format_vtt_timestamp(seg['start'])} --> {format_vtt_timestamp(seg['end'])}] {seg['text']}"
            for seg in final
        ]

        logger.info(f"Generated text transcript with {len(lines)} segments")
        return "\n".join(lines)

import { useEffect, useState } from 'react';
import { getEpisodePeaks } from '../../api/feeds';

// 100ms buckets -- 4x fewer peaks than the prior 50ms default. Still
// plenty of detail to see speech vs. silence at any reasonable zoom
// level, and shaves the JSON payload + canvas-render cost on first
// mount roughly 4x. Kept identical to the value AdReviewModal used
// before extraction so behavior is preserved.
const PEAK_RESOLUTION_MS = 100;

export interface UsePeaksResult {
  peaks: number[] | null;
  peakResolutionMs: number;
  peaksError: string | null;
}

// Fetches episode peaks for the given window and exposes the result
// plus the resolution the server actually used (it may coarsen for
// long windows). Re-fetches whenever any of the inputs change. The
// `resetTick` knob lets the host force a fresh fetch even when no
// real input moved -- belt-and-suspenders for the Reset action.
export function usePeaks(
  podcastSlug: string,
  episodeId: string,
  windowStart: number,
  windowEnd: number,
  resetTick: number,
): UsePeaksResult {
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [peakResolutionMs, setPeakResolutionMs] = useState<number>(PEAK_RESOLUTION_MS);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPeaksError(null);
    setPeaks(null);
    getEpisodePeaks(podcastSlug, episodeId, windowStart, windowEnd, PEAK_RESOLUTION_MS)
      .then((res) => {
        if (cancelled) return;
        setPeaks(res.peaks);
        setPeakResolutionMs(res.resolutionMs || PEAK_RESOLUTION_MS);
      })
      .catch((e) => {
        if (!cancelled) setPeaksError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [podcastSlug, episodeId, windowStart, windowEnd, resetTick]);

  return { peaks, peakResolutionMs, peaksError };
}

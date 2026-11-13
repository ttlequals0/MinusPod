import { useMemo } from 'react';

/**
 * Slice the whole-episode peaks to the visible window [windowStart, windowEnd]. Both waveform modals fetch
 * peaks once and slice client-side so only the rendered slice changes on zoom/pan; refetching per window would
 * null `peaks` every tick and flash the waveform.
 */
export function usePeakSlice(
  peaks: number[] | null,
  peakResolutionMs: number,
  windowStart: number,
  windowEnd: number,
): number[] | null {
  return useMemo(() => {
    if (!peaks) return null;
    const bucket = peakResolutionMs / 1000;
    if (!(bucket > 0)) return peaks;
    const startIdx = Math.max(0, Math.floor(windowStart / bucket));
    const endIdx = Math.min(peaks.length, Math.ceil(windowEnd / bucket));
    return endIdx > startIdx ? peaks.slice(startIdx, endIdx) : peaks;
  }, [peaks, peakResolutionMs, windowStart, windowEnd]);
}

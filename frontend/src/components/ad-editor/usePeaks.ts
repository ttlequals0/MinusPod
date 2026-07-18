import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getEpisodePeaks } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';

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

// Fetches episode peaks for the given window and exposes the result plus the
// resolution the server actually used (it may coarsen for long windows).
// Cached via react-query (staleTime Infinity) so remounting the editor for
// the same episode -- e.g. AdReviewModal stepping through an ad queue -- hits
// the cache instead of re-downloading the full-episode peaks. Bumping the
// `resetTick` knob resets every cached ['peaks', slug, episodeId, ...] entry
// (dropping the possibly-bad data instead of stranding it under an old key)
// and refetches fresh (belt-and-suspenders for the Reset action); retry is off
// because apiRequest already retries transient failures, matching the old
// single-fetch behavior.
export function usePeaks(
  podcastSlug: string,
  episodeId: string,
  windowStart: number,
  windowEnd: number,
  resetTick: number,
): UsePeaksResult {
  const queryClient = useQueryClient();

  // Skip the initial mount: only an actual Reset click should blow the cache.
  // resetQueries (not removeQueries) because removal does not notify the
  // mounted observer; reset clears the data of every matching entry and
  // refetches the active one.
  const prevResetTickRef = useRef(resetTick);
  useEffect(() => {
    if (prevResetTickRef.current === resetTick) return;
    prevResetTickRef.current = resetTick;
    queryClient.resetQueries({ queryKey: ['peaks', podcastSlug, episodeId] });
  }, [resetTick, queryClient, podcastSlug, episodeId]);

  const query = useQuery({
    queryKey: ['peaks', podcastSlug, episodeId, windowStart, windowEnd],
    queryFn: () =>
      getEpisodePeaks(podcastSlug, episodeId, windowStart, windowEnd, PEAK_RESOLUTION_MS),
    staleTime: Infinity,
    retry: false,
  });

  return {
    peaks: query.data?.peaks ?? null,
    peakResolutionMs: query.data?.resolutionMs || PEAK_RESOLUTION_MS,
    peaksError: query.error
      ? getErrorMessage(query.error, String(query.error))
      : null,
  };
}

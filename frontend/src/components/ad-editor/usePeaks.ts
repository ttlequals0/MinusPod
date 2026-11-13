import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getEpisodePeaks } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';

// 100ms buckets -- 4x fewer peaks than the prior 50ms default. Still
// plenty of detail to see speech vs. silence at any reasonable zoom
// level, and shaves the JSON payload + canvas-render cost on first
// mount roughly 4x. Kept identical to the value AdReviewModal used
// before extraction so behavior is preserved.
const PEAK_RESOLUTION_MS = 100;

interface UsePeaksResult {
  peaks: number[] | null;
  peakResolutionMs: number;
  peaksError: string | null;
  loading: boolean;
  retry: () => void;
}

// Fetches episode peaks for the given window and exposes the result plus the
// resolution the server actually used (it may coarsen for long windows).
// Cached via react-query (staleTime Infinity) so remounting the editor for
// the same episode -- e.g. AdReviewModal stepping through an ad queue -- hits
// the cache instead of re-downloading the full-episode peaks. Bumping the
// `resetTick` knob resets every cached ['peaks', slug, episodeId, ...] entry
// (dropping the possibly-bad data instead of stranding it under an old key)
// and refetches fresh. A 503 with Retry-After means the waveform is still being
// built: the request waits it out inside apiRequest, so the query stays pending.
// Other failures surface after the client's own retries; react-query retry stays off.
export function usePeaks(
  podcastSlug: string,
  episodeId: string,
  windowStart: number,
  windowEnd: number,
  resetTick: number,
): UsePeaksResult {
  const queryClient = useQueryClient();
  const [retryTick, setRetryTick] = useState(0);
  const retry = useCallback(() => setRetryTick((n) => n + 1), []);
  const tick = resetTick + retryTick;

  // Skip the initial mount: only an actual Reset click should blow the cache.
  // resetQueries (not removeQueries) because removal does not notify the
  // mounted observer; reset clears the data of every matching entry and
  // refetches the active one.
  const prevResetTickRef = useRef(tick);
  useEffect(() => {
    if (prevResetTickRef.current === tick) return;
    prevResetTickRef.current = tick;
    queryClient.resetQueries({ queryKey: ['peaks', podcastSlug, episodeId] });
  }, [tick, queryClient, podcastSlug, episodeId]);

  const query = useQuery({
    queryKey: ['peaks', podcastSlug, episodeId, windowStart, windowEnd],
    queryFn: ({ signal }) =>
      getEpisodePeaks(podcastSlug, episodeId, windowStart, windowEnd, PEAK_RESOLUTION_MS, signal),
    staleTime: Infinity,
    retry: false,
  });

  const peaksError = query.error ? getErrorMessage(query.error, String(query.error)) : null;
  const peaks = query.data?.peaks ?? null;
  return {
    peaks,
    peakResolutionMs: query.data?.resolutionMs || PEAK_RESOLUTION_MS,
    peaksError,
    loading: !peaks && !peaksError,
    retry,
  };
}

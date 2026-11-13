import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getEpisodePeaks } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';

// 100ms buckets: 4x fewer peaks than the prior 50ms default, still enough to see speech vs silence at any
// reasonable zoom, and roughly 4x less JSON payload and canvas-render cost on first mount.
const PEAK_RESOLUTION_MS = 100;

interface UsePeaksResult {
  peaks: number[] | null;
  peakResolutionMs: number;
  peaksError: string | null;
  loading: boolean;
  retry: () => void;
}

// Fetches episode peaks for a window plus the resolution the server used (it may coarsen for long windows).
// Cached via react-query (staleTime Infinity) so remounting the editor for the same episode hits the cache.
// Bumping `resetTick` resets every cached ['peaks', slug, episodeId, ...] entry and refetches, so bad data is
// dropped rather than stranded under an old key. A 503 with Retry-After (waveform still building) is waited
// out inside apiRequest, leaving the query pending; react-query retry stays off.
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

  // Skip the initial mount: only an actual Reset click should blow the cache. resetQueries, not removeQueries,
  // because removal does not notify the mounted observer.
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

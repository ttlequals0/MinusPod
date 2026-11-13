import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getEpisodePeaks } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';

// 100 ms buckets halve the payload compared with 50 ms buckets.
const PEAK_RESOLUTION_MS = 100;

interface UsePeaksResult {
  peaks: number[] | null;
  peakResolutionMs: number;
  peaksError: string | null;
  loading: boolean;
  retry: () => void;
}

// apiRequest waits on 503 Retry-After responses while the waveform is being built.
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

  // resetQueries also notifies mounted editors.
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

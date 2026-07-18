import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { getErrorMessage } from '../api/client';

// Shared claim/poll config for the background cue scans (cross-episode body
// scan and window optimizer). Both endpoints are POST-only and return a
// {status: 'scanning'|'ready'|'error', error?} envelope the client polls every
// 3s until status leaves 'scanning'. rescan() force-refetches via the same
// fetchQuery idiom both panels used.

// status is optional (older servers omit it on some endpoints) and typed as
// string so envelopes with extra states (e.g. cue candidates' 'idle') fit.
interface ScanEnvelope {
  status?: string;
  error?: string;
}

interface UseScanQueryOptions<T extends ScanEnvelope> {
  queryKey: QueryKey;
  queryFn: () => Promise<T>;
  rescanFn: () => Promise<T>;
  enabled?: boolean;
  // Message shown when the poll result carries status='error' but no error text.
  savedErrorFallback: string;
  // Message shown when the query itself threw (e.g. a 409 trigger error). Pass
  // 'message' to surface the thrown error's message, or a literal string.
  thrownError: 'message' | string;
}

interface UseScanQueryResult<T> {
  data: T | undefined;
  scanning: boolean;
  scanError: string | null;
  rescan: () => Promise<T>;
}

export function useScanQuery<T extends ScanEnvelope>({
  queryKey,
  queryFn,
  rescanFn,
  enabled = true,
  savedErrorFallback,
  thrownError,
}: UseScanQueryOptions<T>): UseScanQueryResult<T> {
  const queryClient = useQueryClient();
  const query = useQuery<T>({
    queryKey,
    queryFn,
    enabled,
    staleTime: Infinity,
    refetchInterval: (q) => (q.state.data?.status === 'scanning' ? 3000 : false),
  });

  const data = query.data;
  // enabled already gates this, so no phase check is needed.
  const scanning = query.isLoading || data?.status === 'scanning';
  const scanError = data?.status === 'error'
    ? (data.error || savedErrorFallback)
    : query.error
      ? (thrownError === 'message'
        ? getErrorMessage(query.error, savedErrorFallback)
        : thrownError)
      : null;

  const rescan = () =>
    queryClient.fetchQuery({ queryKey, queryFn: rescanFn, staleTime: 0 });

  return { data, scanning, scanError, rescan };
}

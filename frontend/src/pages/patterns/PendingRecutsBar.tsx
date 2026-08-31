import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { applyPendingRecuts, getPendingRecuts } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { btnPrimary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { useState } from 'react';

// Review is bulk work: decisions are recorded as they are made and cut in one
// pass per episode when the operator applies them, so an episode edited five
// times is re-cut once rather than five times.
export function PendingRecutsBar() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ['pending-recuts'],
    queryFn: getPendingRecuts,
  });

  const apply = useMutation({
    mutationFn: applyPendingRecuts,
    onSuccess: ({ queued, skipped }) => {
      setError(null);
      // Skipped episodes keep their decisions and stay listed, so say so
      // rather than leaving a button that looks like it did nothing.
      setResult(
        queued === 0
          ? `Nothing could be recut. ${skipped} ${skipped === 1 ? 'episode no longer has' : 'episodes no longer have'} the original audio to rebuild from.`
          : `Recutting ${queued} ${queued === 1 ? 'episode' : 'episodes'}.${
            skipped ? ` ${skipped} skipped for missing original audio.` : ''}`,
      );
      queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
      queryClient.invalidateQueries({ queryKey: ['detections'] });
    },
    onError: (e: unknown) => {
      setResult(null);
      setError(getErrorMessage(e, 'Could not start the recuts.'));
    },
  });

  const count = data?.count ?? 0;
  if (!count) return null;

  return (
    <div className="mb-3 rounded-lg border border-border bg-secondary/40 px-3 py-2 flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm text-foreground">
        {count === 1
          ? 'Your decisions are saved, but 1 episode still plays its old audio.'
          : `Your decisions are saved, but ${count} episodes still play their old audio.`}
        <span className="block text-xs text-muted-foreground mt-0.5">
          Recutting rebuilds them from the current markers, once per episode.
        </span>
        {result && (
          <span className="block text-xs mt-1 text-foreground" role="status">{result}</span>
        )}
        {error && <span className="block text-destructive text-xs mt-1">{error}</span>}
      </div>
      <button
        type="button"
        onClick={() => apply.mutate()}
        disabled={apply.isPending}
        className={`px-4 py-2 rounded-lg text-sm ${btnPrimary} disabled:opacity-50 transition-colors ${focusRing}`}
      >
        {apply.isPending
          ? 'Starting...'
          : `Apply recuts (${count})`}
      </button>
    </div>
  );
}

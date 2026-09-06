import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { applyPendingRecuts, getPendingRecuts } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { btnPrimary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { useState } from 'react';

// Review is bulk work: decisions are recorded as they are made and cut in one
// pass per episode when the operator applies them, so an episode edited five
// times is re-cut once rather than five times.
interface PendingRecutsBarProps {
  /** Scope to one feed. Omitted on the review pages, which cover every feed. */
  slug?: string;
}

export function PendingRecutsBar({ slug }: PendingRecutsBarProps) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const { data } = useQuery({
    // Scoped and global bars must not share a cache entry.
    queryKey: ['pending-recuts', slug ?? 'all'],
    queryFn: () => getPendingRecuts(slug),
    // An episode keeps its stamp until its recut finishes, so poll to clear
    // the bar on its own rather than leaving a queued state on screen.
    refetchInterval: 15000,
  });

  const apply = useMutation({
    mutationFn: () => applyPendingRecuts(slug),
    onSuccess: ({ queued, skipped }) => {
      setError(null);
      // Skipped episodes keep their decisions and stay listed, so say so
      // rather than leaving a button that looks like it did nothing.
      setResult(
        queued === 0
          ? `Nothing could be recut. ${skipped} ${skipped === 1 ? 'episode is' : 'episodes are'} already queued to run or missing what a recut needs. Your decisions are kept.`
          : `Recutting ${queued} ${queued === 1 ? 'episode' : 'episodes'}.${
            skipped ? ` ${skipped} skipped: already queued to run, or missing what a recut needs.` : ''}`,
      );
      queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
      queryClient.invalidateQueries({ queryKey: ['detections'] });
    },
    onError: (e: unknown) => {
      setResult(null);
      setError(getErrorMessage(e, 'Could not start the recuts.'));
    },
  });

  // The server's flags drive the whole bar: a row an apply would queue now
  // (ready), one whose own run is underway or queued (inFlight), or one
  // missing what a recut needs (blocked). Applying refetches, so the rows
  // flip to inFlight on their own and no client-side batch tracking is
  // needed; a decision arriving mid-batch simply shows as a new ready row.
  const episodes = data?.episodes ?? [];
  const count = data?.count ?? 0;
  const ready = episodes.filter((e) => e.recutReady).length;
  const inFlight = episodes.filter((e) => e.inFlight).length;
  const blocked = count - ready - inFlight;
  const recuttingOnly = ready === 0 && inFlight > 0;
  if (!count) return null;

  return (
    <div className="mb-3 rounded-lg border border-border bg-secondary/40 px-3 py-2 flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm text-foreground">
        {recuttingOnly
          ? 'Recutting now. This panel clears as each episode finishes.'
          : count === 1
            ? 'Your decisions are saved, but 1 episode still plays its old audio.'
            : `Your decisions are saved, but ${count} episodes still play their old audio.`}
        {!recuttingOnly && (
          <span className="block text-xs text-muted-foreground mt-0.5">
            Recutting rebuilds them from the current markers, once per episode.
          </span>
        )}
        {!recuttingOnly && inFlight > 0 && (
          <span className="block text-xs text-muted-foreground mt-0.5">
            {inFlight === 1 ? '1 of them is' : `${inFlight} of them are`} being
            rebuilt right now.
          </span>
        )}
        {blocked > 0 && (
          <span className="block text-xs text-muted-foreground mt-0.5">
            {blocked === 1 ? '1 of them is' : `${blocked} of them are`} missing
            the retained audio or saved transcript a recut needs; those
            decisions apply on the next full reprocess.
          </span>
        )}
        {result && (
          <span className="block text-xs mt-1 text-foreground" role="status">{result}</span>
        )}
        {error && <span className="block text-destructive text-xs mt-1">{error}</span>}
      </div>
      <button
        type="button"
        onClick={() => apply.mutate()}
        disabled={apply.isPending || ready === 0}
        className={`px-4 py-2 rounded-lg text-sm ${btnPrimary} disabled:opacity-50 transition-colors ${focusRing}`}
      >
        {apply.isPending ? 'Starting...'
          : recuttingOnly ? `Recutting ${inFlight}...`
            : `Apply recuts (${ready})`}
      </button>
    </div>
  );
}

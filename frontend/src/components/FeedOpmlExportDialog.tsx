import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { feedsQueryOptions } from '../api/feeds';
import { exportOpml } from '../api/settings';
import { getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import Checkbox from './Checkbox';
import { focusRing } from './fieldStyles';
import { btnOutline, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { feedDisplayTitle } from '../utils/feedTitle';
import { Skeleton } from './Skeleton';

interface Props {
  open: boolean;
  onClose: () => void;
}

function FeedOpmlExportDialogImpl({ onClose }: Omit<Props, 'open'>) {
  const { data, error: feedsError, isError: feedsFailed, isFetching, isLoading, refetch } = useQuery({
    ...feedsQueryOptions,
    select: (r) => r.feeds,
  });
  const feeds = useMemo(() => data ?? [], [data]);
  // null means every feed, including feeds returned by a later refresh.
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const feedSlugs = useMemo(() => new Set(feeds.map((f) => f.slug)), [feeds]);
  const effective = useMemo(
    () => selected ? new Set([...selected].filter((slug) => feedSlugs.has(slug))) : feedSlugs,
    [selected, feedSlugs],
  );
  const allSelected = feeds.length > 0 && feeds.every((f) => effective.has(f.slug));

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(feeds.map((f) => f.slug)));
  }

  function toggleOne(slug: string) {
    const next = new Set(effective);
    if (next.has(slug)) next.delete(slug);
    else next.add(slug);
    setSelected(next);
  }

  async function download() {
    if (effective.size === 0 || busy) return;
    setBusy(true);
    setError('');
    try {
      await exportOpml('modified', allSelected ? undefined : Array.from(effective));
      onClose();
    } catch (e) {
      setError(getErrorMessage(e, 'Export failed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      onClose={onClose}
      closeOnBackdrop
      ariaLabelledBy="feed-opml-export-title"
      panelClassName="w-full max-w-lg max-h-[80vh] flex flex-col text-card-foreground"
    >
      <div className="p-6 pb-3 border-b border-border">
        <h2 id="feed-opml-export-title" className="text-lg font-semibold mb-1">Export OPML</h2>
        <p className="text-sm text-muted-foreground">
          Select feeds to export as ad-free subscription URLs.
        </p>
      </div>

      <div className="px-6 py-2 border-b border-border flex items-center justify-between text-sm">
        <Checkbox
          checked={allSelected}
          onChange={toggleAll}
          disabled={feeds.length === 0}
          label={allSelected ? 'Deselect all' : 'Select all'}
          labelClassName=""
          className={touchTarget}
        />
        <span className="text-xs text-muted-foreground">
          {effective.size} of {feeds.length} selected
        </span>
      </div>

      <div className="flex-1 overflow-y-auto overscroll-contain p-3">
        {isLoading && (
          <div role="status" aria-busy="true" aria-label="Loading feeds" className="space-y-3 py-4">
            <Skeleton className="h-11 rounded-lg" />
            <Skeleton className="h-11 rounded-lg" />
            <Skeleton className="h-11 rounded-lg" />
          </div>
        )}
        {feedsFailed && feeds.length === 0 && (
          <div className="space-y-3 rounded-lg bg-destructive/10 p-3">
            <p className="text-sm text-destructive">{getErrorMessage(feedsError, 'Failed to load feeds')}</p>
            <button
              type="button"
              onClick={() => void refetch()}
              disabled={isFetching}
              className={`${touchTarget} px-3 py-1.5 text-sm rounded ${btnSecondary} transition-colors disabled:opacity-50 ${focusRing}`}
            >
              {isFetching ? 'Loading...' : 'Retry'}
            </button>
          </div>
        )}
        {feedsFailed && feeds.length > 0 && (
          <div className="mb-3 space-y-1 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
            <p>{getErrorMessage(feedsError, 'Could not refresh feeds')}. Showing the last loaded list.</p>
            <button
              type="button"
              onClick={() => void refetch()}
              disabled={isFetching}
              className={`${touchTarget} px-3 py-1.5 text-sm rounded ${btnSecondary} transition-colors disabled:opacity-50 ${focusRing}`}
            >
              {isFetching ? 'Refreshing...' : 'Retry'}
            </button>
          </div>
        )}
        {!isLoading && !feedsFailed && feeds.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">No feeds to export.</p>
        )}
        <ul className="space-y-1">
          {feeds.map((f) => {
            const title = feedDisplayTitle(f) || f.slug;
            return (
              <li key={f.slug}>
                <label
                  htmlFor={`opml-export-${f.slug}`}
                  className="flex min-h-11 items-center gap-2 px-2 py-2 rounded hover:bg-accent/50 cursor-pointer sm:min-h-0"
                >
                  <Checkbox
                    id={`opml-export-${f.slug}`}
                    checked={effective.has(f.slug)}
                    onChange={() => toggleOne(f.slug)}
                    ariaLabel={`Include ${title}`}
                    className="shrink-0"
                  />
                  <span className="min-w-0 text-sm break-words sm:truncate">{title}</span>
                </label>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="p-6 pt-3 border-t border-border space-y-3">
        {error && <p className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className={`${touchTarget} px-3 py-1.5 text-sm rounded ${btnOutline} transition-colors disabled:opacity-50 ${focusRing}`}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={download}
            disabled={effective.size === 0 || busy}
            className={`${touchTarget} px-3 py-1.5 text-sm rounded ${btnPrimary} transition-colors disabled:opacity-50 ${focusRing}`}
          >
            {busy ? 'Exporting...' : `Download ${effective.size} feed${effective.size === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function FeedOpmlExportDialog({ open, onClose }: Props) {
  if (!open) return null;
  return <FeedOpmlExportDialogImpl onClose={onClose} />;
}

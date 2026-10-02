import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { feedsQueryOptions } from '../api/feeds';
import { exportOpml } from '../api/settings';
import { getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import Checkbox from './Checkbox';
import { focusRing } from './fieldStyles';
import { btnOutline, btnPrimary } from './buttonStyles';

interface Props {
  open: boolean;
  onClose: () => void;
}

// Picks which feeds go into a modified-feed OPML, the file a podcast app
// imports. Every feed starts selected; exporting all of them sends no slugs
// so the request matches the Settings > Data Management export.
function FeedOpmlExportDialogImpl({ onClose }: Omit<Props, 'open'>) {
  const { data, isLoading } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const feeds = useMemo(() => data ?? [], [data]);
  // null = untouched, which means every feed (including ones still loading).
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const effective = useMemo(
    () => selected ?? new Set(feeds.map((f) => f.slug)),
    [selected, feeds],
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
      panelClassName="w-full max-w-lg max-h-[80vh] flex flex-col text-card-foreground"
    >
      <div className="p-6 pb-3 border-b border-border">
        <h2 className="text-lg font-semibold mb-1">Export OPML</h2>
        <p className="text-sm text-muted-foreground">
          Pick the feeds to include. The file uses MinusPod&apos;s ad-free feed URLs, ready to import into a podcast app.
        </p>
      </div>

      <div className="px-6 py-2 border-b border-border flex items-center justify-between text-sm">
        <Checkbox
          checked={allSelected}
          onChange={toggleAll}
          disabled={feeds.length === 0}
          label={allSelected ? 'Deselect all' : 'Select all'}
          labelClassName=""
        />
        <span className="text-xs text-muted-foreground">
          {effective.size} of {feeds.length} selected
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground text-center py-8">Loading feeds...</p>
        )}
        {!isLoading && feeds.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">No feeds to export.</p>
        )}
        <ul className="space-y-1">
          {feeds.map((f) => (
            <li key={f.slug}>
              <label
                htmlFor={`opml-export-${f.slug}`}
                className="flex items-center gap-2 px-2 py-2 rounded hover:bg-accent/50 cursor-pointer"
              >
                <Checkbox
                  id={`opml-export-${f.slug}`}
                  checked={effective.has(f.slug)}
                  onChange={() => toggleOne(f.slug)}
                  ariaLabel={`Include ${f.title || f.slug}`}
                />
                <span className="text-sm truncate">{f.title || f.slug}</span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      <div className="p-6 pt-3 border-t border-border space-y-3">
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className={`px-3 py-1.5 text-sm rounded ${btnOutline} transition-colors disabled:opacity-50 ${focusRing}`}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={download}
            disabled={effective.size === 0 || busy}
            className={`px-3 py-1.5 text-sm rounded ${btnPrimary} transition-colors disabled:opacity-50 ${focusRing}`}
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

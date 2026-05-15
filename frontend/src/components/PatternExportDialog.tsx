import { useMemo, useState } from 'react';
import type { AdPattern } from '../api/patterns';

interface Props {
  open: boolean;
  patterns: AdPattern[];
  onClose: () => void;
}

function PatternExportDialogImpl({ patterns, onClose }: Omit<Props, 'open'>) {
  // Initial selection = every pattern in the current filter. The parent
  // remounts this component each open (see key= below), so useState
  // initializer runs fresh and stays in sync with the filter.
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(patterns.map((p) => p.id)),
  );
  const [includeDisabled, setIncludeDisabled] = useState(false);
  const [includeCorrections, setIncludeCorrections] = useState(false);

  const allSelected = useMemo(
    () => patterns.length > 0 && patterns.every((p) => selected.has(p.id)),
    [patterns, selected],
  );

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(patterns.map((p) => p.id)));
  }

  function toggleOne(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function exportSelected() {
    if (selected.size === 0) return;
    const params = new URLSearchParams();
    params.set('ids', Array.from(selected).join(','));
    if (includeDisabled) params.set('include_disabled', 'true');
    if (includeCorrections) params.set('include_corrections', 'true');
    const url = `/api/v1/patterns/export?${params.toString()}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = 'minuspod-patterns.json';
    a.click();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-lg bg-white dark:bg-slate-900 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 pb-3 border-b border-border">
          <h2 className="text-lg font-semibold mb-1">Export patterns</h2>
          <p className="text-sm text-muted-foreground">
            Pick the patterns to include. The download is a JSON file you can import back into another MinusPod instance.
          </p>
        </div>

        <div className="px-6 py-2 border-b border-border flex items-center justify-between text-sm">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              className="rounded"
            />
            <span>{allSelected ? 'Deselect all' : 'Select all'}</span>
          </label>
          <span className="text-xs text-muted-foreground">
            {selected.size} of {patterns.length} selected
          </span>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {patterns.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-8">
              No patterns match the current filters.
            </p>
          )}
          <ul className="space-y-1">
            {patterns.map((p) => (
              <li key={p.id}>
                <label className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-accent/50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selected.has(p.id)}
                    onChange={() => toggleOne(p.id)}
                    className="mt-0.5 rounded"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-xs text-muted-foreground">#{p.id}</span>
                      <span className="font-medium">{p.sponsor || '(unknown sponsor)'}</span>
                      <span className="text-xs text-muted-foreground">[{p.scope}]</span>
                      {p.source === 'community' && (
                        <span className="text-xs text-teal-700 dark:text-teal-400">community</span>
                      )}
                    </div>
                    {p.text_template && (
                      <div className="text-xs text-muted-foreground truncate">
                        {p.text_template.substring(0, 100)}…
                      </div>
                    )}
                  </div>
                </label>
              </li>
            ))}
          </ul>
        </div>

        <div className="p-6 pt-3 border-t border-border space-y-3">
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={includeDisabled}
                onChange={(e) => setIncludeDisabled(e.target.checked)}
                className="rounded"
              />
              <span>Include disabled patterns</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={includeCorrections}
                onChange={(e) => setIncludeCorrections(e.target.checked)}
                className="rounded"
              />
              <span>Include correction history</span>
            </label>
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded border border-border"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={exportSelected}
              disabled={selected.size === 0}
              className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
            >
              Export {selected.size} pattern{selected.size === 1 ? '' : 's'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function PatternExportDialog({ open, patterns, onClose }: Props) {
  if (!open) return null;
  return <PatternExportDialogImpl patterns={patterns} onClose={onClose} />;
}

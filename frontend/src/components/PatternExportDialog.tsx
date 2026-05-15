import { useMemo, useState } from 'react';
import type { AdPattern } from '../api/patterns';
import { PATTERN_SOURCE_COMMUNITY, submitPatternToCommunity } from '../api/patterns';
import { downloadBlob } from '../api/history';

interface Props {
  open: boolean;
  patterns: AdPattern[];
  onClose: () => void;
}

type Destination = 'download' | 'community';

function PatternExportDialogImpl({ patterns, onClose }: Omit<Props, 'open'>) {
  const [destination, setDestination] = useState<Destination>('download');
  // Initial selection = every pattern in the current filter. The parent
  // remounts this component each open (see key= below), so useState
  // initializer runs fresh and stays in sync with the filter.
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(patterns.map((p) => p.id)),
  );
  const [includeDisabled, setIncludeDisabled] = useState(false);
  const [includeCorrections, setIncludeCorrections] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const busy = progress !== null;

  // Re-sharing community-sourced patterns just round-trips them; filter them
  // out of the eligible set. Local and imported are both eligible.
  const visiblePatterns = useMemo(
    () => destination === 'community'
      ? patterns.filter((p) => p.source !== PATTERN_SOURCE_COMMUNITY)
      : patterns,
    [patterns, destination],
  );

  const effectiveSelection = useMemo(() => {
    const visibleIds = new Set(visiblePatterns.map((p) => p.id));
    return new Set(Array.from(selected).filter((id) => visibleIds.has(id)));
  }, [selected, visiblePatterns]);

  const allSelected = useMemo(
    () => visiblePatterns.length > 0
      && visiblePatterns.every((p) => effectiveSelection.has(p.id)),
    [visiblePatterns, effectiveSelection],
  );

  function toggleAll() {
    if (allSelected) {
      const visibleIds = new Set(visiblePatterns.map((p) => p.id));
      setSelected((prev) => new Set(Array.from(prev).filter((id) => !visibleIds.has(id))));
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        for (const p of visiblePatterns) next.add(p.id);
        return next;
      });
    }
  }

  function toggleOne(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function downloadSelected() {
    if (effectiveSelection.size === 0) return;
    const params = new URLSearchParams();
    params.set('ids', Array.from(effectiveSelection).join(','));
    if (includeDisabled) params.set('include_disabled', 'true');
    if (includeCorrections) params.set('include_corrections', 'true');
    const url = `/api/v1/patterns/export?${params.toString()}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = 'minuspod-patterns.json';
    a.click();
    onClose();
  }

  async function submitSelectedToCommunity() {
    if (effectiveSelection.size === 0 || busy) return;
    const ids = Array.from(effectiveSelection);
    // Open all blank tabs synchronously inside the click handler so the
    // browser's popup blocker counts them as user-initiated. Each tab is
    // redirected to its real PR URL once the API call returns. Tabs whose
    // submit fails or returns too-large get closed.
    const tabs: (Window | null)[] = ids.map(() => window.open('about:blank', '_blank'));
    setProgress({ done: 0, total: ids.length });
    const failures: { id: number; msg: string }[] = [];
    let opened = 0;
    let downloaded = 0;
    let blocked = 0;
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      const tab = tabs[i];
      try {
        const result = await submitPatternToCommunity(id);
        if (result.too_large) {
          tab?.close();
          const blob = new Blob([JSON.stringify(result.payload, null, 2)], {
            type: 'application/json',
          });
          downloadBlob(blob, result.filename);
          downloaded++;
        } else if (tab) {
          tab.location.href = result.pr_url;
          opened++;
        } else {
          blocked++;
        }
      } catch (e) {
        tab?.close();
        failures.push({ id, msg: e instanceof Error ? e.message : 'submit failed' });
      }
      setProgress({ done: i + 1, total: ids.length });
    }
    setProgress(null);
    const lines: string[] = [];
    if (opened) lines.push(`Opened ${opened} PR tab${opened === 1 ? '' : 's'}.`);
    if (downloaded) lines.push(`${downloaded} pattern${downloaded === 1 ? '' : 's'} too large for prefilled URL; downloaded as JSON.`);
    if (blocked) lines.push(`${blocked} tab${blocked === 1 ? ' was' : 's were'} blocked by your browser. Allow popups from this site to share more than one pattern at a time.`);
    if (failures.length) {
      lines.push(`${failures.length} failed: ${failures.slice(0, 3).map((f) => `#${f.id} (${f.msg})`).join(', ')}${failures.length > 3 ? '...' : ''}`);
    }
    if (lines.length) alert(lines.join('\n'));
    if (failures.length === 0 && blocked === 0) onClose();
  }

  // Guard against closing the dialog mid-submit: the in-flight loop would
  // otherwise keep firing setState/window.open on an unmounted component.
  function handleClose() {
    if (busy) return;
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={handleClose}>
      <div
        className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-lg bg-white dark:bg-slate-900 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 pb-3 border-b border-border">
          <h2 className="text-lg font-semibold mb-1">Export patterns</h2>
          <p className="text-sm text-muted-foreground">
            Pick the patterns to include, then choose what to do with them.
          </p>
        </div>

        <div className="px-6 py-3 border-b border-border space-y-2 text-sm">
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              name="export-destination"
              checked={destination === 'download'}
              onChange={() => setDestination('download')}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Download as JSON</span>
              <span className="block text-xs text-muted-foreground">
                One bundle file you can import into another MinusPod instance.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              name="export-destination"
              checked={destination === 'community'}
              onChange={() => setDestination('community')}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Submit to community</span>
              <span className="block text-xs text-muted-foreground">
                Open one prefilled GitHub PR per selected pattern. Community patterns are excluded automatically.
              </span>
            </span>
          </label>
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
            {effectiveSelection.size} of {visiblePatterns.length} selected
          </span>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {visiblePatterns.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-8">
              {destination === 'community'
                ? 'Nothing to submit. Community patterns are excluded; only local or imported patterns can be shared.'
                : 'No patterns match the current filters.'}
            </p>
          )}
          <ul className="space-y-1">
            {visiblePatterns.map((p) => (
              <li key={p.id}>
                <label className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-accent/50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={effectiveSelection.has(p.id)}
                    onChange={() => toggleOne(p.id)}
                    className="mt-0.5 rounded"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-xs text-muted-foreground">#{p.id}</span>
                      <span className="font-medium">{p.sponsor || '(unknown sponsor)'}</span>
                      <span className="text-xs text-muted-foreground">[{p.scope}]</span>
                      {p.source === PATTERN_SOURCE_COMMUNITY && (
                        <span className="text-xs text-teal-700 dark:text-teal-400">community</span>
                      )}
                    </div>
                    {p.text_template && (
                      <div className="text-xs text-muted-foreground truncate">
                        {p.text_template.substring(0, 100)}...
                      </div>
                    )}
                  </div>
                </label>
              </li>
            ))}
          </ul>
        </div>

        <div className="p-6 pt-3 border-t border-border space-y-3">
          {destination === 'download' && (
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
          )}

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={handleClose}
              disabled={busy}
              className="px-3 py-1.5 text-sm rounded border border-border disabled:opacity-50"
            >
              Cancel
            </button>
            {destination === 'download' ? (
              <button
                type="button"
                onClick={downloadSelected}
                disabled={effectiveSelection.size === 0}
                className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
              >
                Export {effectiveSelection.size} pattern{effectiveSelection.size === 1 ? '' : 's'}
              </button>
            ) : (
              <button
                type="button"
                onClick={submitSelectedToCommunity}
                disabled={effectiveSelection.size === 0 || busy}
                className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
              >
                {progress
                  ? `Submitting ${progress.done} of ${progress.total}...`
                  : `Submit ${effectiveSelection.size} pattern${effectiveSelection.size === 1 ? '' : 's'}`}
              </button>
            )}
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

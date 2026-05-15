import { useMemo, useState } from 'react';
import type { AdPattern, BundlePreview } from '../api/patterns';
import {
  PATTERN_SOURCE_COMMUNITY,
  previewExportBundle,
  downloadCommunityBundle,
} from '../api/patterns';

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
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [downloadedFilename, setDownloadedFilename] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Stage is fully derivable from the two artifacts; making it a separate
  // useState would let it drift out of sync (preview=null but stage='preview').
  const stage: 'pick' | 'preview' | 'done' = downloadedFilename
    ? 'done'
    : preview
      ? 'preview'
      : 'pick';

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

  function handleClose() {
    if (busy) return;
    onClose();
  }

  function changeDestination(d: Destination) {
    setDestination(d);
    setPreview(null);
    setDownloadedFilename(null);
    setError(null);
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

  async function runPreview() {
    if (effectiveSelection.size === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await previewExportBundle(Array.from(effectiveSelection));
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setBusy(false);
    }
  }

  async function downloadBundle() {
    if (!preview || preview.ready_count === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { filename } = await downloadCommunityBundle(preview.ready);
      setDownloadedFilename(filename);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Download failed');
    } finally {
      setBusy(false);
    }
  }

  const totalEligible = visiblePatterns.length;

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
              onChange={() => changeDestination('download')}
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
              onChange={() => changeDestination('community')}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Submit to community</span>
              <span className="block text-xs text-muted-foreground">
                Build one bundle file with everything that passes the quality gates.
                Commit it into a fork and open one PR. Community patterns are excluded automatically.
              </span>
            </span>
          </label>
        </div>

        {destination === 'community' && stage === 'preview' && preview && (
          <CommunityPreview
            preview={preview}
            onBack={() => setPreview(null)}
            onDownload={downloadBundle}
            busy={busy}
          />
        )}

        {destination === 'community' && stage === 'done' && downloadedFilename && (
          <CommunityDone filename={downloadedFilename} onClose={onClose} />
        )}

        {(destination === 'download' || stage === 'pick') && (
          <>
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
                {effectiveSelection.size} of {totalEligible} selected
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

              {error && (
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
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
                    onClick={runPreview}
                    disabled={effectiveSelection.size === 0 || busy}
                    className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
                  >
                    {busy ? 'Checking...' : 'Continue'}
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CommunityPreview({
  preview, onBack, onDownload, busy,
}: {
  preview: BundlePreview;
  onBack: () => void;
  onDownload: () => void;
  busy: boolean;
}) {
  const { ready_count, rejected_count, rejected } = preview;
  return (
    <>
      <div className="px-6 py-3 border-b border-border text-sm">
        <p>
          <span className="font-medium">{ready_count}</span> ready to submit,{' '}
          <span className="font-medium">{rejected_count}</span> will be rejected.
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {rejected.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            Every selected pattern passes the quality gates.
          </p>
        ) : (
          <details open className="text-sm">
            <summary className="cursor-pointer font-medium mb-2">
              Rejected patterns ({rejected.length})
            </summary>
            <ul className="space-y-2 pl-3">
              {rejected.map((r) => (
                <li key={r.id} className="border-l-2 border-red-300 dark:border-red-700 pl-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground">#{r.id}</span>
                    <span className="font-medium">{r.sponsor || '(unknown sponsor)'}</span>
                  </div>
                  <ul className="text-xs text-muted-foreground list-disc pl-5">
                    {r.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
                  </ul>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      <div className="p-6 pt-3 border-t border-border flex justify-end gap-2">
        <button
          type="button"
          onClick={onBack}
          disabled={busy}
          className="px-3 py-1.5 text-sm rounded border border-border disabled:opacity-50"
        >
          Back
        </button>
        <button
          type="button"
          onClick={onDownload}
          disabled={ready_count === 0 || busy}
          className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
        >
          {busy ? 'Building bundle...' : `Download bundle (${ready_count})`}
        </button>
      </div>
    </>
  );
}

function CommunityDone({ filename, onClose }: { filename: string; onClose: () => void }) {
  const snippet = [
    '# 1. Fork ttlequals0/MinusPod and clone your fork',
    '# 2. Drop the file into patterns/community/, then:',
    `mv ~/Downloads/${filename} patterns/community/`,
    'git checkout -b community-submission',
    `git add patterns/community/${filename}`,
    'git commit -m "Submit community ad patterns"',
    'git push -u origin community-submission',
    'gh pr create --fill --label pattern',
  ].join('\n');
  return (
    <>
      <div className="px-6 py-3 border-b border-border space-y-2">
        <p className="text-sm">
          Bundle downloaded as <code className="font-mono text-xs">{filename}</code>. Open a PR with it
          via your usual git flow, or copy the commands below:
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        <pre className="text-xs font-mono bg-muted p-3 rounded whitespace-pre-wrap">{snippet}</pre>
      </div>
      <div className="p-6 pt-3 border-t border-border flex justify-end">
        <button
          type="button"
          onClick={onClose}
          className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white"
        >
          Done
        </button>
      </div>
    </>
  );
}

export function PatternExportDialog({ open, patterns, onClose }: Props) {
  if (!open) return null;
  return <PatternExportDialogImpl patterns={patterns} onClose={onClose} />;
}

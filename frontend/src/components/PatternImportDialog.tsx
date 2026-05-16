import { useRef, useState } from 'react';
import { apiRequest } from '../api/client';

interface Props {
  open: boolean;
  onClose: () => void;
  onComplete: () => void;
}

type ImportMode = 'merge' | 'replace' | 'supplement';

export function PatternImportDialog({ open, onClose, onComplete }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<ImportMode>('supplement');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    importedCount?: number;
    updatedCount?: number;
    skippedCount?: number;
    error?: string;
  } | null>(null);

  if (!open) return null;

  async function handleImport() {
    setResult(null);
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setResult({ error: 'Please pick a JSON file first.' });
      return;
    }
    setBusy(true);
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const body =
        Array.isArray(parsed)
          ? { patterns: parsed, mode }
          : Array.isArray(parsed?.patterns)
            ? { ...parsed, mode }
            : { patterns: [parsed], mode };
      const res = await apiRequest<{
        importedCount: number;
        updatedCount: number;
        skippedCount: number;
      }>('/patterns/import', { method: 'POST', body });
      setResult(res);
      onComplete();
    } catch (e) {
      setResult({ error: e instanceof Error ? e.message : 'Import failed' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-lg bg-white dark:bg-slate-900 p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-3">Import patterns</h2>
        <p className="text-sm text-slate-500 mb-4">
          Upload a JSON file exported from MinusPod, or a single community-pattern JSON.
        </p>

        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="block w-full mb-4 text-sm"
        />

        <div className="mb-4">
          <label className="block text-sm font-medium mb-1">Mode</label>
          <select
            className="w-full rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value as ImportMode)}
            disabled={busy}
          >
            <option value="supplement">Supplement -- add only new patterns</option>
            <option value="merge">Merge -- update existing, add new</option>
            <option value="replace">Replace -- wipe all then import</option>
          </select>
        </div>

        {result && (
          <div className="text-sm mb-4">
            {result.error ? (
              <p className="text-red-600 dark:text-red-400">{result.error}</p>
            ) : (
              <p>
                Imported {result.importedCount ?? 0}, updated {result.updatedCount ?? 0},
                skipped {result.skippedCount ?? 0}.
              </p>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded border border-slate-300 dark:border-slate-700"
            disabled={busy}
          >
            Close
          </button>
          <button
            type="button"
            onClick={handleImport}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
          >
            {busy ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}

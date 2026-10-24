import { useRef, useState } from 'react';
import { apiRequest, getErrorMessage } from '../api/client';
import { Modal, ConfirmModal, dialogFooterBtn } from './Modal';
import { btnOutline, btnPrimary } from './buttonStyles';
import { selectBase } from './fieldStyles';

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
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [result, setResult] = useState<{
    importedCount?: number;
    updatedCount?: number;
    skippedCount?: number;
    error?: string;
  } | null>(null);

  if (!open) return null;

  async function handleImport(confirmed = false) {
    setResult(null);
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setResult({ error: 'Please pick a JSON file first.' });
      return;
    }
    if (mode === 'replace' && !confirmed) { setConfirmReplace(true); return; }
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
      setConfirmReplace(false);
      onComplete();
    } catch (e) {
      setResult({ error: getErrorMessage(e, 'Import failed') });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal ariaLabel="Import patterns" onClose={onClose} closeOnEscape={!busy} panelClassName="w-full max-w-md p-6 text-card-foreground">
      <h2 className="text-lg font-semibold mb-3">Import patterns</h2>
      <p id="pattern-import-file-help" className="text-sm text-muted-foreground mb-4">
        Upload a JSON file exported from MinusPod, or a single community-pattern JSON.
      </p>

      <label htmlFor="pattern-import-file" className="block text-sm font-medium mb-2">Pattern JSON file</label>
      <input
        id="pattern-import-file"
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        disabled={busy}
        className="block w-full mb-4 text-sm py-2"
        aria-describedby="pattern-import-file-help"
      />

      <div className="mb-4">
        <label htmlFor="pattern-import-mode" className="block text-sm font-medium mb-1">Mode</label>
        <select
          id="pattern-import-mode"
          className={`w-full ${selectBase}`}
          value={mode}
          onChange={(e) => setMode(e.target.value as ImportMode)}
          disabled={busy}
        >
          <option value="supplement">Supplement: add only new patterns</option>
          <option value="merge">Merge: update existing, add new</option>
          <option value="replace">Replace: delete all patterns, then import</option>
        </select>
      </div>

      {result && (
        <div role={result.error ? 'alert' : 'status'} className="text-sm mb-4">
          {result.error ? (
            <p className="text-destructive">{result.error}</p>
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
          className={`${dialogFooterBtn} ${btnOutline}`}
          disabled={busy}
        >
          Close
        </button>
        <button
          type="button"
          onClick={() => void handleImport()}
          disabled={busy}
          className={`${dialogFooterBtn} ${btnPrimary}`}
        >
          {busy ? 'Importing...' : 'Import'}
        </button>
      </div>
      {confirmReplace && <ConfirmModal title="Replace all patterns?" confirmLabel="Replace patterns" busyLabel="Importing..." pending={busy}
        onCancel={() => setConfirmReplace(false)} onConfirm={() => void handleImport(true)}>
        <p>This deletes the existing pattern catalog and imports the selected file.</p>
        {result?.error && <p role="alert" className="text-destructive">{result.error}</p>}
      </ConfirmModal>}
    </Modal>
  );
}

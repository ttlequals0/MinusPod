import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import {
  getDatabaseBackupSettings,
  updateDatabaseBackupSettings,
  runDatabaseBackupNow,
} from '../../api/settings';
import { formatStorage } from './settingsUtils';

interface Draft {
  enabled?: boolean;
  cron?: string;
  dest?: string;
  keepCount?: number;
}

function DatabaseBackupSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dbBackup'],
    queryFn: getDatabaseBackupSettings,
  });

  const [draft, setDraft] = useState<Draft>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const enabled = draft.enabled ?? data?.enabled ?? false;
  const cron = draft.cron ?? data?.cron ?? '30 3 * * *';
  const dest = draft.dest ?? data?.dest ?? '';
  const keepCount = draft.keepCount ?? data?.keepCount ?? 1;

  const save = useMutation({
    mutationFn: () => updateDatabaseBackupSettings({ enabled, cron, dest, keepCount }),
    onSuccess: () => {
      setSaveError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: ['dbBackup'] });
    },
    onError: (e: unknown) =>
      setSaveError(e instanceof Error ? e.message : 'Save failed'),
  });

  const runNow = useMutation({
    mutationFn: runDatabaseBackupNow,
    // Invalidate on success AND error so a failed manual run refetches the
    // status block and shows lastError (the 500 body is a flat message only).
    onSettled: () => qc.invalidateQueries({ queryKey: ['dbBackup'] }),
  });

  // React Compiler memoizes this automatically; a manual useMemo would trip the
  // preserve-memoization rule because the inferred dep is `data`, broader than
  // `data?.lastSummary`.
  const lastSummary = (() => {
    if (!data?.lastSummary) return null;
    try {
      return JSON.parse(data.lastSummary) as {
        path: string;
        sizeBytes: number;
        durationMs: number;
      };
    } catch {
      return null;
    }
  })();

  return (
    <CollapsibleSection
      title="Scheduled Backups"
      storageKey="settings-section-scheduled-backups"
    >
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : isError || !data ? (
        // A failed GET must not render the editable form from fallback defaults;
        // one Save click would overwrite the real stored settings.
        <div className="space-y-2">
          <p className="text-sm text-red-600 dark:text-red-400">
            Could not load backup settings.
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 text-sm"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={enabled}
              onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
              ariaLabel={enabled ? 'Scheduled backups enabled' : 'Scheduled backups disabled'}
            />
            <span className="text-sm font-medium text-foreground">
              Enable scheduled backups
            </span>
          </label>
          <p className="text-sm text-muted-foreground -mt-2">
            Copies the SQLite database to the destination on a schedule so you
            can restore after a bad upgrade or a lost volume. The copies are not
            encrypted, so pick a destination you trust. Back up now works even
            with scheduling off.
          </p>

          {enabled && (
            <div className="flex items-center gap-3">
              <label
                htmlFor="db-backup-cron"
                className="text-sm text-muted-foreground whitespace-nowrap"
              >
                Schedule (cron):
              </label>
              <input
                id="db-backup-cron"
                type="text"
                value={cron}
                onChange={(e) => setDraft((d) => ({ ...d, cron: e.target.value }))}
                placeholder="30 3 * * *"
                className="w-40 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground font-mono text-sm"
              />
              <span className="text-xs text-muted-foreground">UTC</span>
            </div>
          )}

          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <label
                htmlFor="db-backup-dest"
                className="text-sm text-muted-foreground whitespace-nowrap"
              >
                Destination:
              </label>
              <input
                id="db-backup-dest"
                type="text"
                value={dest}
                onChange={(e) => setDraft((d) => ({ ...d, dest: e.target.value }))}
                placeholder={data?.effectiveDest}
                className="flex-1 min-w-0 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground font-mono text-sm"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Directory path inside the container. Empty uses the default.
            </p>
            {data.destWritable === false && (
              // Render whenever the stored dest is not writable, including the
              // case where validation failed and effectiveDest came back empty;
              // fall back to the entered path so the message still names it.
              <p className="text-xs text-amber-600 dark:text-amber-400">
                {data.effectiveDest || dest || 'The backup destination'} is not
                writable. Backups will fail until this is fixed.
              </p>
            )}
          </div>

          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <label
                htmlFor="db-backup-keep"
                className="text-sm text-muted-foreground whitespace-nowrap"
              >
                Copies to keep:
              </label>
              <input
                id="db-backup-keep"
                type="number"
                min={1}
                max={365}
                step={1}
                value={keepCount}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  setDraft((d) => ({
                    ...d,
                    keepCount: Number.isNaN(n) ? 1 : Math.min(365, Math.max(1, n)),
                  }));
                }}
                className="w-20 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground text-sm"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              1 overwrites a single file; higher keeps timestamped copies and
              prunes the oldest.
            </p>
          </div>

          {saveError && (
            <p className="text-sm text-red-600 dark:text-red-400">{saveError}</p>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 text-sm"
            >
              {save.isPending ? 'Saving...' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => runNow.mutate()}
              disabled={runNow.isPending}
              className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 text-sm"
            >
              {runNow.isPending ? 'Backing up...' : 'Back up now'}
            </button>
            {save.isSuccess && (
              <span className="ml-1 text-sm text-green-600 dark:text-green-400">Saved</span>
            )}
            {runNow.isSuccess && (
              <span className="ml-1 text-sm text-green-600 dark:text-green-400">
                Backup complete
              </span>
            )}
            {runNow.isError && (
              <span className="ml-1 text-sm text-red-600 dark:text-red-400">
                {(runNow.error as Error)?.message || 'Backup failed'}
              </span>
            )}
          </div>

          <div className="text-sm text-muted-foreground pt-2 border-t border-border">
            <div>
              <span className="font-medium text-foreground">Last backup:</span>{' '}
              {data?.lastRun ? new Date(data.lastRun).toLocaleString() : 'never'}
            </div>
            {lastSummary && (
              <div>
                <span className="font-medium text-foreground">Last result:</span>{' '}
                {lastSummary.path} ({formatStorage(lastSummary.sizeBytes / (1024 * 1024))},{' '}
                {(lastSummary.durationMs / 1000).toFixed(1)}s)
              </div>
            )}
            {data?.lastError && (
              <div className="text-red-600 dark:text-red-400">
                <span className="font-medium">Last error:</span> {data.lastError}
              </div>
            )}
          </div>
        </div>
      )}
    </CollapsibleSection>
  );
}

export default DatabaseBackupSection;

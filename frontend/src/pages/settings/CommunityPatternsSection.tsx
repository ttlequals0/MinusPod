import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import {
  getCommunitySyncSettings,
  updateCommunitySyncSettings,
  triggerCommunitySync,
  purgeAllCommunityPatterns,
} from '../../api/community';

interface Draft {
  enabled?: boolean;
  cron?: string;
}

function CommunityPatternsSection() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['communitySync'],
    queryFn: getCommunitySyncSettings,
    refetchInterval: 60_000,
  });

  const [draft, setDraft] = useState<Draft>({});
  const [cronError, setCronError] = useState<string | null>(null);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purgeResult, setPurgeResult] = useState<string | null>(null);
  const enabled = draft.enabled ?? data?.enabled ?? false;
  const cron = draft.cron ?? data?.cron ?? '0 3 * * 0';

  const save = useMutation({
    mutationFn: () => updateCommunitySyncSettings({ enabled, cron }),
    onSuccess: () => {
      setCronError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: ['communitySync'] });
    },
    onError: (e: unknown) =>
      setCronError(e instanceof Error ? e.message : 'Save failed'),
  });

  const syncNow = useMutation({
    mutationFn: triggerCommunitySync,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['communitySync'] }),
  });

  const purge = useMutation({
    mutationFn: purgeAllCommunityPatterns,
    onSuccess: (res) => {
      setPurgeResult(`Removed ${res.deleted} community pattern${res.deleted === 1 ? '' : 's'}.`);
      setConfirmPurge(false);
      qc.invalidateQueries({ queryKey: ['patterns'] });
      qc.invalidateQueries({ queryKey: ['patternStats'] });
      qc.invalidateQueries({ queryKey: ['communitySync'] });
    },
    onError: (e: unknown) =>
      setPurgeResult(e instanceof Error ? `Purge failed: ${e.message}` : 'Purge failed'),
  });

  // React Compiler memoizes this automatically; manual useMemo trips the
  // preserve-memoization rule because the inferred dep is `data` (broader
  // than `data?.lastSummary`).
  const lastSummary = (() => {
    if (!data?.lastSummary) return null;
    try {
      return JSON.parse(data.lastSummary) as {
        inserted: number;
        updated: number;
        deleted: number;
        skipped: number;
        errors: number;
      };
    } catch {
      return null;
    }
  })();

  return (
    <CollapsibleSection title="Community Patterns">
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : (
        <div className="space-y-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={enabled}
              onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
              ariaLabel={enabled ? 'Sync enabled' : 'Sync disabled'}
            />
            <span className="text-sm font-medium text-foreground">
              Enable community pattern sync
            </span>
          </label>
          <p className="text-sm text-muted-foreground -mt-2">
            Pulls a curated list of common-sponsor patterns from the MinusPod
            GitHub repository so a fresh install gets coverage without having
            to build a library from scratch. Off by default; opt in here.
          </p>

          {enabled && (
            <div className="flex items-center gap-3">
              <label htmlFor="cron" className="text-sm text-muted-foreground whitespace-nowrap">
                Schedule (cron):
              </label>
              <input
                id="cron"
                type="text"
                value={cron}
                onChange={(e) => setDraft((d) => ({ ...d, cron: e.target.value }))}
                placeholder="0 3 * * 0"
                className="w-40 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground font-mono text-sm"
              />
              <span className="text-xs text-muted-foreground">UTC</span>
            </div>
          )}

          {cronError && (
            <p className="text-sm text-red-600 dark:text-red-400">{cronError}</p>
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
              onClick={() => syncNow.mutate()}
              disabled={syncNow.isPending || !data?.enabled}
              className="px-4 py-2 rounded-lg border border-border hover:bg-accent disabled:opacity-50 text-sm"
            >
              {syncNow.isPending ? 'Syncing...' : 'Sync now'}
            </button>
            {save.isSuccess && (
              <span className="ml-1 text-sm text-green-600 dark:text-green-400">Saved</span>
            )}
            {syncNow.isError && (
              <span className="ml-1 text-sm text-red-600 dark:text-red-400">
                {(syncNow.error as Error)?.message || 'Sync failed'}
              </span>
            )}
          </div>

          <div className="text-sm text-muted-foreground pt-2 border-t border-border">
            <div>
              <span className="font-medium text-foreground">Last sync:</span>{' '}
              {data?.lastRun ? new Date(data.lastRun).toLocaleString() : 'never'}
            </div>
            {data?.manifestVersion && (
              <div>
                <span className="font-medium text-foreground">Manifest version:</span>{' '}
                {data.manifestVersion}
              </div>
            )}
            {lastSummary && (
              <div>
                <span className="font-medium text-foreground">Last result:</span>{' '}
                {lastSummary.inserted} added, {lastSummary.updated} updated,{' '}
                {lastSummary.deleted} removed, {lastSummary.skipped} skipped,{' '}
                {lastSummary.errors} errors.
              </div>
            )}
            {data?.lastError && (
              <div className="text-red-600 dark:text-red-400">
                <span className="font-medium">Last error:</span> {data.lastError}
              </div>
            )}
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <h4 className="text-sm font-medium text-foreground">Remove all community patterns</h4>
            <p className="text-sm text-muted-foreground">
              Wipes every pattern with source=community from this instance, including any
              you marked Protect from sync. Local and imported patterns are left alone.
              If sync is on, the next tick repopulates.
            </p>
            {confirmPurge ? (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => purge.mutate()}
                  disabled={purge.isPending}
                  className="px-3 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 text-sm"
                >
                  {purge.isPending ? 'Removing...' : 'Yes, remove all'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmPurge(false)}
                  disabled={purge.isPending}
                  className="px-3 py-1.5 rounded-lg border border-border hover:bg-accent disabled:opacity-50 text-sm"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => { setPurgeResult(null); setConfirmPurge(true); }}
                className="px-3 py-1.5 rounded-lg border border-red-500 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 text-sm"
              >
                Remove all community patterns
              </button>
            )}
            {purgeResult && (
              <p className={`text-sm ${purgeResult.startsWith('Purge failed') ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'}`}>
                {purgeResult}
              </p>
            )}
          </div>
        </div>
      )}
    </CollapsibleSection>
  );
}

export default CommunityPatternsSection;

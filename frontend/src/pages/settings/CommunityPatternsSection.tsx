import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import Checkbox from '../../components/Checkbox';
import { getErrorMessage } from '../../api/client';
import ToggleSwitch from '../../components/ToggleSwitch';
import {
  getCommunitySyncSettings,
  updateCommunitySyncSettings,
  triggerCommunitySync,
  purgeAllCommunityPatterns,
} from '../../api/community';
import { SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, type SegmentCategory } from '../../utils/segmentCategory';
import { btnDestructive, btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import SavedBadge from './SavedBadge';

interface Draft {
  enabled?: boolean;
  cron?: string;
  categories?: SegmentCategory[];
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
  const categories = draft.categories ?? data?.categories ?? SEGMENT_CATEGORIES;

  const save = useMutation({
    mutationFn: () => updateCommunitySyncSettings({ enabled, cron, categories }),
    onSuccess: () => {
      setCronError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: ['communitySync'] });
    },
    onError: (e: unknown) => setCronError(getErrorMessage(e, 'Save failed')),
  });

  function toggleCategory(cat: SegmentCategory) {
    setDraft((d) => {
      const current = d.categories ?? data?.categories ?? SEGMENT_CATEGORIES;
      const next = current.includes(cat)
        ? current.filter((c) => c !== cat)
        : [...current, cat];
      return { ...d, categories: next };
    });
  }

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
        filtered?: number;
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
            <p className="text-sm text-destructive">{cronError}</p>
          )}

          <div className="pt-2 border-t border-border space-y-2">
            <h4 className="text-sm font-medium text-foreground">Categories to sync</h4>
            <p className="text-sm text-muted-foreground">
              Unchecking a category deactivates its already-synced community patterns
              rather than deleting them; re-checking it reactivates them on the next sync.
              Locally created patterns are never affected.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {SEGMENT_CATEGORIES.map((cat) => (
                <div key={cat} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={categories.includes(cat)}
                    onChange={() => toggleCategory(cat)}
                    ariaLabel={`${SEGMENT_CATEGORY_LABELS[cat]} (${data?.categoryBreakdown?.[cat] ?? 0})`}
                  />
                  <span className="text-foreground">{SEGMENT_CATEGORY_LABELS[cat]}</span>
                  <span className="text-muted-foreground">
                    ({data?.categoryBreakdown?.[cat] ?? 0})
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 text-sm`}
            >
              {save.isPending ? 'Saving...' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => syncNow.mutate()}
              disabled={syncNow.isPending || !data?.enabled}
              className={`px-4 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 text-sm`}
            >
              {syncNow.isPending ? 'Syncing...' : 'Sync now'}
            </button>
            {save.isSuccess && <SavedBadge className="ml-1" />}
            {syncNow.isError && (
              <span className="ml-1 text-sm text-destructive">
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
                {lastSummary.filtered ?? 0} filtered by category, {lastSummary.errors} errors.
              </div>
            )}
            {data?.lastError && (
              <div className="text-destructive">
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
                  className={`px-3 py-1.5 rounded-lg ${btnDestructive} ${focusRing} disabled:opacity-50 text-sm transition-colors`}
                >
                  {purge.isPending ? 'Removing...' : 'Yes, remove all'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmPurge(false)}
                  disabled={purge.isPending}
                  className={`px-3 py-1.5 rounded-lg ${btnSecondary} disabled:opacity-50 text-sm`}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => { setPurgeResult(null); setConfirmPurge(true); }}
                className="px-3 py-1.5 rounded-lg border border-destructive text-destructive hover:bg-destructive dark:hover:bg-destructive/20 text-sm"
              >
                Remove all community patterns
              </button>
            )}
            {purgeResult && (
              <p className={`text-sm ${purgeResult.startsWith('Purge failed') ? 'text-destructive' : 'text-success'}`}>
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

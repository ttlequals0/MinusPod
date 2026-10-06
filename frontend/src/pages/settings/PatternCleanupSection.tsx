import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import CollapsibleSection, { useCollapsibleOpen, useSectionVisible } from '../../components/CollapsibleSection';
import { SkeletonRows } from '../../components/Skeleton';
import ToggleSwitch from '../../components/ToggleSwitch';
import ExperimentalBadge from '../../components/ExperimentalBadge';
import NumberInput from '../../components/NumberInput';
import { ConfirmModal } from '../../components/Modal';
import { ApiError, getErrorMessage } from '../../api/client';
import {
  getPatternCleanupStatus,
  patternCleanupQueryKey,
  runPatternCleanup,
  updatePatternCleanupSettings,
  type PatternCleanupSettings,
} from '../../api/patternCleanup';
import { SAME_AS_DETECTION, SLOT_PRIMARY, SLOT_SECONDARY, type ProviderSlot } from '../../api/types';
import { useModelCatalog } from '../../hooks/useModelCatalog';
import { btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import SavedBadge from './SavedBadge';
import CronScheduleField from './CronScheduleField';
import ModelSelect from './ModelSelect';
import StageProviderSelect, { inheritedSlotOptions } from './StageProviderSelect';

interface PatternCleanupSectionProps {
  // Provider types behind each slot; secondaryProvider is '' when that slot is unusable.
  primaryProvider: string;
  secondaryProvider: string;
  secondaryEnabled: boolean;
  detectionSlot: ProviderSlot;
}

const STORAGE_KEY = 'settings-section-pattern-cleanup';
const fieldInput = 'px-3 py-1.5 rounded-lg border border-input bg-background text-foreground text-sm';
const actionButton = `px-4 py-2 rounded-lg disabled:opacity-50 text-sm ${focusRing}`;

function runErrorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) return 'A cleanup run is already in progress.';
  return getErrorMessage(e, 'Could not start the cleanup run');
}

function PatternCleanupSection({
  primaryProvider, secondaryProvider, secondaryEnabled, detectionSlot,
}: PatternCleanupSectionProps) {
  const qc = useQueryClient();
  // Fetch only while the card is on screen; it sits in the collapsed Experiments group.
  const [open, setOpen] = useCollapsibleOpen(STORAGE_KEY);
  const visible = useSectionVisible(STORAGE_KEY, open);
  const { data, isError, refetch } = useQuery({
    queryKey: patternCleanupQueryKey,
    queryFn: getPatternCleanupStatus,
    enabled: visible,
    // Poll while a run is going so the last-run line updates when it ends.
    refetchInterval: (query) => (query.state.data?.inProgress ? 3_000 : false),
  });

  const [draft, setDraft] = useState<Partial<PatternCleanupSettings>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmForce, setConfirmForce] = useState(false);

  const settings: PatternCleanupSettings = {
    enabled: draft.enabled ?? data?.enabled ?? false,
    cron: draft.cron ?? data?.cron ?? '0 4 * * 0',
    batchSize: draft.batchSize ?? data?.batchSize ?? 25,
    unusedDays: draft.unusedDays ?? data?.unusedDays ?? 90,
    provider: draft.provider ?? (data?.provider || SAME_AS_DETECTION),
    model: draft.model ?? data?.model ?? '',
  };
  const update = (patch: Partial<PatternCleanupSettings>) => setDraft((d) => ({ ...d, ...patch }));

  // Mirrors llm_route: an unusable secondary falls back to the primary.
  const slot: ProviderSlot = settings.provider === SLOT_SECONDARY
    ? (secondaryProvider ? SLOT_SECONDARY : SLOT_PRIMARY)
    : settings.provider === SLOT_PRIMARY ? SLOT_PRIMARY : detectionSlot;
  const catalog = useModelCatalog(
    slot === SLOT_SECONDARY ? secondaryProvider : primaryProvider, slot, visible && !!data,
  );

  const save = useMutation({
    mutationFn: () => updatePatternCleanupSettings(settings),
    onSuccess: () => {
      setSaveError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: patternCleanupQueryKey });
    },
    onError: (e: unknown) => setSaveError(getErrorMessage(e, 'Save failed')),
  });

  const run = useMutation({
    mutationFn: (force: boolean) => runPatternCleanup(force),
    onSettled: () => {
      setConfirmForce(false);
      qc.invalidateQueries({ queryKey: patternCleanupQueryKey });
    },
  });

  const running = run.isPending || !!data?.inProgress;
  const summary = data?.lastSummary;

  return (
    <CollapsibleSection
      title="Pattern Cleanup"
      subtitle="Reviews learned patterns with an LLM and suggests trims, splits, renames and retirements for you to approve. Off by default."
      storageKey={STORAGE_KEY}
      onToggle={setOpen}
    >
      {isError ? (
        <div className="space-y-2">
          <p className="text-sm text-destructive">Could not load pattern cleanup settings.</p>
          <button type="button" onClick={() => refetch()} className={`${actionButton} ${btnSecondary}`}>
            Retry
          </button>
        </div>
      ) : !data ? (
        <SkeletonRows count={3} />
      ) : (
        <div className="space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={settings.enabled}
                onChange={(v) => update({ enabled: v })}
                ariaLabel="Enable scheduled pattern cleanup"
              />
              <span className="text-sm font-medium text-foreground">Enable scheduled cleanup</span>
              <ExperimentalBadge title="Experimental: suggestions change nothing until you approve them" />
            </label>
            <p className="mt-2 text-sm text-muted-foreground">
              Only learned patterns are reviewed; community and manual patterns are left alone.
              Suggestions wait on the Patterns page until you approve or reject them, and an
              approved change can be undone. Run now works even with scheduling off.
            </p>
          </div>

          {settings.enabled && (
            <CronScheduleField
              id="pattern-cleanup-cron"
              value={settings.cron}
              onChange={(v) => update({ cron: v })}
              placeholder="0 4 * * 0"
            />
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <StageProviderSelect
              id="patternCleanupProvider"
              label="Cleanup Provider"
              value={settings.provider}
              options={inheritedSlotOptions(secondaryEnabled)}
              onChange={(v) => update({ provider: v })}
              secondaryEnabled={secondaryEnabled}
            />
            <ModelSelect
              id="patternCleanupModel"
              label="Cleanup Model"
              value={settings.model}
              catalog={catalog}
              onChange={(v) => update({ model: v })}
              inheritLabel="Same as detection model"
              description="A higher quality model gives better trims and splits"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label htmlFor="pattern-cleanup-batch" className="block text-sm font-medium text-foreground">
                Patterns per run
              </label>
              <NumberInput
                id="pattern-cleanup-batch"
                min={1}
                max={200}
                step={1}
                fallback={25}
                parse={(s) => parseInt(s, 10)}
                value={settings.batchSize}
                onCommit={(n) => update({ batchSize: n })}
                className={`w-24 ${fieldInput} ${focusRing}`}
              />
              <p className="text-xs text-muted-foreground">
                Patterns not yet reviewed go first. 1 to 200.
              </p>
            </div>
            <div className="space-y-1">
              <label htmlFor="pattern-cleanup-unused" className="block text-sm font-medium text-foreground">
                Retire after (days unused)
              </label>
              <NumberInput
                id="pattern-cleanup-unused"
                min={7}
                max={3650}
                step={1}
                fallback={90}
                parse={(s) => parseInt(s, 10)}
                value={settings.unusedDays}
                onCommit={(n) => update({ unusedDays: n })}
                className={`w-24 ${fieldInput} ${focusRing}`}
              />
              <p className="text-xs text-muted-foreground">
                Suggests retiring a pattern with no matches for this long. 7 to 3650.
              </p>
            </div>
          </div>

          {saveError && <p className="text-sm text-destructive">{saveError}</p>}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className={`${actionButton} ${btnPrimary}`}
            >
              {save.isPending ? 'Saving...' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => run.mutate(false)}
              disabled={running}
              className={`${actionButton} ${btnSecondary}`}
            >
              {running ? 'Running...' : 'Run now'}
            </button>
            <button
              type="button"
              onClick={() => setConfirmForce(true)}
              disabled={running}
              className={`${actionButton} ${btnSecondary}`}
            >
              Force recheck all
            </button>
            {save.isSuccess && <SavedBadge className="ml-1" />}
            {run.isError && <span className="text-sm text-destructive">{runErrorMessage(run.error)}</span>}
          </div>

          <div className="text-sm text-muted-foreground pt-2 border-t border-border space-y-0.5">
            {data.inProgress && data.lastRun && (
              <div>
                <span className="font-medium text-foreground">Running since:</span>{' '}
                {new Date(data.lastRun).toLocaleString()}
              </div>
            )}
            <div>
              <span className="font-medium text-foreground">Last run:</span>{' '}
              {summary?.finishedAt ? new Date(summary.finishedAt).toLocaleString() : 'never'}
              {summary && (
                <>
                  {': '}
                  {summary.reviewedCount} reviewed, {summary.suggestedCount} suggested, {summary.skippedCount} skipped
                  {summary.errorCount > 0 && `, ${summary.errorCount} failed`}
                </>
              )}
            </div>
            {data.pending.total > 0 && (
              <div>
                <span className="font-medium text-foreground">Waiting for review:</span>{' '}
                <Link to="/patterns?tab=cleanup" className={`text-primary hover:underline ${focusRing}`}>
                  {data.pending.total} {data.pending.total === 1 ? 'suggestion' : 'suggestions'}
                </Link>
              </div>
            )}
            {data.lastError && (
              <div className="text-destructive">
                <span className="font-medium">Last error:</span> {data.lastError}
              </div>
            )}
          </div>
        </div>
      )}

      {confirmForce && (
        <ConfirmModal
          title="Recheck every learned pattern?"
          confirmLabel="Recheck all"
          busyLabel="Starting..."
          destructive={false}
          pending={run.isPending}
          onCancel={() => setConfirmForce(false)}
          onConfirm={() => run.mutate(true)}
        >
          <p>
            Every learned pattern will be reviewed again, including ones you already approved or
            rejected. Runs still take {settings.batchSize} patterns at a time, so a large library
            takes several runs and uses more LLM calls.
          </p>
        </ConfirmModal>
      )}
    </CollapsibleSection>
  );
}

export default PatternCleanupSection;

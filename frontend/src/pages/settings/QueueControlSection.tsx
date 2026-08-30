import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import { getErrorMessage } from '../../api/client';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import {
  getOfflineQueueSettings,
  updateOfflineQueueSettings,
  getRateLimitHoldSettings,
  updateRateLimitHoldSettings,
} from '../../api/settings';
import { btnPrimary, btnSecondary } from '../../components/buttonStyles';
import SavedBadge from './SavedBadge';
import { focusRing } from '../../components/fieldStyles';

interface QueueControlSectionProps {
  processNewEpisodesFirst: boolean;
  onProcessNewEpisodesFirstChange: (enabled: boolean) => void;
  queueManualBoost: number;
  onQueueManualBoostChange: (value: number) => void;
  queueFreshBoost: number;
  onQueueFreshBoostChange: (value: number) => void;
  queueBulkBoost: number;
  onQueueBulkBoostChange: (value: number) => void;
}

interface OfflineQueueDraft {
  enabled?: boolean;
  ttlHours?: number;
}

function OfflineQueueBlock() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['offlineQueue'],
    queryFn: getOfflineQueueSettings,
  });

  const [draft, setDraft] = useState<OfflineQueueDraft>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const enabled = draft.enabled ?? data?.enabled ?? false;
  const ttlHours = draft.ttlHours ?? data?.ttlHours ?? 48;

  const save = useMutation({
    mutationFn: () => updateOfflineQueueSettings({ enabled, ttlHours }),
    onSuccess: () => {
      setSaveError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: ['offlineQueue'] });
    },
    onError: (e: unknown) => setSaveError(getErrorMessage(e, 'Save failed')),
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }
  if (isError || !data) {
    // A failed GET must not render the editable form from fallback
    // defaults; one Save click would overwrite the real stored settings.
    return (
      <div className="space-y-2">
        <p className="text-sm text-destructive">Could not load offline queue settings.</p>
        <button
          type="button"
          onClick={() => refetch()}
          className={`px-4 py-2 rounded-lg ${btnSecondary} text-sm ${focusRing}`}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={enabled}
          onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
          ariaLabel={enabled ? 'Offline queue enabled' : 'Offline queue disabled'}
        />
        <span className="text-sm font-medium text-foreground">
          Queue episodes while the LLM or Whisper endpoint is down
        </span>
      </label>
      <p className="text-sm text-muted-foreground -mt-2">
        For self-hosted LLMs or Whisper servers that only run part of the
        day. Episodes that fail because the endpoint is unreachable wait
        in a queue and process on their own once it is back, instead of
        erroring out until you reprocess them by hand.
      </p>

      <div className="space-y-1">
        <div className="flex items-center gap-3">
          <label
            htmlFor="offline-queue-ttl"
            className="text-sm text-muted-foreground whitespace-nowrap"
          >
            Give up after:
          </label>
          <NumberInput
            id="offline-queue-ttl"
            value={ttlHours}
            min={1}
            max={720}
            step={1}
            fallback={48}
            parse={(s) => parseInt(s, 10)}
            onCommit={(v) => setDraft((d) => ({ ...d, ttlHours: v }))}
            className="w-20 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground text-sm"
          />
          <span className="text-xs text-muted-foreground">hours</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Episodes still waiting after this long are marked failed and
          logged. Applies to episodes already in the queue even if you
          turn the toggle off.
        </p>
      </div>

      {data.deferredCount > 0 && (
        <p className="text-sm text-c-purple">
          {data.deferredCount} episode{data.deferredCount === 1 ? '' : 's'} currently
          waiting for an endpoint to come back.
        </p>
      )}

      {saveError && (
        <p className="text-sm text-destructive">{saveError}</p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 text-sm ${focusRing}`}
        >
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
        {save.isSuccess && <SavedBadge className="ml-1" />}
      </div>
    </div>
  );
}

interface RateLimitHoldDraft {
  enabled?: boolean;
  ttlHours?: number;
}

function RateLimitHoldBlock() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['rateLimitHold'],
    queryFn: getRateLimitHoldSettings,
  });

  const [draft, setDraft] = useState<RateLimitHoldDraft>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const enabled = draft.enabled ?? data?.enabled ?? false;
  const ttlHours = draft.ttlHours ?? data?.ttlHours ?? 48;

  const save = useMutation({
    mutationFn: () => updateRateLimitHoldSettings({ enabled, ttlHours }),
    onSuccess: () => {
      setSaveError(null);
      setDraft({});
      qc.invalidateQueries({ queryKey: ['rateLimitHold'] });
    },
    onError: (e: unknown) => setSaveError(getErrorMessage(e, 'Save failed')),
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }
  if (isError || !data) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-destructive">Could not load rate-limit hold settings.</p>
        <button
          type="button"
          onClick={() => refetch()}
          className={`px-4 py-2 rounded-lg ${btnSecondary} text-sm ${focusRing}`}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={enabled}
          onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
          ariaLabel={enabled ? 'Rate-limit hold enabled' : 'Rate-limit hold disabled'}
        />
        <span className="text-sm font-medium text-foreground">
          Pause the queue when the LLM provider is rate limited
        </span>
      </label>
      <p className="text-sm text-muted-foreground -mt-2">
        When the provider answers 429 with a reset time, episodes stop
        retrying and wait instead. The queue stays paused until the reset
        passes, then processes on its own. Off by default. With it off,
        rate limits retry on the normal ladder.
      </p>

      <div className="space-y-1">
        <div className="flex items-center gap-3">
          <label
            htmlFor="rate-limit-hold-ttl"
            className="text-sm text-muted-foreground whitespace-nowrap"
          >
            Give up after:
          </label>
          <NumberInput
            id="rate-limit-hold-ttl"
            value={ttlHours}
            min={1}
            max={720}
            step={1}
            fallback={48}
            parse={(s) => parseInt(s, 10)}
            onCommit={(v) => setDraft((d) => ({ ...d, ttlHours: v }))}
            className="w-20 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground text-sm"
          />
          <span className="text-xs text-muted-foreground">hours</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Episodes still waiting after this long are marked failed and
          logged. Applies to episodes already in the queue even if you
          turn the toggle off.
        </p>
      </div>

      {data.holdUntil && (
        <p className="text-sm text-c-purple">
          Queue paused until {new Date(data.holdUntil).toLocaleString()} (provider
          rate limit). {data.holdCount} episode{data.holdCount === 1 ? '' : 's'} waiting.
        </p>
      )}

      {saveError && (
        <p className="text-sm text-destructive">{saveError}</p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 text-sm ${focusRing}`}
        >
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
        {save.isSuccess && <SavedBadge className="ml-1" />}
      </div>
    </div>
  );
}

function QueueControlSection({
  processNewEpisodesFirst,
  onProcessNewEpisodesFirstChange,
  queueManualBoost,
  onQueueManualBoostChange,
  queueFreshBoost,
  onQueueFreshBoostChange,
  queueBulkBoost,
  onQueueBulkBoostChange,
}: QueueControlSectionProps) {
  return (
    <CollapsibleSection
      title="Queue Control"
      subtitle="How episodes move through the processing queue and when they wait."
    >
      <div className="space-y-6">
        {/* Process new episodes first: fresh-episode queue boost, saves immediately */}
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={processNewEpisodesFirst}
              onChange={onProcessNewEpisodesFirstChange}
              ariaLabel="Process new episodes first"
            />
            <span className="text-sm font-medium text-foreground">
              Process new episodes first
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Episodes published in the last 48 hours jump ahead of queued backlog.
          </p>
        </div>

        {/* Queue priority: how far each kind of request jumps the queue.
            Higher number processes sooner; ties process oldest first. */}
        <div className="pt-4 border-t border-border">
          <p className="text-sm font-medium text-foreground mb-1">Queue priority</p>
          <p className="text-sm text-muted-foreground mb-3">
            How far each request type jumps the queue. Ties process oldest first.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label htmlFor="queueManualBoost" className="block text-sm text-foreground mb-2">
                Play / Reprocess
              </label>
              <NumberInput
                id="queueManualBoost"
                value={queueManualBoost}
                min={0}
                max={100}
                step={1}
                fallback={20}
                onCommit={onQueueManualBoostChange}
              />
              <p className="mt-2 text-sm text-muted-foreground">
                Playing an unprocessed episode or reprocessing one. Keep highest so your requests skip the backlog. Default 20.
              </p>
            </div>
            <div>
              <label htmlFor="queueFreshBoost" className="block text-sm text-foreground mb-2">
                New episode
              </label>
              <NumberInput
                id="queueFreshBoost"
                value={queueFreshBoost}
                min={0}
                max={100}
                step={1}
                fallback={5}
                onCommit={onQueueFreshBoostChange}
              />
              <p className="mt-2 text-sm text-muted-foreground">
                Published in the last 48 hours. Needs the toggle above. Default 5.
              </p>
            </div>
            <div>
              <label htmlFor="queueBulkBoost" className="block text-sm text-foreground mb-2">
                Reprocess All
              </label>
              <NumberInput
                id="queueBulkBoost"
                value={queueBulkBoost}
                min={0}
                max={100}
                step={1}
                fallback={0}
                onCommit={onQueueBulkBoostChange}
              />
              <p className="mt-2 text-sm text-muted-foreground">
                Reprocess All and segment re-renders. Leave at 0 to keep backfills last. Default 0.
              </p>
            </div>
          </div>
        </div>

        <div className="pt-4 border-t border-border">
          <OfflineQueueBlock />
        </div>

        <div className="pt-4 border-t border-border">
          <RateLimitHoldBlock />
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default QueueControlSection;

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import {
  getOfflineQueueSettings,
  updateOfflineQueueSettings,
} from '../../api/settings';

interface Draft {
  enabled?: boolean;
  ttlHours?: number;
}

function OfflineQueueSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['offlineQueue'],
    queryFn: getOfflineQueueSettings,
  });

  const [draft, setDraft] = useState<Draft>({});
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
    onError: (e: unknown) =>
      setSaveError(e instanceof Error ? e.message : 'Save failed'),
  });

  return (
    <CollapsibleSection
      title="Offline Queue"
      storageKey="settings-section-offline-queue"
    >
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : isError || !data ? (
        // A failed GET must not render the editable form from fallback
        // defaults; one Save click would overwrite the real stored settings.
        <div className="space-y-2">
          <p className="text-sm text-red-600 dark:text-red-400">
            Could not load offline queue settings.
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
            <p className="text-sm text-purple-600 dark:text-purple-400">
              {data.deferredCount} episode{data.deferredCount === 1 ? '' : 's'} currently
              waiting for an endpoint to come back.
            </p>
          )}

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
            {save.isSuccess && (
              <span className="ml-1 text-sm text-green-600 dark:text-green-400">Saved</span>
            )}
          </div>
        </div>
      )}
    </CollapsibleSection>
  );
}

export default OfflineQueueSection;

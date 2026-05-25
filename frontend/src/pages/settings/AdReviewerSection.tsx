import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { getReviewerSettings, updateReviewerSettings } from '../../api/community';

interface Draft {
  enabled?: boolean;
  threshold?: number;
}

function AdReviewerSection() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['reviewerSettings'],
    queryFn: getReviewerSettings,
  });

  const [draft, setDraft] = useState<Draft>({});
  const enabled = draft.enabled ?? data?.updatePatternsFromReviewerAdjustments ?? true;
  const threshold = draft.threshold ?? data?.minTrimThreshold ?? 20;

  const save = useMutation({
    mutationFn: () =>
      updateReviewerSettings({
        updatePatternsFromReviewerAdjustments: enabled,
        minTrimThreshold: threshold,
      }),
    onSuccess: () => {
      setDraft({});
      qc.invalidateQueries({ queryKey: ['reviewerSettings'] });
    },
  });

  return (
    <CollapsibleSection title="Ad Reviewer">
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <div className="space-y-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={enabled}
              onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
              ariaLabel={enabled ? 'Auto-update enabled' : 'Auto-update disabled'}
            />
            <span className="text-sm font-medium text-foreground">
              Update patterns from reviewer adjustments
            </span>
          </label>
          <p className="text-sm text-muted-foreground -mt-2">
            When a reviewer narrows an ad's boundaries by more than the threshold
            below, the matching local pattern's text is re-extracted from the
            new bounds. Community patterns are never auto-rewritten.
          </p>

          {enabled && (
            <div className="flex items-center gap-3">
              <label htmlFor="trimThreshold" className="text-sm text-muted-foreground whitespace-nowrap">
                Minimum trim threshold:
              </label>
              <input
                id="trimThreshold"
                type="number"
                min={1}
                max={120}
                value={threshold}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, threshold: parseFloat(e.target.value) || 0 }))
                }
                className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground"
              />
              <span className="text-sm text-muted-foreground">seconds</span>
            </div>
          )}

          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 text-sm"
          >
            {save.isPending ? 'Saving…' : 'Save Reviewer Settings'}
          </button>
          {save.isSuccess && (
            <span className="ml-3 text-sm text-green-600 dark:text-green-400">Saved</span>
          )}
        </div>
      )}
    </CollapsibleSection>
  );
}

export default AdReviewerSection;

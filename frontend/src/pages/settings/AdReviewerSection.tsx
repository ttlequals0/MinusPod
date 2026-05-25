import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { getReviewerSettings, updateReviewerSettings } from '../../api/community';

interface Draft {
  enabled?: boolean;
  threshold?: number;
  parallelAds?: number;
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
  const parallelAdsDefault = data?.parallelAdsDefault ?? 4;
  const parallelAds = draft.parallelAds ?? data?.parallelAds ?? parallelAdsDefault;

  const save = useMutation({
    mutationFn: () =>
      updateReviewerSettings({
        updatePatternsFromReviewerAdjustments: enabled,
        minTrimThreshold: threshold,
        parallelAds,
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

          <div className="pt-2 border-t border-border">
            <div className="flex items-center gap-3">
              <label htmlFor="reviewerParallelAds" className="text-sm text-muted-foreground whitespace-nowrap">
                Parallel ad reviews:
              </label>
              <input
                id="reviewerParallelAds"
                type="number"
                min={1}
                max={32}
                step={1}
                value={parallelAds}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === '') {
                    setDraft((d) => ({ ...d, parallelAds: parallelAdsDefault }));
                    return;
                  }
                  const v = parseInt(raw, 10);
                  if (!Number.isFinite(v)) return;
                  setDraft((d) => ({ ...d, parallelAds: Math.max(1, Math.min(32, v)) }));
                }}
                onBlur={(e) => {
                  if (e.target.value === '') {
                    setDraft((d) => ({ ...d, parallelAds: parallelAdsDefault }));
                  }
                }}
                className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground"
              />
              <span className="text-sm text-muted-foreground">ads at a time</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Number of ads the reviewer asks the LLM about at the same time. 1 means sequential
              (original behavior). Higher values speed up reviewer passes on episodes with many
              ads, at the cost of more concurrent LLM load. Range 1-32, default {parallelAdsDefault}.
            </p>
          </div>

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

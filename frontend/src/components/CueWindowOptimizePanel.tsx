import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import LoadingSpinner from './LoadingSpinner';
import { ghostBtn, primaryBtn } from './cueScanStyles';
import { getErrorMessage } from '../api/client';
import { useScanQuery } from '../hooks/useScanQuery';
import {
  optimizeCueWindow,
  updateCueTemplate,
  type CueTemplate,
  type CueWindowOptimizeResponse,
} from '../api/cueTemplates';

interface CueWindowOptimizePanelProps {
  slug: string;
  template: CueTemplate;
  onClose: () => void;
}

// Inline before/after panel for the window optimizer (D2b). Mounting claims or
// polls the background sweep (D1b claim/poll convention); Apply moves the
// window via the template PATCH, which re-extracts blobs server-side.
export default function CueWindowOptimizePanel({ slug, template, onClose }: CueWindowOptimizePanelProps) {
  const queryClient = useQueryClient();
  const [applyError, setApplyError] = useState<string | null>(null);

  const queryKey = ['cue-window-optimize', slug, template.id];
  const { data, scanning, scanError, rescan: doRescan } =
    useScanQuery<CueWindowOptimizeResponse>({
      queryKey,
      queryFn: () => optimizeCueWindow(slug, template.id),
      rescanFn: () => optimizeCueWindow(slug, template.id, true),
      savedErrorFallback: 'Optimize failed.',
      thrownError: 'message',
    });

  // Collapse keeps nothing: drop the cached proposal once the panel unmounts
  // (Discard, Apply, or toggling the row action). Removing after unmount also
  // avoids an observer refetch re-claiming a scan server-side.
  useEffect(() => {
    return () => {
      queryClient.removeQueries({ queryKey: ['cue-window-optimize', slug, template.id] });
    };
  }, [queryClient, slug, template.id]);

  const { proposedStartS, proposedEndS, meanPeakScore, baselineMeanPeakScore, perEpisode, baselineWindow } = data ?? {};
  const ready = data?.status === 'ready'
    && proposedStartS != null && proposedEndS != null && meanPeakScore != null;
  const alreadyOptimal = ready
    && proposedStartS === baselineWindow?.startS
    && proposedEndS === baselineWindow?.endS;
  const scoreDelta = ready && baselineMeanPeakScore != null
    ? meanPeakScore - baselineMeanPeakScore
    : null;

  const applyMutation = useMutation({
    mutationFn: (vars: { startS: number; endS: number }) =>
      updateCueTemplate(template.id, {
        sourceOffsetS: vars.startS,
        // Round away float dust from end - start; the sweep works in 0.1s steps.
        durationS: Math.round((vars.endS - vars.startS) * 1000) / 1000,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });
      onClose();
    },
    onError: (e) => setApplyError(getErrorMessage(e, 'Apply failed')),
  });

  const rescan = () => {
    setApplyError(null);
    doRescan();
  };

  return (
    <div className="mt-2 rounded border border-border bg-secondary/30 px-3 py-2 text-xs">
      {scanning && (
        <p className="text-muted-foreground flex items-center gap-2">
          <LoadingSpinner size="sm" inline /> Testing window trims across episodes, this can take a minute...
        </p>
      )}
      {!scanning && scanError && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-destructive">{scanError}</p>
          <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={rescan}>
            Rescan
          </button>
          <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={onClose}>
            Close
          </button>
        </div>
      )}
      {!scanning && !scanError && ready && (
        <div className="space-y-2">
          {alreadyOptimal ? (
            <p>
              <span className="px-2 py-0.5 rounded font-medium bg-success/20 text-success">
                Already optimal
              </span>
              <span className="ml-2 text-muted-foreground">
                No trim scored higher than the current window.
              </span>
            </p>
          ) : (
            <div className="grid max-w-sm grid-cols-[auto_1fr_1fr] gap-x-4 gap-y-0.5 font-mono">
              <span />
              <span className="font-sans text-muted-foreground">Current</span>
              <span className="font-sans text-muted-foreground">Proposed</span>
              <span className="font-sans text-muted-foreground">Start</span>
              <span>{baselineWindow ? `${baselineWindow.startS.toFixed(2)}s` : '--'}</span>
              <span>{proposedStartS.toFixed(2)}s</span>
              <span className="font-sans text-muted-foreground">End</span>
              <span>{baselineWindow ? `${baselineWindow.endS.toFixed(2)}s` : '--'}</span>
              <span>{proposedEndS.toFixed(2)}s</span>
              <span className="font-sans text-muted-foreground">Score</span>
              <span title={baselineMeanPeakScore == null
                ? 'The current window is outside the capture bounds, so it was not scored'
                : undefined}
              >
                {baselineMeanPeakScore != null ? baselineMeanPeakScore.toFixed(3) : '--'}
              </span>
              <span>
                {meanPeakScore.toFixed(3)}
                {scoreDelta != null && (
                  <span className={`ml-1 ${scoreDelta >= 0
                    ? 'text-success'
                    : 'text-warning'}`}
                  >
                    {scoreDelta >= 0 ? '+' : ''}{scoreDelta.toFixed(3)}
                  </span>
                )}
              </span>
            </div>
          )}
          {(perEpisode?.length ?? 0) > 0 && (
            <p className="text-muted-foreground">
              Per episode:{' '}
              {perEpisode!.map((e) => (
                <span key={e.episodeId} className="mr-2 font-mono">
                  {e.episodeId.slice(0, 8)} {e.peakScore.toFixed(3)}
                </span>
              ))}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={rescan}>
              Rescan
            </button>
            <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={onClose}>
              Discard
            </button>
            {!alreadyOptimal && (
              <button
                type="button"
                className={`px-2 py-1 rounded ${primaryBtn} disabled:opacity-50`}
                onClick={() => {
                  setApplyError(null);
                  applyMutation.mutate({ startS: proposedStartS, endS: proposedEndS });
                }}
                disabled={applyMutation.isPending}
              >
                {applyMutation.isPending ? 'Applying...' : 'Apply'}
              </button>
            )}
          </div>
          {applyError && <p className="text-destructive">{applyError}</p>}
        </div>
      )}
    </div>
  );
}

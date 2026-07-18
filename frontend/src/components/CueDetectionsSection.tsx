import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Play, Pause } from 'lucide-react';
import CollapsibleSection from './CollapsibleSection';
import { cueTypeLabel, type CueTemplateType } from '../api/cueTemplates';
import { setCueDetectionVerdict, type CueVerdict } from '../api/cueDetections';
import type { CueDetection } from '../api/types';
import { episodeOriginalUrl } from '../api/feeds';
import { formatTimestamp } from '../utils/format';
import { useAuditionPlayer } from '../hooks/useAuditionPlayer';
import { btnDestructive, btnPrimary } from './buttonStyles';

interface CueDetectionsSectionProps {
  slug: string;
  episodeId: string;
  detections: CueDetection[];
}

type OutcomeMeta = { label: string; className: string; title: string };

const OUTCOME_META: Record<CueDetection['outcome'], OutcomeMeta> = {
  pair: {
    label: 'Paired',
    className: 'bg-violet-500/20 text-violet-600 dark:text-violet-400',
    title: 'Two cues bracketed and created an ad',
  },
  snap: {
    label: 'Snapped',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
    title: 'Moved an ad edge onto this cue',
  },
  none: {
    label: 'LLM cue',
    className: 'bg-muted text-muted-foreground',
    title: 'Sent to the model as evidence; did not move an ad edge',
  },
  below_threshold: {
    label: 'missed - below threshold',
    className: 'bg-amber-500/15 text-warning',
    title: 'Scored just under the feed threshold; never a signal, never affected a cut',
  },
};

// Defensive fallback for an outcome the server adds before the frontend knows
// it (forward-compat), so the row still renders instead of throwing.
const UNKNOWN_OUTCOME: OutcomeMeta = {
  label: 'unknown',
  className: 'bg-muted text-muted-foreground',
  title: 'Unrecognized outcome',
};

function CueDetectionsSection({ slug, episodeId, detections }: CueDetectionsSectionProps) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: ({ id, verdict }: { id: number; verdict: CueVerdict }) =>
      setCueDetectionVerdict(id, verdict),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
  });

  const hasPending = detections.some((d) => d.verdict === 'pending');

  // Audition a match: shared windowed player, one row sounds at a time.
  const audioUrl = episodeOriginalUrl(slug, episodeId);
  const { playingKey, toggle: toggleMatch, audioElement } = useAuditionPlayer(audioUrl);

  return (
    <CollapsibleSection
      title="Cue Matches"
      subtitle="Confirm or reject each template cue match"
      defaultOpen={hasPending}
      storageKey={`episode-cue-detections-${episodeId}`}
      headerRight={
        <span className="px-2 py-0.5 text-xs rounded-full bg-secondary text-secondary-foreground">
          {detections.length}
        </span>
      }
    >
      <p className="text-sm text-muted-foreground mb-4">
        Confirm a match on a real ad boundary, or reject a false one. Verdicts tune the feed's cues; they never add or remove ads.
      </p>
      <div className="space-y-2">
        {detections.map((d) => {
          const outcome = OUTCOME_META[d.outcome] ?? UNKNOWN_OUTCOME;
          const pending = mutation.isPending && mutation.variables?.id === d.id;
          return (
            <div
              key={d.id}
              className="p-3 bg-secondary/40 rounded-lg border border-border"
            >
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => toggleMatch(String(d.id), audioUrl, d.start_s, d.end_s)}
                    aria-label={playingKey === String(d.id) ? 'Pause match' : 'Play this match'}
                    title={playingKey === String(d.id) ? 'Pause' : 'Play this match'}
                    className={`p-1.5 rounded-full ${btnPrimary} transition-colors shrink-0 touch-manipulation`}
                  >
                    {playingKey === String(d.id) ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                  </button>
                  <span className="font-mono text-sm text-foreground">
                    {formatTimestamp(d.start_s)} - {formatTimestamp(d.end_s)}
                  </span>
                  {d.cue_type && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground">
                      {cueTypeLabel(d.cue_type as CueTemplateType)}
                    </span>
                  )}
                  <span
                    title={outcome.title}
                    className={`px-1.5 py-0.5 text-xs rounded font-medium ${outcome.className}`}
                  >
                    {outcome.label}
                  </span>
                  {d.match_score != null && (
                    <span className="text-xs text-muted-foreground">
                      Match {Math.round(d.match_score * 100)}%
                    </span>
                  )}
                  {d.outcome === 'none' && d.unused_reason && (
                    <span className="text-xs text-muted-foreground italic">
                      {d.unused_reason.replace(/_/g, ' ')}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {d.outcome === 'below_threshold' ? (
                    <span
                      className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground"
                      title="Informational; not counted in stats"
                    >
                      informational
                    </span>
                  ) : d.verdict === 'pending' ? (
                    <>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'confirmed' })}
                        disabled={pending}
                        className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-green-600 hover:bg-green-700 active:bg-green-800 text-white disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'rejected' })}
                        disabled={pending}
                        className={`px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium ${btnDestructive} active:bg-destructive/80 disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0`}
                      >
                        Reject
                      </button>
                    </>
                  ) : (
                    <>
                      <span
                        className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                          d.verdict === 'confirmed'
                            ? 'bg-success/20 text-success'
                            : 'bg-red-500/20 text-red-600 dark:text-red-400'
                        }`}
                      >
                        {d.verdict === 'confirmed' ? 'Confirmed' : 'Rejected'}
                      </span>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'pending' })}
                        disabled={pending}
                        className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded border border-border text-muted-foreground hover:bg-secondary disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                      >
                        Reset
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {audioElement}
    </CollapsibleSection>
  );
}

export default CueDetectionsSection;

import { useState } from 'react';
import type { ProcessingEpisode } from '../../api/settings';
import CollapsibleSection from '../../components/CollapsibleSection';
import { btnDestructive } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { getStageLabel } from '../../utils/processingStage';

const STORAGE_KEY = 'settings-section-processing-queue';

// Queued rows shown before the "Show all" toggle. The backlog can run to
// hundreds of episodes after a bulk reprocess, which would otherwise bury
// every section below this one.
const QUEUE_PREVIEW_LIMIT = 10;

interface ProcessingQueueSectionProps {
  processingEpisodes: ProcessingEpisode[] | undefined;
  onCancel: (params: { slug: string; episodeId: string }) => void;
  cancelIsPending: boolean;
  /** `slug:episodeId` of the row a cancel is in flight for, if any. */
  cancelingKey?: string | null;
}

function episodeKey(episode: ProcessingEpisode): string {
  return `${episode.slug}:${episode.episodeId}`;
}

function ProcessingQueueSection({
  processingEpisodes,
  onCancel,
  cancelIsPending,
  cancelingKey,
}: ProcessingQueueSectionProps) {
  const [showAllQueued, setShowAllQueued] = useState(false);

  const episodes = processingEpisodes ?? [];
  const active = episodes.filter((e) => e.stage !== 'queued');
  const queued = episodes.filter((e) => e.stage === 'queued');
  const hasProcessing = episodes.length > 0;

  const visibleQueued = showAllQueued ? queued : queued.slice(0, QUEUE_PREVIEW_LIMIT);
  const hiddenQueued = queued.length - visibleQueued.length;
  // The API caps how many rows it returns, so the backlog can be larger than
  // what we can list.
  const queueTotal = queued[0]?.queueTotal ?? queued.length;
  const beyondResponse = queueTotal - queued.length;

  // Write synchronously (before key-triggered remount) so the new
  // CollapsibleSection reads it. Tracked in state so we only write on
  // transitions, not every 5s poll cycle.
  const [prevHasProcessing, setPrevHasProcessing] = useState(false);
  if (hasProcessing !== prevHasProcessing) {
    setPrevHasProcessing(hasProcessing);
    if (hasProcessing) {
      localStorage.setItem(STORAGE_KEY, 'true');
    }
  }

  const cancelButton = (episode: ProcessingEpisode) => {
    const isCanceling = cancelIsPending && cancelingKey === episodeKey(episode);
    return (
      <button
        onClick={() => onCancel({ slug: episode.slug, episodeId: episode.episodeId })}
        disabled={cancelIsPending}
        className={`px-3 py-1 text-sm rounded ${btnDestructive} disabled:opacity-50 transition-colors ml-4 shrink-0 ${focusRing}`}
      >
        {isCanceling ? 'Canceling...' : 'Cancel'}
      </button>
    );
  };

  return (
    <CollapsibleSection
      title="Processing Queue"
      storageKey={STORAGE_KEY}
      key={hasProcessing ? 'processing-active' : 'processing-idle'}
    >
      {hasProcessing ? (
        <div className="space-y-4">
          {active.length > 0 && (
            <div className="space-y-2">
              {active.map((episode) => (
                <div
                  key={episodeKey(episode)}
                  className="bg-secondary/50 rounded-lg p-4 flex justify-between items-center"
                >
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-foreground truncate">{episode.title}</p>
                    <p className="text-sm text-muted-foreground truncate">
                      {episode.podcast}
                      {episode.stage ? ` · ${getStageLabel(episode.stage)}` : ''}
                    </p>
                  </div>
                  {cancelButton(episode)}
                </div>
              ))}
            </div>
          )}

          {queued.length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-medium text-muted-foreground">
                Waiting ({queueTotal})
              </p>
              {visibleQueued.map((episode) => (
                <div
                  key={episodeKey(episode)}
                  className="bg-secondary/30 rounded-lg px-4 py-2.5 flex justify-between items-center"
                >
                  <span className="text-sm text-muted-foreground tabular-nums w-6 shrink-0">
                    {episode.queuePosition}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{episode.title}</p>
                    <p className="text-xs text-muted-foreground truncate">{episode.podcast}</p>
                  </div>
                  {cancelButton(episode)}
                </div>
              ))}
              {(hiddenQueued > 0 || showAllQueued) && (
                <button
                  onClick={() => setShowAllQueued(!showAllQueued)}
                  className={`text-sm text-primary hover:underline rounded ${focusRing}`}
                >
                  {showAllQueued ? 'Show fewer' : `Show all ${queued.length}`}
                </button>
              )}
              {beyondResponse > 0 && showAllQueued && (
                <p className="text-xs text-muted-foreground">
                  +{beyondResponse} further back in the queue
                </p>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No episodes processing or queued</p>
      )}
    </CollapsibleSection>
  );
}

export default ProcessingQueueSection;

import { useState } from 'react';
import { Minus, Plus } from 'lucide-react';
import type { ProcessingEpisode } from '../../api/settings';
import CollapsibleSection from '../../components/CollapsibleSection';
import { Pagination } from '../../components/Pagination';
import { btnDestructive, btnGhost } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { getStageLabel } from '../../utils/processingStage';

const STORAGE_KEY = 'settings-section-processing-queue';

// Rows per page of the waiting list. Page state lives in the host
// (Settings.tsx) because this panel remounts on idle<->active transitions.
export const QUEUE_PAGE_SIZE = 25;

interface ProcessingQueueSectionProps {
  processingEpisodes: ProcessingEpisode[] | undefined;
  onCancel: (params: { slug: string; episodeId: string }) => void;
  cancelIsPending: boolean;
  /** `slug:episodeId` of the row a cancel is in flight for, if any. */
  cancelingKey?: string | null;
  /** 1-based page of the waiting list. */
  queuePage: number;
  onQueuePage: (page: number) => void;
  onPriorityChange: (params: { slug: string; episodeId: string; priority: number }) => void;
  priorityIsPending: boolean;
}

function episodeKey(episode: ProcessingEpisode): string {
  return `${episode.slug}:${episode.episodeId}`;
}

function ProcessingQueueSection({
  processingEpisodes,
  onCancel,
  cancelIsPending,
  cancelingKey,
  queuePage,
  onQueuePage,
  onPriorityChange,
  priorityIsPending,
}: ProcessingQueueSectionProps) {
  const episodes = processingEpisodes ?? [];
  const active = episodes.filter((e) => e.stage !== 'queued');
  const queued = episodes.filter((e) => e.stage === 'queued');
  const hasProcessing = episodes.length > 0;

  // The API reports the whole backlog regardless of the page window.
  const queueTotal = queued[0]?.queueTotal ?? queued.length;
  const totalPages = Math.max(1, Math.ceil(queueTotal / QUEUE_PAGE_SIZE));
  const page = Math.min(queuePage, totalPages);

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
        className={`px-3 py-1 text-sm rounded ${btnDestructive} disabled:opacity-50 transition-colors shrink-0 ${focusRing}`}
      >
        {isCanceling ? 'Canceling...' : 'Cancel'}
      </button>
    );
  };

  const priorityStepper = (episode: ProcessingEpisode) => {
    // Display-queue-only entries have no DB row to update.
    if (episode.queueId == null || episode.priority == null) return null;
    const priority = (delta: number) =>
      onPriorityChange({
        slug: episode.slug,
        episodeId: episode.episodeId,
        priority: episode.priority! + delta,
      });
    return (
      <div className="flex items-center shrink-0" aria-label={`Priority ${episode.priority}`}>
        <button
          onClick={() => priority(-1)}
          disabled={priorityIsPending}
          aria-label={`Decrease priority for ${episode.title}`}
          className={`h-8 w-8 inline-flex items-center justify-center rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          <Minus className="w-4 h-4" />
        </button>
        <span className="w-8 text-center text-xs tabular-nums text-muted-foreground">
          {episode.priority}
        </span>
        <button
          onClick={() => priority(1)}
          disabled={priorityIsPending}
          aria-label={`Increase priority for ${episode.title}`}
          className={`h-8 w-8 inline-flex items-center justify-center rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>
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
              {queued.map((episode) => (
                <div
                  key={episodeKey(episode)}
                  className="bg-secondary/30 rounded-lg px-4 py-2.5 flex items-center gap-3"
                >
                  <span className="text-sm text-muted-foreground tabular-nums w-10 shrink-0 text-right">
                    {episode.queuePosition}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{episode.title}</p>
                    <p className="text-xs text-muted-foreground truncate">{episode.podcast}</p>
                  </div>
                  {priorityStepper(episode)}
                  {cancelButton(episode)}
                </div>
              ))}
              <Pagination
                page={page}
                totalPages={totalPages}
                total={queueTotal}
                onPage={onQueuePage}
              />
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

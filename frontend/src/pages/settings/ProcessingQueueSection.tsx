import { useEffect, useState } from 'react';
import { Minus, Plus } from 'lucide-react';
import type { ProcessingEpisode } from '../../api/settings';
import CollapsibleSection from '../../components/CollapsibleSection';
import { Pagination } from '../../components/Pagination';
import NumberInput from '../../components/NumberInput';
import { btnDestructive, btnGhost } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { getStageLabel } from '../../utils/processingStage';

const STORAGE_KEY = 'settings-section-processing-queue';

// Rows per page of the waiting list. Page state lives in the host
// (Settings.tsx) because this panel remounts on idle<->active transitions.
export const QUEUE_PAGE_SIZE = 25;

// Priority bounds mirror QUEUE_PRIORITY_MIN/MAX on the endpoint. The step is
// 5 so a row clears the fresh-episode boost in one click and the manual boost
// in four, rather than twenty.
const PRIORITY_MIN = -1000;
const PRIORITY_MAX = 1000;
const PRIORITY_STEP = 5;

interface ProcessingQueueSectionProps {
  processingEpisodes: ProcessingEpisode[] | undefined;
  onCancel: (params: { slug: string; episodeId: string }) => void;
  cancelIsPending: boolean;
  /** `slug:episodeId` of the row a cancel is in flight for, if any. */
  cancelingKey?: string | null;
  /** 1-based page of the waiting list. */
  queuePage: number;
  onQueuePage: (page: number) => void;
  onPriorityChange: (
    params: { slug: string; episodeId: string; priority?: number; delta?: number },
  ) => void;
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

  // Every entry carries the whole-backlog total, so a page whose rows all
  // deduped away still pages correctly instead of collapsing to one page.
  const queueTotal = episodes[0]?.queueTotal ?? queued.length;
  const totalPages = Math.max(1, Math.ceil(queueTotal / QUEUE_PAGE_SIZE));
  const page = Math.min(queuePage, totalPages);
  // queueTotal only arrives with the response, so the host cannot clamp
  // before fetching; correct it here when a shrinking backlog strands the
  // pager on a page past the end.
  useEffect(() => {
    if (page !== queuePage) onQueuePage(page);
  }, [page, queuePage, onQueuePage]);

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

  const priorityControl = (episode: ProcessingEpisode) => {
    // Display-queue-only entries carry a null priority: nothing to reorder.
    if (episode.priority == null) return null;
    const row = { slug: episode.slug, episodeId: episode.episodeId };
    // Steps go as a delta so the server adds them; sending priority+5 from a
    // list that refetches every 5s would drop a click made on a stale value.
    const step = (delta: number) => onPriorityChange({ ...row, delta });
    const stepBtn = (delta: number, Icon: typeof Minus, verb: string) => (
      <button
        type="button"
        onClick={() => step(delta)}
        disabled={priorityIsPending}
        aria-label={`${verb} priority for ${episode.title}`}
        className={`h-8 w-8 inline-flex items-center justify-center rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
      >
        <Icon className="w-4 h-4" />
      </button>
    );
    return (
      <div className="flex items-center gap-1 shrink-0">
        {stepBtn(-PRIORITY_STEP, Minus, 'Lower')}
        <NumberInput
          value={episode.priority}
          min={PRIORITY_MIN}
          max={PRIORITY_MAX}
          step={PRIORITY_STEP}
          fallback={episode.priority}
          parse={(s) => parseInt(s, 10)}
          disabled={priorityIsPending}
          commitOn="blur"
          ariaLabel={`Priority for ${episode.title}`}
          onCommit={(priority) => {
            if (priority !== episode.priority) onPriorityChange({ ...row, priority });
          }}
          className="w-16 px-2 py-1 rounded-lg border border-input bg-background text-foreground text-sm text-center tabular-nums"
        />
        {stepBtn(PRIORITY_STEP, Plus, 'Raise')}
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
                  className="bg-secondary/50 rounded-lg p-4 flex justify-between items-center gap-3"
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
                  className="bg-secondary/30 rounded-lg px-4 py-2.5 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3"
                >
                  <div className="flex items-center gap-3 min-w-0 sm:flex-1">
                    <span className="text-sm text-muted-foreground tabular-nums w-10 shrink-0 text-right">
                      {episode.queuePosition}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">{episode.title}</p>
                      <p className="text-xs text-muted-foreground truncate">{episode.podcast}</p>
                    </div>
                  </div>
                  <div className="flex items-center justify-end gap-2 shrink-0">
                    {priorityControl(episode)}
                    {cancelButton(episode)}
                  </div>
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

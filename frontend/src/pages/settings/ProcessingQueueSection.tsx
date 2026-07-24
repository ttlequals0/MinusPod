import { useState } from 'react';
import type { ProcessingEpisode } from '../../api/settings';
import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import { btnDestructive } from '../../components/buttonStyles';

const STORAGE_KEY = 'settings-section-processing-queue';

interface ProcessingQueueSectionProps {
  processingEpisodes: ProcessingEpisode[] | undefined;
  onCancel: (params: { slug: string; episodeId: string }) => void;
  cancelIsPending: boolean;
  rssRefreshIntervalMinutes: number;
  onRssRefreshIntervalMinutesChange: (value: number) => void;
}

function ProcessingQueueSection({
  processingEpisodes,
  onCancel,
  cancelIsPending,
  rssRefreshIntervalMinutes,
  onRssRefreshIntervalMinutesChange,
}: ProcessingQueueSectionProps) {
  const hasProcessing = !!(processingEpisodes && processingEpisodes.length > 0);

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

  return (
    <CollapsibleSection
      title="Processing Queue"
      storageKey={STORAGE_KEY}
      key={hasProcessing ? 'processing-active' : 'processing-idle'}
    >
      <div className="mb-4">
        <label htmlFor="rssRefreshIntervalMinutes" className="block text-sm font-medium text-foreground mb-2">
          Feed refresh interval
        </label>
        <div className="flex items-center gap-3">
          <NumberInput
            id="rssRefreshIntervalMinutes"
            value={rssRefreshIntervalMinutes}
            min={5}
            max={1440}
            step={1}
            fallback={15}
            onCommit={onRssRefreshIntervalMinutesChange}
          />
          <span className="text-sm text-muted-foreground">5 to 1440</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          Minutes between background RSS refresh passes. Default 15.
        </p>
      </div>

      {hasProcessing ? (
        <div className="space-y-2">
          {processingEpisodes.map((episode) => (
            <div
              key={`${episode.slug}-${episode.episodeId}`}
              className="bg-secondary/50 rounded-lg p-4 flex justify-between items-center"
            >
              <div className="flex-1 min-w-0">
                <p className="font-medium text-foreground truncate">{episode.title}</p>
                <p className="text-sm text-muted-foreground">{episode.podcast}</p>
              </div>
              <button
                onClick={() => onCancel({ slug: episode.slug, episodeId: episode.episodeId })}
                disabled={cancelIsPending}
                className={`px-3 py-1 text-sm rounded ${btnDestructive} disabled:opacity-50 transition-colors ml-4 shrink-0`}
              >
                {cancelIsPending ? 'Canceling...' : 'Cancel'}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No episodes currently processing</p>
      )}
    </CollapsibleSection>
  );
}

export default ProcessingQueueSection;

import { useState } from 'react';
import RunLogViewer from './RunLogViewer';
import { btnOutline } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { formatDateTime } from '../utils/format';
import type { EpisodeProcessingRun } from '../api/types';

interface EpisodeLogsCardProps {
  slug: string;
  episodeId: string;
  runs: EpisodeProcessingRun[];
}

function EpisodeLogsCard({ slug, episodeId, runs }: EpisodeLogsCardProps) {
  const [openRun, setOpenRun] = useState<number | null>(null);
  const anyStored = runs.some((run) => run.hasLog);

  return (
    <div>
      {!anyStored && (
        <p className="text-sm text-muted-foreground mb-3">
          No run has stored a log yet. Turn on log storage in Settings, or in this feed's own
          settings.
        </p>
      )}

      <ul className="text-sm">
        {runs.map((run) => (
          <li
            key={`${run.runNumber}-${run.processedAt}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 border-b border-border/50 last:border-b-0"
          >
            <span className="font-medium">Run {run.runNumber}</span>
            <span className="text-muted-foreground">{formatDateTime(run.processedAt)}</span>
            {run.status === 'failed' && <span className="text-destructive">failed</span>}
            <span className="ml-auto">
              {run.hasLog ? (
                <button
                  onClick={() => setOpenRun(run.runNumber)}
                  aria-label={`View log for run ${run.runNumber}`}
                  className={`px-3 py-1 text-sm rounded ${btnOutline} transition-colors ${focusRing}`}
                >
                  View log
                </button>
              ) : (
                <span className="text-muted-foreground">Not stored</span>
              )}
            </span>
          </li>
        ))}
      </ul>

      {openRun !== null && (
        <RunLogViewer
          slug={slug}
          episodeId={episodeId}
          runNumber={openRun}
          onClose={() => setOpenRun(null)}
        />
      )}
    </div>
  );
}

export default EpisodeLogsCard;

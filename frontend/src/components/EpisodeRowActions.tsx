import { useMutation, useQueryClient } from '@tanstack/react-query';
import { reprocessEpisode } from '../api/feeds';
import { getErrorMessage, jobStateOf } from '../api/client';
import type { JobState } from '../api/types';
import { isActionBlocked } from '../utils/processingStage';
import { applyEpisodeJobState, jobStateFromError } from '../utils/jobStateCache';
import DropdownMenu from './DropdownMenu';
import { btnPrimary } from './buttonStyles';
import { cardActionBtn } from './rowActionStyles';

interface EpisodeRowActionsProps {
  feedSlug: string;
  episodeId: string;
  status: string;
  jobState?: JobState;
  // Stable across a reprocess, unlike status; absent on older cached rows.
  hasBeenProcessed?: boolean;
}

// Compact process/reprocess control for a dashboard group row. Scoped to the
// two modes a bounded episode summary can support (mirrors EpisodeDetail's
// menu, minus llm/recut/chapters which need fields the summary doesn't carry).
function EpisodeRowActions({
  feedSlug, episodeId, status, jobState, hasBeenProcessed,
}: EpisodeRowActionsProps) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full') => reprocessEpisode(feedSlug, episodeId, mode),
    onSuccess: (result) => {
      applyEpisodeJobState(queryClient, feedSlug, [episodeId], jobStateOf(result));
    },
    // A 409 carries the state that refused the click; apply it so the control
    // stops offering an action the server has already rejected.
    onError: (error) => {
      applyEpisodeJobState(queryClient, feedSlug, [episodeId], jobStateFromError(error));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      queryClient.invalidateQueries({ queryKey: ['episodes', feedSlug] });
    },
  });

  const blocked = isActionBlocked(jobState, mutation.isPending);
  const processed = hasBeenProcessed ?? status === 'completed';
  const baseLabel = processed ? 'Reprocess' : 'Process';
  const errorMessage = mutation.isError
    ? getErrorMessage(mutation.error, 'Could not start processing.')
    : null;

  return (
    <div className="flex items-center gap-2">
      {errorMessage && (
        <span
          role="alert"
          title={errorMessage}
          className="px-2 py-0.5 text-xs rounded whitespace-nowrap bg-destructive/20 text-destructive cursor-help"
        >
          Failed
        </span>
      )}
      <DropdownMenu
        // A fixed min-width plus a flex-1 centered label keeps every row's
        // button (and chevron) the same width and aligned, whether the label
        // is "Process" or "Reprocess". Height comes from the shared 44px
        // row-action recipe so the control fills a touch row.
        triggerLabel={<span className="flex-1 text-center">{baseLabel}</span>}
        triggerClassName={`${cardActionBtn} ${btnPrimary} disabled:opacity-50 disabled:cursor-not-allowed transition-colors min-w-[96px] gap-1`}
        chevronClassName="w-3 h-3"
        disabled={blocked}
        title={`${baseLabel} episode`}
        items={[
          { title: baseLabel, subtitle: 'Use patterns + AI', onClick: () => mutation.mutate('reprocess') },
          { title: 'Full Analysis', subtitle: 'Skip patterns, AI only', onClick: () => mutation.mutate('full') },
        ]}
      />
    </div>
  );
}

export default EpisodeRowActions;

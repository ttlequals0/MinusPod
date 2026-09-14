import { useMutation, useQueryClient } from '@tanstack/react-query';
import { reprocessEpisode } from '../api/feeds';
import { isActionBlocked } from '../utils/processingStage';
import DropdownMenu from './DropdownMenu';
import { btnPrimary } from './buttonStyles';

interface EpisodeRowActionsProps {
  feedSlug: string;
  episodeId: string;
  status: string;
  jobState?: 'idle' | 'submitting' | 'queued' | 'processing';
}

// Compact process/reprocess control for a dashboard group row. Scoped to the
// two modes a bounded episode summary can support (mirrors EpisodeDetail's
// menu, minus llm/recut/chapters which need fields the summary doesn't carry).
function EpisodeRowActions({ feedSlug, episodeId, status, jobState }: EpisodeRowActionsProps) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full') => reprocessEpisode(feedSlug, episodeId, mode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feeds'] }),
  });

  const blocked = isActionBlocked(jobState, mutation.isPending);
  const neverProcessed = status !== 'completed';
  const baseLabel = neverProcessed ? 'Process' : 'Reprocess';

  return (
    <DropdownMenu
      // A fixed min-width plus a flex-1 centered label keeps every row's
      // button (and chevron) the same width and aligned, whether the label
      // is "Process" or "Reprocess".
      triggerLabel={<span className="flex-1 text-center">{baseLabel}</span>}
      triggerClassName={`px-2 py-1 text-xs ${btnPrimary} rounded disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap min-w-[96px] flex items-center justify-between gap-1`}
      chevronClassName="w-3 h-3"
      disabled={blocked}
      title={`${baseLabel} episode`}
      items={[
        { title: baseLabel, subtitle: 'Use patterns + AI', onClick: () => mutation.mutate('reprocess') },
        { title: 'Full Analysis', subtitle: 'Skip patterns, AI only', onClick: () => mutation.mutate('full') },
      ]}
    />
  );
}

export default EpisodeRowActions;

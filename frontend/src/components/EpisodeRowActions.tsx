import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { bulkEpisodeAction, reprocessEpisode } from '../api/feeds';
import { getErrorMessage, jobStateOf } from '../api/client';
import type { BulkActionResult, JobState } from '../api/types';
import { isActionBlocked } from '../utils/processingStage';
import { applyEpisodeJobState, jobStateFromError } from '../utils/jobStateCache';
import DropdownMenu from './DropdownMenu';
import { Modal } from './Modal';
import { btnDestructive, btnPrimary, btnSecondary } from './buttonStyles';
import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

interface EpisodeRowActionsProps {
  feedSlug: string;
  episodeId: string;
  status: string;
  jobState?: JobState;
  // Stable across a reprocess, unlike status; absent on older cached rows.
  hasBeenProcessed?: boolean;
}

// Compact dashboard menu for the two processing modes supported by a summary.
function EpisodeRowActions({
  feedSlug, episodeId, status, jobState, hasBeenProcessed,
}: EpisodeRowActionsProps) {
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteStatus, setDeleteStatus] = useState<string | null>(null);
  const deleteTitleId = useId();
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

  const deleteMutation = useMutation({
    mutationFn: () => bulkEpisodeAction(feedSlug, [episodeId], 'delete'),
    onSuccess: (result: BulkActionResult) => {
      if (result.queued !== 1 || result.errors.length > 0) {
        setDeleteError(result.errors[0] || 'No files were deleted. The episode may be busy or no longer have downloaded audio.');
        return;
      }
      setConfirmDelete(false);
      setDeleteError(null);
      setDeleteStatus('Downloaded files were deleted. The episode and its history remain.');
    },
    onError: (error) => setDeleteError(getErrorMessage(error, 'Could not delete downloaded episode files.')),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      queryClient.invalidateQueries({ queryKey: ['feed', feedSlug] });
      queryClient.invalidateQueries({ queryKey: ['episodes', feedSlug] });
      queryClient.invalidateQueries({ queryKey: ['episode', feedSlug, episodeId] });
    },
  });

  const blocked = isActionBlocked(jobState, mutation.isPending || deleteMutation.isPending);
  const processed = hasBeenProcessed ?? status === 'completed';
  const baseLabel = processed ? 'Reprocess' : 'Process';
  const deletable = processed
    && ['completed', 'failed', 'permanently_failed', 'deferred'].includes(status);
  const errorMessage = mutation.isError
    ? getErrorMessage(mutation.error, 'Could not start processing.')
    : null;

  return (
    <div className="flex items-center gap-2">
      {errorMessage && (
        <span
          role="alert"
          title={errorMessage}
          className={`${badgeBase} whitespace-nowrap ${tint.destructive} cursor-help`}
        >
          Failed
        </span>
      )}
      <DropdownMenu
        // The 6rem floor aligns row actions; flex-1 centers the label beside the chevron.
        triggerLabel={<span className="flex-1 text-center">Actions</span>}
        triggerClassName={`min-h-11 sm:min-h-0 px-2 py-2 sm:py-1.5 text-xs rounded flex items-center gap-1 min-w-24 whitespace-nowrap touch-manipulation ${btnPrimary} disabled:opacity-50 disabled:cursor-not-allowed transition-colors`}
        chevronClassName="w-3 h-3"
        disabled={blocked}
        title="Episode actions"
        items={[
          { title: baseLabel, subtitle: 'Use patterns + AI', onClick: () => mutation.mutate('reprocess') },
          { title: 'Full Analysis', subtitle: 'Skip patterns, AI only', onClick: () => mutation.mutate('full') },
          ...(deletable ? [{
            title: 'Delete',
            subtitle: 'Free downloaded space',
            onClick: () => {
              mutation.reset();
              deleteMutation.reset();
              setDeleteError(null);
              setDeleteStatus(null);
              setConfirmDelete(true);
            },
          }] : []),
        ]}
      />
      {deleteStatus && <span role="status" className="sr-only">{deleteStatus}</span>}
      {confirmDelete && (
        <Modal
          onClose={() => setConfirmDelete(false)}
          closeOnEscape={!deleteMutation.isPending}
          ariaLabelledBy={deleteTitleId}
          panelClassName="max-w-md w-full"
        >
          <div className="p-5">
            <h2 id={deleteTitleId} className="text-lg font-semibold text-foreground">Delete downloaded episode files?</h2>
            <p className="mt-3 text-sm text-muted-foreground">
              This deletes downloaded audio and resets the episode to Discovered. The episode and processing history remain. Originals uploaded to local feeds are kept.
            </p>
            {deleteError && <p role="alert" className="mt-3 text-sm text-destructive">{deleteError}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                disabled={deleteMutation.isPending}
                className={`min-h-11 px-4 py-2 rounded ${btnSecondary} disabled:opacity-50 ${focusRing}`}
              >Cancel</button>
              <button
                type="button"
                onClick={() => {
                  if (!deletable || isActionBlocked(jobState, mutation.isPending || deleteMutation.isPending)) {
                    setDeleteError('This episode can no longer be deleted from this view.');
                    return;
                  }
                  deleteMutation.mutate();
                }}
                disabled={deleteMutation.isPending || blocked}
                className={`min-h-11 px-4 py-2 rounded ${btnDestructive} disabled:opacity-50 ${focusRing}`}
              >{deleteMutation.isPending ? 'Deleting...' : 'Delete files'}</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default EpisodeRowActions;

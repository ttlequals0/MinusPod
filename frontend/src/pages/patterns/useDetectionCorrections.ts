import { useState } from 'react';
import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../../api/client';
import type { ReviewDetection } from '../../api/detections';
import { reprocessEpisode } from '../../api/feeds';
import { submitCorrection, type PatternCorrection } from '../../api/patterns';

// Refresh what a recut changes, then start it. Logs and resolves false when
// the recut request fails; the caller picks how to surface that.
export async function startEpisodeRecut(
  queryClient: QueryClient, slug: string, episodeId: string,
): Promise<boolean> {
  queryClient.invalidateQueries({ queryKey: ['detections'] });
  queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
  try {
    await reprocessEpisode(slug, episodeId, 'recut');
    return true;
  } catch (error) {
    console.error('Failed to trigger recut:', error);
    return false;
  }
}

interface Options {
  // Stops windowed preview playback before a refetch drops the playing row,
  // the same guard the episode page uses.
  stopAudition: () => void;
  onSettled?: () => void;
}

// Correction submission shared by the Ad Review and Detected Ads tabs: both
// file the same corrections against the same endpoint and both need the recut
// that follows, so the mutation, the recut trigger, and the error surface live
// here rather than once per tab.
export function useDetectionCorrections({ stopAudition, onSettled }: Options) {
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);

  // Fire-and-forget recut; a failure surfaces through actionError.
  const triggerRecut = (d: Pick<ReviewDetection, 'feedSlug' | 'episodeId'>) => {
    void startEpisodeRecut(queryClient, d.feedSlug, d.episodeId).then((ok) => {
      if (!ok) setActionError('Saved, but the recut did not start. The change applies on the next reprocess.');
    });
  };

  const mutation = useMutation({
    mutationFn: async (args: {
      d: ReviewDetection;
      correction: PatternCorrection;
    }) => {
      await submitCorrection(args.d.feedSlug, args.d.episodeId, args.correction);
    },
    onMutate: () => {
      setActionError(null);
      stopAudition();
    },
    onSuccess: () => {
      onSettled?.();
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      // The server stamps the episode when a decision needs new audio; the
      // Apply button cuts them in one pass per episode.
      queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
    },
    onError: (error) => {
      console.error('Failed to save correction:', error);
      // Surface what the server said: some refusals are permanent, and
      // "try again" would send the reader in circles.
      setActionError(getErrorMessage(error, 'Failed to save correction.'));
    },
  });

  const originalAdOf = (d: ReviewDetection) => ({
    start: d.start,
    end: d.end,
    pattern_id: d.patternId ?? undefined,
    confidence: d.confidence ?? undefined,
    reason: d.reason ?? undefined,
    sponsor: d.sponsor ?? undefined,
  });

  // Confirming a detection that was left in the audio has to cut it, so the
  // recut needs the retained original.
  const approve = (d: ReviewDetection) => mutation.mutate({
    d,
    correction: { type: 'confirm', original_ad: originalAdOf(d) },
  });

  // Rejecting one that was cut has to put the audio back, which also needs the
  // original. Rejecting one that was never cut changes no audio.
  const dismiss = (d: ReviewDetection) => mutation.mutate({
    d,
    correction: { type: 'reject', original_ad: originalAdOf(d) },
  });

  // Category drives which segment action applies, so this is how a span the
  // feed is currently keeping gets cut (or the reverse).
  const recategorize = (d: ReviewDetection, category: string | null) => mutation.mutate({
    d,
    correction: { type: 'recategorize', original_ad: originalAdOf(d), category },
  });

  // Bounds are optional to match AdReviewSubmit, whose adjust variant carries
  // them optionally; the correction payload accepts undefined the same way.
  const adjust = (
    d: ReviewDetection, adjustedStart?: number, adjustedEnd?: number,
    sponsor?: string,
  ) => mutation.mutate({
    d,
    correction: {
      type: 'adjust',
      original_ad: originalAdOf(d),
      adjusted_start: adjustedStart,
      adjusted_end: adjustedEnd,
      sponsor,
    },
  });

  return {
    approve,
    dismiss,
    recategorize,
    adjust,
    triggerRecut,
    busy: mutation.isPending,
    actionError,
    setActionError,
  };
}

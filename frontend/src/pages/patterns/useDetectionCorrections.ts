import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ReviewDetection } from '../../api/detections';
import { reprocessEpisode } from '../../api/feeds';
import { submitCorrection, type PatternCorrection } from '../../api/patterns';

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

  const mutation = useMutation({
    mutationFn: async (args: {
      d: ReviewDetection;
      correction: PatternCorrection;
      recut: boolean;
    }) => {
      await submitCorrection(args.d.feedSlug, args.d.episodeId, args.correction);
    },
    onMutate: () => {
      setActionError(null);
      stopAudition();
    },
    onSuccess: (_, vars) => {
      onSettled?.();
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      if (vars.recut) {
        reprocessEpisode(vars.d.feedSlug, vars.d.episodeId, 'recut').catch(
          (error) => {
            console.error('Failed to trigger recut:', error);
            setActionError('Saved, but the recut did not start. The change applies on the next reprocess.');
          },
        );
      }
    },
    onError: (error) => {
      console.error('Failed to save correction:', error);
      setActionError('Failed to save correction. Try again.');
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
    recut: d.hasOriginalAudio,
  });

  // Rejecting one that was cut has to put the audio back, which also needs the
  // original. Rejecting one that was never cut changes no audio.
  const dismiss = (d: ReviewDetection, recut: boolean) => mutation.mutate({
    d,
    correction: { type: 'reject', original_ad: originalAdOf(d) },
    recut,
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
    recut: false,
  });

  return {
    approve,
    dismiss,
    adjust,
    busy: mutation.isPending,
    actionError,
    setActionError,
  };
}

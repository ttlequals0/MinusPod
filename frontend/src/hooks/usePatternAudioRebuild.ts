import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  describeFingerprintSkip, patternAudioKey, rebuildPatternAudio, type AudioRebuildResult,
} from '../api/patterns';

export interface AudioSourceSpan {
  podcastSlug: string;
  episodeId: string;
  start: number;
  end: number;
}

/** Rebuilds a pattern's audio from an episode span; an answer that stored nothing is an error carrying the reason. */
export function usePatternAudioRebuild(patternId: number, onStored?: (result: AudioRebuildResult) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (span: AudioSourceSpan) => {
      const result = await rebuildPatternAudio(patternId, span);
      if (!result.fingerprintStored) {
        throw new Error(describeFingerprintSkip(result.fingerprintSkippedReason ?? 'fingerprint_failed'));
      }
      return result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      queryClient.invalidateQueries({ queryKey: patternAudioKey(patternId) });
      queryClient.invalidateQueries({ queryKey: ['pattern-audio-matches', patternId] });
      onStored?.(result);
    },
  });
}

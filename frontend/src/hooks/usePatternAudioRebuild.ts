import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  describeFingerprintSkip, invalidatePatternAudio, rebuildPatternAudio, type AudioRebuildResult, type AudioSpan,
} from '../api/patterns';

/** Rebuilds a pattern's audio from an episode span; an answer that stored nothing is an error carrying the reason. */
export function usePatternAudioRebuild(patternId: number, onStored?: (result: AudioRebuildResult) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (span: AudioSpan) => {
      const result = await rebuildPatternAudio(patternId, span);
      if (!result.fingerprintStored) {
        throw new Error(describeFingerprintSkip(result.fingerprintSkippedReason ?? 'fingerprint_failed'));
      }
      return result;
    },
    onSuccess: (result) => {
      invalidatePatternAudio(queryClient, patternId);
      onStored?.(result);
    },
  });
}

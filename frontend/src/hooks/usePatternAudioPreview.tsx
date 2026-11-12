import { useEffect, useState } from 'react';
import { explainPreviewFailure, patternAudioPreviewUrl } from '../api/patterns';
import { useAuditionPlayer } from './useAuditionPlayer';

const SOURCE_KEY = 'source';

// One shared player for a pattern's source clip and its matches, with a plain message when a clip cannot load.
export function usePatternAudioPreview(patternId: number) {
  const player = useAuditionPlayer();
  const { failedKey } = player;
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null);

  useEffect(() => {
    if (!failedKey) return;
    const matchId = failedKey === SOURCE_KEY ? undefined : Number(failedKey);
    const url = patternAudioPreviewUrl(patternId, matchId);
    if (!url) return;
    let live = true;
    void explainPreviewFailure(url).then((text) => {
      if (live) setFailure({ key: failedKey, text });
    });
    return () => { live = false; };
  }, [failedKey, patternId]);

  const toggle = (matchId?: number) => {
    setFailure(null);
    const key = matchId === undefined ? SOURCE_KEY : String(matchId);
    // The clip is the span itself, so it plays to its own end.
    player.toggle(key, patternAudioPreviewUrl(patternId, matchId), 0, Number.POSITIVE_INFINITY);
  };

  const keyOf = (matchId?: number) => (matchId === undefined ? SOURCE_KEY : String(matchId));
  return {
    audioElement: player.audioElement,
    stop: player.stop,
    toggle,
    isPlaying: (matchId?: number) => player.playingKey === keyOf(matchId),
    errorFor: (matchId?: number) => (failure?.key === keyOf(matchId) && failedKey === failure.key ? failure.text : null),
  };
}

export type PatternAudioPreview = ReturnType<typeof usePatternAudioPreview>;

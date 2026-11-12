import { useCallback, useEffect, useRef, useState } from 'react';
import { describePreviewFailure, fetchPatternAudioClip, patternAudioPreviewUrl } from '../api/patterns';

const SOURCE_KEY = 'source';
const keyOf = (matchId?: number) => (matchId === undefined ? SOURCE_KEY : String(matchId));
const DECODE_FAILURE = 'Could not play this audio. Try again.';

// One player for a pattern's source clip and its matches. A clip is fetched once as a
// blob, so a failure carries its status and a retry is a new request.
export function usePatternAudioPreview(patternId: number) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null);

  const revoke = () => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
  };

  const stop = () => {
    abortRef.current?.abort();
    audioRef.current?.pause();
    setPlayingKey(null);
    setLoadingKey(null);
  };

  useEffect(() => () => {
    abortRef.current?.abort();
    audioRef.current?.pause();
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
  }, []);

  const start = async (matchId?: number) => {
    const key = keyOf(matchId);
    abortRef.current?.abort();
    audioRef.current?.pause();
    const controller = new AbortController();
    abortRef.current = controller;
    setFailure(null);
    setPlayingKey(null);
    setLoadingKey(key);
    try {
      const blob = await fetchPatternAudioClip(patternId, matchId, controller.signal);
      const audio = audioRef.current;
      if (controller.signal.aborted || !audio) return;
      revoke();
      objectUrlRef.current = URL.createObjectURL(blob);
      audio.src = objectUrlRef.current;
      setLoadingKey(null);
      setPlayingKey(key);
      await audio.play();
    } catch (error) {
      if (controller.signal.aborted) return;
      setLoadingKey(null);
      setPlayingKey(null);
      setFailure({ key, text: describePreviewFailure(error) });
    }
  };

  const toggle = (matchId?: number) => {
    const key = keyOf(matchId);
    if (playingKey === key || loadingKey === key) {
      stop();
      return;
    }
    // The demo build has no audio.
    if (!patternAudioPreviewUrl(patternId, matchId)) return;
    void start(matchId);
  };

  const attachAudio = useCallback((el: HTMLAudioElement | null) => {
    if (el) audioRef.current = el;
  }, []);

  const audioElement = (
    <audio
      ref={attachAudio}
      preload="none"
      onEnded={() => setPlayingKey(null)}
      onError={() => {
        if (!playingKey) return;
        setFailure({ key: playingKey, text: DECODE_FAILURE });
        setPlayingKey(null);
      }}
    />
  );

  return {
    audioElement,
    stop,
    toggle,
    isPlaying: (matchId?: number) => playingKey === keyOf(matchId),
    isLoading: (matchId?: number) => loadingKey === keyOf(matchId),
    errorFor: (matchId?: number) => (failure?.key === keyOf(matchId) ? failure.text : null),
  };
}

export type PatternAudioPreview = ReturnType<typeof usePatternAudioPreview>;

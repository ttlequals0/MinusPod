import { useCallback, useEffect, useRef, useState } from 'react';
import { explainPreviewFailure, patternAudioPreviewUrl } from '../api/patterns';

const SOURCE_KEY = 'source';
const keyOf = (matchId?: number) => (matchId === undefined ? SOURCE_KEY : String(matchId));
const NOT_ALLOWED = 'Tap play again to start the audio.';

type Active = { key: string; phase: 'loading' | 'playing' };

// One player for a pattern's source clip and its matches. play() runs inside the tap so
// WebKit allows it; the element streams the clip and a failure is explained by one extra request.
export function usePatternAudioPreview(patternId: number) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const loadedKeyRef = useRef<string | null>(null);
  const activeRef = useRef<Active | null>(null);
  const tokenRef = useRef(0);
  const failedTokenRef = useRef(0);
  const explainRef = useRef<AbortController | null>(null);
  const [active, setActiveState] = useState<Active | null>(null);
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null);

  const setActive = (next: Active | null) => {
    activeRef.current = next;
    setActiveState(next);
  };

  const releaseAudio = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
  };

  const stop = () => {
    explainRef.current?.abort();
    tokenRef.current += 1;
    audioRef.current?.pause();
    setActive(null);
  };

  useEffect(() => () => {
    explainRef.current?.abort();
    releaseAudio();
  }, []);

  const fail = async (token: number, key: string, url: string, error?: unknown) => {
    if (failedTokenRef.current === token) return;
    failedTokenRef.current = token;
    loadedKeyRef.current = null;
    releaseAudio();
    setActive(null);
    if (error instanceof DOMException && error.name === 'NotAllowedError') {
      setFailure({ key, text: NOT_ALLOWED });
      return;
    }
    const controller = new AbortController();
    explainRef.current = controller;
    const text = await explainPreviewFailure(url, controller.signal);
    if (!controller.signal.aborted) setFailure({ key, text });
  };

  const start = (matchId?: number) => {
    const audio = audioRef.current;
    const url = patternAudioPreviewUrl(patternId, matchId);
    // The demo build has no audio.
    if (!audio || !url) return;
    const key = keyOf(matchId);
    explainRef.current?.abort();
    audio.pause();
    const token = ++tokenRef.current;
    setFailure(null);
    setActive({ key, phase: 'loading' });
    if (loadedKeyRef.current === key) {
      audio.currentTime = 0;
    } else {
      audio.src = url;
      loadedKeyRef.current = key;
    }
    audio.play()?.catch((error: unknown) => {
      if (token !== tokenRef.current || (error instanceof DOMException && error.name === 'AbortError')) return;
      void fail(token, key, url, error);
    });
  };

  const toggle = (matchId?: number) => {
    if (active?.key === keyOf(matchId)) {
      stop();
      return;
    }
    start(matchId);
  };

  const attachAudio = useCallback((el: HTMLAudioElement | null) => {
    if (el) audioRef.current = el;
  }, []);

  const setPhase = (phase: Active['phase']) => {
    const current = activeRef.current;
    if (current) setActive({ key: current.key, phase });
  };

  const audioElement = (
    <audio
      ref={attachAudio}
      preload="none"
      onLoadStart={() => setPhase('loading')}
      onWaiting={() => setPhase('loading')}
      onPlaying={() => setPhase('playing')}
      onEnded={() => setActive(null)}
      // A pause we did not ask for (an OS interruption) must not leave the row on playing.
      onPause={() => { if (activeRef.current?.phase === 'playing') setActive(null); }}
      onError={() => {
        const current = activeRef.current;
        if (!current) return;
        const matchId = current.key === SOURCE_KEY ? undefined : Number(current.key);
        const url = patternAudioPreviewUrl(patternId, matchId);
        if (url) void fail(tokenRef.current, current.key, url);
      }}
    />
  );

  return {
    audioElement,
    stop,
    toggle,
    isPlaying: (matchId?: number) => active?.key === keyOf(matchId) && active.phase === 'playing',
    isLoading: (matchId?: number) => active?.key === keyOf(matchId) && active.phase === 'loading',
    errorFor: (matchId?: number) => (failure?.key === keyOf(matchId) ? failure.text : null),
  };
}

export type PatternAudioPreview = ReturnType<typeof usePatternAudioPreview>;

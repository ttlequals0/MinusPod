import { useEffect, useRef, useState } from 'react';

// Shared windowed-audio player: one hidden <audio> element per consumer plays
// the [startS, endS] window of an original-audio URL, one row at a time.
// Extracted verbatim from CueDetectionsSection's audition logic; the gesture,
// cold-load, and superseded-click behaviors are load-bearing (see 2.18.2).
// Pass preloadSrc when the consumer plays a single known file (preloads its
// metadata like the old inline version); omit it for multi-source consumers.
export function useAuditionPlayer(preloadSrc?: string) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Holds a function that detaches whichever listener is active (the timeupdate
  // stop, or a pending loadedmetadata seek), so a row switch tears down cleanly.
  const cleanupRef = useRef<(() => void) | null>(null);
  // Bumped on every play/stop so a slow cold-load callback for a superseded
  // row bails instead of arming a stray stop listener or seeking the wrong row.
  const reqRef = useRef(0);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  // The source currently loaded in the element. Tracked here (not via string
  // comparison against audio.src) because the browser absolutizes audio.src,
  // making suffix matching against a relative path fragile.
  const srcRef = useRef(preloadSrc);
  if (preloadSrc !== undefined && srcRef.current !== preloadSrc) {
    srcRef.current = preloadSrc;
  }
  // True while a source swap is settling; a swap fires a browser pause event
  // for the old playback, which must not clobber the just-set playingKey.
  const switchingRef = useRef(false);

  const clearStop = () => {
    reqRef.current += 1;
    if (cleanupRef.current) cleanupRef.current();
    cleanupRef.current = null;
  };

  const stop = () => {
    clearStop();
    audioRef.current?.pause();
    setPlayingKey(null);
  };

  const play = (key: string, src: string, startS: number, endS: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    clearStop();
    const req = reqRef.current;
    // Set playing state synchronously (not in play().then) so a second click
    // pauses during a cold load instead of starting a duplicate playback.
    setPlayingKey(key);
    // A different source resets the element to HAVE_NOTHING; the cold-load
    // path below then seeks once the new metadata arrives.
    if (srcRef.current !== src) {
      switchingRef.current = true;
      srcRef.current = src;
      audio.src = src;
    }
    // Pause at the window end; setting currentTime before HAVE_METADATA is
    // dropped by Chrome/Safari, so the seek waits for metadata.
    const armStop = () => {
      const stopAtEnd = () => {
        if (audio.currentTime >= endS) {
          clearStop();
          audio.pause();
          setPlayingKey(null);
        }
      };
      audio.addEventListener('timeupdate', stopAtEnd);
      cleanupRef.current = () => audio.removeEventListener('timeupdate', stopAtEnd);
    };
    const settle = () => { switchingRef.current = false; };
    const fail = () => {
      settle();
      if (reqRef.current === req) setPlayingKey(null);
    };
    if (audio.readyState >= 1) {
      audio.currentTime = startS;
      armStop();
      audio.play().then(settle).catch(fail);
    } else {
      // Metadata not loaded yet. Call play() synchronously so it keeps the
      // click's user gesture (autoplay policy) AND triggers the load; then
      // seek to the window start once metadata arrives. Deferring play() into
      // a loadedmetadata callback loses the gesture and, with no load() kicked
      // off, the metadata may never arrive -- so the click did nothing.
      audio.play().then(() => {
        settle();
        if (reqRef.current !== req) return;  // a newer click took over
        const seek = () => { audio.currentTime = startS; armStop(); };
        if (audio.readyState >= 1) seek();
        else {
          audio.addEventListener('loadedmetadata', seek, { once: true });
          cleanupRef.current = () => audio.removeEventListener('loadedmetadata', seek);
        }
      }).catch(fail);
    }
  };

  const toggle = (key: string, src: string, startS: number, endS: number) => {
    if (playingKey === key) stop();
    else play(key, src, startS, endS);
  };

  useEffect(() => () => { clearStop(); audioRef.current?.pause(); }, []);

  const audioElement = (
    <audio
      ref={audioRef}
      src={preloadSrc}
      preload={preloadSrc ? 'metadata' : 'none'}
      onPause={() => {
        // Suppress the pause fired by an in-flight source swap; explicit stop
        // paths reset playingKey themselves.
        if (!switchingRef.current) setPlayingKey(null);
      }}
      onEnded={() => setPlayingKey(null)}
      onError={() => setPlayingKey(null)}
    />
  );

  return { playingKey, toggle, stop, audioElement };
}

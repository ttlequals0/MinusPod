import { useCallback, useEffect, useState, type RefObject } from 'react';

export interface PlayerChapter {
  startTime: number;
  title: string;
}

export const SPEED_PRESETS = [1, 1.25, 1.5, 1.75, 2, 0.8] as const;
export const RATE_STORAGE_KEY = 'minuspod.playbackRate';
export const SKIP_BACK_SECONDS = 15;
export const SKIP_FORWARD_SECONDS = 30;
const NO_CHAPTERS: PlayerChapter[] = [];
const PREV_CHAPTER_RESTART_SECONDS = 3;

export function nextRate(rate: number): number {
  const i = SPEED_PRESETS.indexOf(rate as (typeof SPEED_PRESETS)[number]);
  return SPEED_PRESETS[(i + 1) % SPEED_PRESETS.length];
}

export function formatRate(rate: number): string {
  return `${rate}x`;
}

export function loadRate(): number {
  try {
    const rate = Number(localStorage.getItem(RATE_STORAGE_KEY));
    return SPEED_PRESETS.includes(rate as (typeof SPEED_PRESETS)[number]) ? rate : 1;
  } catch {
    return 1;
  }
}

export function saveRate(rate: number): void {
  try {
    localStorage.setItem(RATE_STORAGE_KEY, String(rate));
  } catch {
    // Storage unavailable: the rate still applies for this session.
  }
}

// Clamp a seek target to [0, duration]; an unknown duration (0) only clamps at 0.
export function clampSeek(target: number, duration: number): number {
  const upper = duration > 0 ? duration : Infinity;
  return Math.min(Math.max(target, 0), upper);
}

// Last chapter starting at or before the playhead; -1 before the first chapter.
export function currentChapterIndex(chapters: PlayerChapter[], time: number): number {
  let idx = -1;
  for (let i = 0; i < chapters.length && chapters[i].startTime <= time; i++) idx = i;
  return idx;
}

// Restart the current chapter when more than 3 s in, else the previous one; null when at the start.
export function prevChapterTarget(chapters: PlayerChapter[], time: number): number | null {
  const idx = currentChapterIndex(chapters, time);
  if (idx < 0) return null;
  if (time - chapters[idx].startTime > PREV_CHAPTER_RESTART_SECONDS) return chapters[idx].startTime;
  return idx > 0 ? chapters[idx - 1].startTime : null;
}

export function nextChapterTarget(chapters: PlayerChapter[], time: number): number | null {
  const next = chapters[currentChapterIndex(chapters, time) + 1];
  return next ? next.startTime : null;
}

export function parseChapters(json: unknown): PlayerChapter[] {
  const list = (json as { chapters?: unknown } | null)?.chapters;
  if (!Array.isArray(list)) return [];
  return list
    .filter((c): c is PlayerChapter => typeof c?.startTime === 'number' && Number.isFinite(c.startTime) && typeof c?.title === 'string')
    .map((c) => ({ startTime: c.startTime, title: c.title }))
    .sort((a, b) => a.startTime - b.startTime);
}

export function useEpisodePlayer(audioRef: RefObject<HTMLAudioElement | null>, chaptersUrl?: string) {
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRate] = useState(loadRate);
  const [loaded, setLoaded] = useState<{ url: string; list: PlayerChapter[] } | null>(null);
  const [scrubTime, setScrubTime] = useState<number | null>(null);

  useEffect(() => {
    if (!chaptersUrl) return;
    const ctrl = new AbortController();
    fetch(chaptersUrl, { credentials: 'include', signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setLoaded({ url: chaptersUrl, list: parseChapters(json) }))
      .catch(() => {});
    return () => ctrl.abort();
  }, [chaptersUrl]);

  const chapters = loaded && loaded.url === chaptersUrl ? loaded.list : NO_CHAPTERS;

  const applyRate = useCallback(() => {
    if (audioRef.current) audioRef.current.playbackRate = rate;
  }, [audioRef, rate]);
  useEffect(applyRate, [applyRate]);

  const readDuration = () => {
    const d = audioRef.current?.duration ?? 0;
    setDuration(Number.isFinite(d) ? d : 0);
  };

  const seek = useCallback((target: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const t = clampSeek(target, Number.isFinite(audio.duration) ? audio.duration : 0);
    audio.currentTime = t;
    setTime(t);
  }, [audioRef]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) audio.play()?.catch(() => setPlaying(false));
    else audio.pause();
  }, [audioRef]);

  const skip = useCallback((delta: number) => {
    if (audioRef.current) seek(audioRef.current.currentTime + delta);
  }, [audioRef, seek]);

  const cycleRate = useCallback(() => {
    const next = nextRate(rate);
    setRate(next);
    saveRate(next);
  }, [rate]);

  const shownTime = scrubTime ?? time;
  const chapterIndex = currentChapterIndex(chapters, shownTime);
  const prevTarget = prevChapterTarget(chapters, shownTime);
  const nextTarget = nextChapterTarget(chapters, shownTime);

  return {
    playing,
    time: shownTime,
    duration,
    rate,
    chapters,
    chapterIndex,
    canPrevChapter: prevTarget !== null,
    canNextChapter: nextTarget !== null,
    toggle,
    skip,
    seek,
    cycleRate,
    prevChapter: () => prevTarget !== null && seek(prevTarget),
    nextChapter: () => nextTarget !== null && seek(nextTarget),
    setScrubTime,
    audioProps: {
      onPlay: () => setPlaying(true),
      onPause: () => setPlaying(false),
      onEnded: () => setPlaying(false),
      onTimeUpdate: () => setTime(audioRef.current?.currentTime ?? 0),
      onDurationChange: readDuration,
      onLoadedMetadata: () => { readDuration(); applyRate(); },
    },
  };
}

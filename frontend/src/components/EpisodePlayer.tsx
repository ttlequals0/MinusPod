import { useRef, type KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, Gauge, Pause, Play, SkipBack, SkipForward } from 'lucide-react';
import { SKIP_BACK_SECONDS, SKIP_FORWARD_SECONDS, formatRate, useEpisodePlayer } from '../hooks/useEpisodePlayer';
import { formatClock } from '../utils/format';
import { btnBase, btnGhost, btnPrimary } from './buttonStyles';

interface EpisodePlayerProps {
  src: string;
  chaptersUrl?: string;
}

const ghostBtn = `${btnBase} ${btnGhost} touch-manipulation min-h-11 min-w-11`;

export default function EpisodePlayer({ src, chaptersUrl }: EpisodePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const p = useEpisodePlayer(audioRef, chaptersUrl);
  const hasChapters = p.chapters.length > 0;
  const title = p.chapters[Math.max(p.chapterIndex, 0)]?.title;
  const endScrub = () => p.setScrubTime(null);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const target = e.target as HTMLElement;
    if (target.closest('button, input, select, textarea')) return;
    if (e.key === ' ') p.toggle();
    else if (e.key === 'ArrowLeft') p.skip(-SKIP_BACK_SECONDS);
    else if (e.key === 'ArrowRight') p.skip(SKIP_FORWARD_SECONDS);
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="group"
      aria-label="Episode player"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="rounded-lg border border-border bg-card p-3 sm:p-4 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
    >
      <audio ref={audioRef} src={src} preload="metadata" {...p.audioProps} />

      {hasChapters && (
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Previous chapter" disabled={!p.canPrevChapter} onClick={p.prevChapter} className={ghostBtn}>
            <ChevronLeft className="h-5 w-5" aria-hidden="true" />
          </button>
          <p className="min-w-0 flex-1 truncate text-center text-sm font-medium text-foreground" title={title}>{title}</p>
          <button type="button" aria-label="Next chapter" disabled={!p.canNextChapter} onClick={p.nextChapter} className={ghostBtn}>
            <ChevronRight className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="relative mt-1 h-11">
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-2 top-1/2 h-4 -translate-y-1/2">
          {p.duration > 0 && p.chapters.map((c) => c.startTime > 0 && (
            <span
              key={c.startTime}
              className="absolute top-0 h-full w-0.5 -translate-x-1/2 rounded-full bg-foreground/40"
              style={{ left: `${Math.min(c.startTime / p.duration, 1) * 100}%` }}
            />
          ))}
        </div>
        <input
          type="range"
          aria-label="Seek"
          aria-valuetext={`${formatClock(p.time)} of ${formatClock(p.duration)}`}
          min={0}
          max={p.duration || 0}
          step={1}
          value={Math.min(p.time, p.duration || 0)}
          disabled={p.duration <= 0}
          onChange={(e) => { p.setScrubTime(Number(e.target.value)); p.seek(Number(e.target.value)); }}
          onPointerUp={endScrub}
          onKeyUp={endScrub}
          onBlur={endScrub}
          className="absolute inset-0 h-11 w-full cursor-pointer touch-manipulation accent-primary disabled:cursor-default focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring rounded"
        />
      </div>
      <div className="flex justify-between text-xs tabular-nums text-muted-foreground">
        <span>{formatClock(p.time)}</span>
        <span>-{formatClock(p.duration - p.time)}</span>
      </div>

      <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center">
        <span aria-hidden="true" />
        <div className="flex items-center gap-3">
          <button type="button" aria-label={`Back ${SKIP_BACK_SECONDS} seconds`} onClick={() => p.skip(-SKIP_BACK_SECONDS)} className={`${ghostBtn} flex-col gap-0`}>
            <SkipBack className="h-4 w-4" aria-hidden="true" />
            <span className="text-[10px] leading-none tabular-nums">{SKIP_BACK_SECONDS}</span>
          </button>
          <button
            type="button"
            aria-label={p.playing ? 'Pause' : 'Play'}
            onClick={p.toggle}
            className={`${btnBase} ${btnPrimary} touch-manipulation h-14 w-14 min-h-14 min-w-14 rounded-full`}
          >
            {p.playing ? <Pause className="h-6 w-6" aria-hidden="true" /> : <Play className="h-6 w-6" aria-hidden="true" />}
          </button>
          <button type="button" aria-label={`Forward ${SKIP_FORWARD_SECONDS} seconds`} onClick={() => p.skip(SKIP_FORWARD_SECONDS)} className={`${ghostBtn} flex-col gap-0`}>
            <SkipForward className="h-4 w-4" aria-hidden="true" />
            <span className="text-[10px] leading-none tabular-nums">{SKIP_FORWARD_SECONDS}</span>
          </button>
        </div>
        <div className="flex justify-end">
          <button type="button" aria-label={`Playback speed ${formatRate(p.rate)}`} onClick={p.cycleRate} className={`${ghostBtn} px-2 tabular-nums`}>
            <Gauge className="hidden h-4 w-4 sm:block" aria-hidden="true" />
            {formatRate(p.rate)}
          </button>
        </div>
      </div>
    </div>
  );
}

import { useMemo, useRef, type KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, Gauge, Pause, Play, RotateCcw, RotateCw } from 'lucide-react';
import { SKIP_BACK_SECONDS, SKIP_FORWARD_SECONDS, formatRate, useEpisodePlayer } from '../hooks/useEpisodePlayer';
import { formatTimestamp } from '../utils/format';
import { btnBase, btnGhost, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';

interface EpisodePlayerProps {
  src: string;
  chaptersUrl?: string;
}

const ghostBtn = `${btnBase} ${btnGhost} ${touchTarget} touch-manipulation`;
const skipBtn = `${ghostBtn} h-11 w-11 shrink-0 px-0`;
const skipIcon = 'h-8 w-8';
const skipNum = 'absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none tabular-nums';

function RetryNote({ message, label, onRetry }: { message: string; label: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
      <p className="text-destructive">{message}</p>
      <button type="button" onClick={onRetry} className={`${btnBase} ${btnSecondary}`}>{label}</button>
    </div>
  );
}

export default function EpisodePlayer({ src, chaptersUrl }: EpisodePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const p = useEpisodePlayer(audioRef, chaptersUrl);
  const hasChapters = p.chapters.length > 0;
  const canSeek = p.duration > 0 && !p.error;
  const ticks = useMemo(
    () => (p.duration > 0 ? p.chapters.map((c, i) => ({ c, i })).filter(({ c }) => c.startTime > 0).map(({ c, i }) => ({ key: `${c.startTime}-${i}`, left: Math.min(c.startTime / p.duration, 1) * 100 })) : []),
    [p.chapters, p.duration],
  );
  const endScrub = () => {
    if (!p.scrubbing) return;
    p.seek(p.time);
    p.setScrubTime(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.defaultPrevented || e.repeat || e.nativeEvent.isComposing || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const target = e.target as HTMLElement;
    if (target.isContentEditable || target.closest('button, input, select, textarea')) return;
    if (e.key === ' ') p.toggle();
    else if (e.key === 'ArrowLeft' && canSeek) p.skip(-SKIP_BACK_SECONDS);
    else if (e.key === 'ArrowRight' && canSeek) p.skip(SKIP_FORWARD_SECONDS);
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="group"
      aria-label="Episode player"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className={`@container rounded-lg border border-border bg-card p-3 sm:p-4 ${focusRing}`}
    >
      <audio ref={audioRef} src={src} preload="metadata" {...p.audioProps} />

      {hasChapters && (
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Previous chapter" disabled={!canSeek || p.prevTarget === null} onClick={() => p.prevTarget !== null && p.seek(p.prevTarget)} className={ghostBtn}>
            <ChevronLeft className="h-5 w-5" aria-hidden="true" />
          </button>
          <p className="min-w-0 flex-1 truncate text-center text-sm font-medium text-foreground" title={p.chapterTitle}>{p.chapterTitle}</p>
          <button type="button" aria-label="Next chapter" disabled={!canSeek || p.nextTarget === null} onClick={() => p.nextTarget !== null && p.seek(p.nextTarget)} className={ghostBtn}>
            <ChevronRight className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="relative mt-1 h-11">
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-2 top-1/2 h-4 -translate-y-1/2">
          {ticks.map((t) => (
            <span
              key={t.key}
              className="absolute top-0 h-full w-0.5 -translate-x-1/2 rounded-full bg-foreground/40"
              style={{ left: `${t.left}%` }}
            />
          ))}
        </div>
        <input
          type="range"
          aria-label="Seek"
          aria-valuetext={`${formatTimestamp(p.time)} of ${formatTimestamp(p.duration)}`}
          min={0}
          max={p.duration || 0}
          step={1}
          value={Math.min(p.time, p.duration || 0)}
          disabled={!canSeek}
          onChange={(e) => {
            const v = Number(e.target.value);
            p.setScrubTime(v);
          }}
          onPointerUp={endScrub}
          onPointerCancel={() => p.setScrubTime(null)}
          onKeyUp={endScrub}
          onBlur={endScrub}
          className={`absolute inset-0 h-11 w-full cursor-pointer touch-manipulation accent-primary disabled:cursor-default ${focusRing} rounded`}
        />
      </div>
      <div className="flex justify-between text-xs tabular-nums text-muted-foreground">
        <span>{formatTimestamp(p.time)}</span>
        <span>{p.duration > 0 ? `-${formatTimestamp(Math.max(0, p.duration - p.time))}` : '-'}</span>
      </div>

      <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-y-1">
        <div className="col-start-2 row-start-1 flex items-center gap-3">
          <button type="button" aria-label={`Back ${SKIP_BACK_SECONDS} seconds`} disabled={!canSeek} onClick={() => p.skip(-SKIP_BACK_SECONDS)} className={skipBtn}>
            <span className="relative inline-flex">
              <RotateCcw className={skipIcon} aria-hidden="true" />
              <span className={skipNum}>{SKIP_BACK_SECONDS}</span>
            </span>
          </button>
          <button
            type="button"
            aria-label={p.playing ? 'Pause' : 'Play'}
            onClick={p.toggle}
            className={`${btnBase} ${btnPrimary} touch-manipulation h-14 w-14 min-h-14 min-w-14 rounded-full`}
          >
            {p.playing ? <Pause className="h-6 w-6" aria-hidden="true" /> : <Play className="h-6 w-6" aria-hidden="true" />}
          </button>
          <button type="button" aria-label={`Forward ${SKIP_FORWARD_SECONDS} seconds`} disabled={!canSeek} onClick={() => p.skip(SKIP_FORWARD_SECONDS)} className={skipBtn}>
            <span className="relative inline-flex">
              <RotateCw className={skipIcon} aria-hidden="true" />
              <span className={skipNum}>{SKIP_FORWARD_SECONDS}</span>
            </span>
          </button>
        </div>
        <div className="col-start-2 row-start-2 justify-self-center @min-[21rem]:col-start-3 @min-[21rem]:row-start-1 @min-[21rem]:justify-self-end">
          <button type="button" aria-label={`Playback speed ${formatRate(p.rate)}`} onClick={p.cycleRate} className={`${ghostBtn} px-2 tabular-nums`}>
            <Gauge className="h-4 w-4 shrink-0" aria-hidden="true" />
            {formatRate(p.rate)}
          </button>
        </div>
      </div>
      {p.loading && !p.error && <p role="status" className="mt-2 text-center text-sm text-muted-foreground">Loading audio...</p>}
      {p.error && <RetryNote message="Could not play this audio." label="Retry playback" onRetry={p.retry} />}
      {p.chaptersFailed && <RetryNote message="Could not load chapters." label="Retry chapters" onRetry={p.retryChapters} />}
    </div>
  );
}

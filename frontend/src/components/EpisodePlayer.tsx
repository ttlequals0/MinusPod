import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
  ChevronDown, ChevronLeft, ChevronRight, Gauge, Pause, Play, RotateCcw, RotateCw, Scissors, Volume2, VolumeX,
} from 'lucide-react';
import { SKIP_BACK_SECONDS, SKIP_FORWARD_SECONDS, formatRate, useEpisodePlayer } from '../hooks/useEpisodePlayer';
import { useOutsideClick } from '../hooks/useOutsideClick';
import { formatElapsed, formatTimestamp } from '../utils/format';
import { badgeBase, tint } from './badgeStyles';
import { btnBase, btnGhost, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';
import { DemoDisabled } from './DemoDisabled';

interface EpisodePlayerProps {
  src: string | null;
  chaptersUrl?: string;
  timeSaved?: number;
  originalDuration?: number;
}

const ghostBtn = `${btnBase} ${btnGhost} ${touchTarget} sm:min-h-8 touch-manipulation`;
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

export default function EpisodePlayer({ src, chaptersUrl, timeSaved, originalDuration }: EpisodePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const chapterMenuRef = useRef<HTMLDivElement>(null);
  const chapterTriggerRef = useRef<HTMLButtonElement>(null);
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const p = useEpisodePlayer(audioRef, chaptersUrl);
  const hasChapters = p.chapters.length > 0;
  const noAudio = src === null;
  const canSeek = !noAudio && p.duration > 0 && !p.error;
  const progressPct = p.duration > 0 ? Math.min(Math.max(p.time / p.duration, 0), 1) * 100 : 0;

  useOutsideClick(chapterMenuRef, chaptersOpen, () => setChaptersOpen(false));
  useEffect(() => {
    if (!chaptersOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setChaptersOpen(false);
        chapterTriggerRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [chaptersOpen]);

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

  const playButton = (
    <button
      type="button"
      aria-label={p.playing ? 'Pause' : 'Play'}
      disabled={noAudio}
      onClick={p.toggle}
      className={`${btnBase} ${btnPrimary} touch-manipulation h-14 w-14 min-h-14 min-w-14 rounded-full shadow-sm`}
    >
      {p.playing ? (
        <Pause className="h-6 w-6 fill-current" aria-hidden="true" />
      ) : (
        <Play className="h-6 w-6 translate-x-0.5 fill-current" aria-hidden="true" />
      )}
    </button>
  );

  return (
    <div
      role="group"
      aria-label="Episode player"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className={`@container rounded-xl border border-border bg-gradient-to-b from-secondary/35 to-secondary/15 p-3 sm:p-4 shadow-xs ${focusRing}`}
    >
      {!noAudio && <audio ref={audioRef} src={src} preload="metadata" {...p.audioProps} />}

      {timeSaved != null && timeSaved > 0 && (
        <div className={`flex items-center justify-center ${hasChapters ? 'mb-2' : 'mb-1'}`}>
          <span
            data-testid="player-time-saved"
            className={`${badgeBase} ${tint.success} gap-1.5`}
            title={
              originalDuration && originalDuration > 0
                ? `Cut from ${formatTimestamp(originalDuration)} original audio`
                : undefined
            }
          >
            <Scissors className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span>{formatElapsed(timeSaved)} of ads removed</span>
          </span>
        </div>
      )}

      {hasChapters && (
        <div ref={chapterMenuRef} className="relative z-20 flex items-center gap-1 rounded-lg border border-border/70 bg-card/70 px-1 py-0.5">
          <button type="button" aria-label="Previous chapter" disabled={!canSeek || p.prevTarget === null} onClick={() => p.prevTarget !== null && p.seek(p.prevTarget)} className={ghostBtn}>
            <ChevronLeft className="h-5 w-5" aria-hidden="true" />
          </button>
          <button
            ref={chapterTriggerRef}
            type="button"
            disabled={!canSeek}
            aria-expanded={chaptersOpen}
            title={p.chapterTitle || 'Chapters'}
            onClick={() => setChaptersOpen((open) => !open)}
            className={`${ghostBtn} min-w-0 flex-1 justify-center gap-1.5 px-2 text-center font-medium text-foreground`}
          >
            {p.chapterIndex >= 0 ? (
              <>
                <span aria-hidden="true" className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[11px] font-semibold leading-none tabular-nums text-muted-foreground">
                  {p.chapterIndex + 1}/{p.chapters.length}
                </span>
                <span className="min-w-0 truncate text-sm font-medium text-foreground">{p.chapterTitle}</span>
              </>
            ) : (
              <span className="text-xs font-medium text-muted-foreground">
                {p.chapters.length} {p.chapters.length === 1 ? 'chapter' : 'chapters'}
              </span>
            )}
            <ChevronDown
              className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none ${chaptersOpen ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>
          <button type="button" aria-label="Next chapter" disabled={!canSeek || p.nextTarget === null} onClick={() => p.nextTarget !== null && p.seek(p.nextTarget)} className={ghostBtn}>
            <ChevronRight className="h-5 w-5" aria-hidden="true" />
          </button>
          {chaptersOpen && canSeek && (
            <ul
              aria-label="Chapters"
              className="absolute inset-x-0 top-full z-40 mt-1 max-h-60 overflow-y-auto rounded-md border border-border bg-card py-1 shadow-lg"
            >
              {p.chapters.map((c, idx) => {
                const active = idx === p.chapterIndex;
                return (
                  <li key={`${c.startTime}-${idx}`}>
                    <button
                      type="button"
                      aria-current={active ? 'true' : undefined}
                      onClick={() => {
                        p.seek(c.startTime);
                        setChaptersOpen(false);
                        chapterTriggerRef.current?.focus();
                      }}
                      className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-accent ${
                        active ? 'bg-primary/10 font-semibold text-foreground' : 'text-muted-foreground'
                      } ${focusRing}`}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="w-5 shrink-0 text-xs tabular-nums text-muted-foreground">{idx + 1}.</span>
                        <span className="min-w-0 truncate text-foreground">{c.title}</span>
                      </span>
                      <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                        {formatTimestamp(c.startTime)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-x-2.5 @min-[18rem]:grid-cols-[auto_minmax(0,1fr)_auto]">
        <span className="col-start-1 row-start-2 text-xs font-medium tabular-nums text-muted-foreground @min-[18rem]:row-start-1">
          {formatTimestamp(p.time)}
        </span>
        <div className="group/scrub relative col-span-2 row-start-1 flex h-11 items-center @min-[18rem]:col-span-1 @min-[18rem]:col-start-2">
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
            className={`peer absolute inset-0 z-10 m-0 h-11 w-full cursor-pointer touch-manipulation opacity-0 disabled:cursor-default ${focusRing} rounded`}
          />
          <div
            aria-hidden="true"
            className="pointer-events-none relative mx-1.5 h-2 w-full rounded-full bg-secondary peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-card"
          >
            <div
              className="absolute inset-y-0 left-0 rounded-full bg-primary"
              style={{ width: `${progressPct}%` }}
            />
            {ticks.map((t) => (
              <span
                key={t.key}
                className="absolute top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground/50 ring-1 ring-card"
                style={{ left: `${t.left}%` }}
              />
            ))}
            <span
              className={`absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-card shadow-sm transition-transform motion-reduce:transition-none ${
                canSeek ? 'group-hover/scrub:scale-110' : 'opacity-40'
              }`}
              style={{ left: `${progressPct}%` }}
            />
          </div>
        </div>
        <span className="col-start-2 row-start-2 justify-self-end text-xs font-medium tabular-nums text-muted-foreground @min-[18rem]:col-start-3 @min-[18rem]:row-start-1">
          {p.duration > 0 ? `-${formatTimestamp(Math.max(0, p.duration - p.time))}` : '-'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-y-1">
        <div className="col-start-2 row-start-1 flex items-center gap-3">
          <button type="button" aria-label={`Back ${SKIP_BACK_SECONDS} seconds`} disabled={!canSeek} onClick={() => p.skip(-SKIP_BACK_SECONDS)} className={skipBtn}>
            <span className="relative inline-flex">
              <RotateCcw className={skipIcon} aria-hidden="true" />
              <span className={skipNum}>{SKIP_BACK_SECONDS}</span>
            </span>
          </button>
          <DemoDisabled when={noAudio} title={DEMO_AUDIO_TOOLTIP}>{playButton}</DemoDisabled>
          <button type="button" aria-label={`Forward ${SKIP_FORWARD_SECONDS} seconds`} disabled={!canSeek} onClick={() => p.skip(SKIP_FORWARD_SECONDS)} className={skipBtn}>
            <span className="relative inline-flex">
              <RotateCw className={skipIcon} aria-hidden="true" />
              <span className={skipNum}>{SKIP_FORWARD_SECONDS}</span>
            </span>
          </button>
        </div>
        <div className="col-start-2 row-start-2 flex items-center justify-center gap-2 justify-self-center @min-[21rem]:contents">
          <button
            type="button"
            aria-label={p.muted ? 'Unmute' : 'Mute'}
            aria-pressed={p.muted}
            disabled={noAudio}
            onClick={p.toggleMute}
            className={`${ghostBtn} px-2 @min-[21rem]:col-start-1 @min-[21rem]:row-start-1 @min-[21rem]:justify-self-start`}
          >
            {p.muted ? (
              <VolumeX className="h-4 w-4 shrink-0" aria-hidden="true" />
            ) : (
              <Volume2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            aria-label={`Playback speed ${formatRate(p.rate)}`}
            disabled={noAudio}
            onClick={p.cycleRate}
            className={`${ghostBtn} px-2 tabular-nums @min-[21rem]:col-start-3 @min-[21rem]:row-start-1 @min-[21rem]:justify-self-end`}
          >
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

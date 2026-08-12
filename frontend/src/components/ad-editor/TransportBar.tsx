import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Play, Pause, SkipBack, SkipForward, Rewind, FastForward, Square, ChevronDown } from 'lucide-react';
import { formatTime } from '../../utils/adReviewHelpers';
import { PLAYBACK_RATES, ghostBtn, primaryBtn, selectionBtn } from './controlStyles';

// Shared playback transport bar for the audio-editor modals (AdReviewModal and
// CueMarkModal). Purely presentational: the host owns the <audio> element, the
// playhead loop, and all handlers. Rendering from one component keeps the two
// modals' controls identical. All controls (transport, the optional amber
// "play selection" icon, and the speed selector) sit on one centered row that
// wraps as a unit on narrow screens; the selection readout is centered below.
interface TransportBarProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  onSeekToStart: () => void;
  onSeekToEnd: () => void;
  onSeekRelative: (delta: number) => void;
  onStop: () => void;
  playbackRate: number;
  onPlaybackRateChange: (rate: number) => void;
  currentTime: number;
  selectionDuration: number;
  inSelection: boolean;
  selectionLabel?: string;
  onPlaySelection?: () => void;
  // Optional override for the selection-length readout (e.g. the cue modal
  // shows a precise "1.00s" + validation instead of the default mm:ss).
  selectionInfo?: ReactNode;
}

function TransportBar({
  isPlaying,
  onTogglePlay,
  onSeekToStart,
  onSeekToEnd,
  onSeekRelative,
  onStop,
  playbackRate,
  onPlaybackRateChange,
  currentTime,
  selectionDuration,
  inSelection,
  selectionLabel = 'in selection',
  onPlaySelection,
  selectionInfo,
}: TransportBarProps) {
  // Custom speed control (not a native <select>): iOS Safari sizes native
  // selects with its own width/height that Tailwind cannot fully override, so a
  // button + popover renders identically everywhere and stays button-sized.
  const [speedOpen, setSpeedOpen] = useState(false);
  const speedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!speedOpen) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (speedRef.current && !speedRef.current.contains(e.target as Node)) setSpeedOpen(false);
    };
    // stopPropagation so Escape only closes the popover, not the parent modal
    // (both editors close on a window-level Escape and would discard the edit).
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setSpeedOpen(false); } };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [speedOpen]);

  return (
    <div className="mt-3 mx-auto w-fit max-w-full px-3 py-2 rounded-lg bg-secondary/50 border border-border">
      {/* Controls and the selection readout share one row on desktop (there is
          plenty of width) and stack on mobile, so the editor is not three lines
          tall on a wide screen. The transport cluster keeps the speed control
          grouped beside it at any width. */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-center sm:gap-4">
        <div className="flex flex-wrap items-center justify-center gap-0.5">
          <button type="button" onClick={onSeekToStart} className={`p-1.5 rounded ${ghostBtn}`} title="Jump to START pin">
            <SkipBack className="w-4 h-4" />
          </button>
          <button type="button" onClick={() => onSeekRelative(-10)} className={`p-1.5 rounded ${ghostBtn}`} title="Back 10s">
            <Rewind className="w-4 h-4" />
          </button>
          <button type="button" onClick={onTogglePlay} className={`p-1.5 rounded ${primaryBtn}`} title="Play / pause (Space)">
            {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
          </button>
          {onPlaySelection && (
            <button
              type="button"
              onClick={onPlaySelection}
              className={selectionBtn}
              title="Play the selection only"
              aria-label="Play selection"
            >
              <span aria-hidden="true" className="text-xs font-bold leading-none">[</span>
              <Play className="w-3.5 h-3.5" />
              <span aria-hidden="true" className="text-xs font-bold leading-none">]</span>
            </button>
          )}
          <button type="button" onClick={() => onSeekRelative(10)} className={`p-1.5 rounded ${ghostBtn}`} title="Forward 10s">
            <FastForward className="w-4 h-4" />
          </button>
          <button type="button" onClick={onSeekToEnd} className={`p-1.5 rounded ${ghostBtn}`} title="Jump to END pin">
            <SkipForward className="w-4 h-4" />
          </button>
          <button type="button" onClick={onStop} className={`p-1.5 rounded ${ghostBtn}`} title="Stop (pause + return to START)">
            <Square className="w-4 h-4" />
          </button>
          <div className="relative ml-1" ref={speedRef}>
            <button
              type="button"
              onClick={() => setSpeedOpen((o) => !o)}
              className={`h-8 px-2 rounded inline-flex items-center gap-1 text-xs font-semibold tabular-nums ${ghostBtn} focus:outline-hidden focus:ring-2 focus:ring-ring`}
              title="Playback speed"
              aria-expanded={speedOpen}
              aria-label="Playback speed"
            >
              {playbackRate}&times;
              <ChevronDown className="w-3 h-3 opacity-60" aria-hidden="true" />
            </button>
            {speedOpen && (
              <ul className="absolute right-0 bottom-full mb-1 z-20 min-w-[3.25rem] rounded-md border border-border bg-card shadow-lg py-1">
                {PLAYBACK_RATES.map((r) => (
                  <li key={r}>
                    <button
                      type="button"
                      aria-current={r === playbackRate}
                      onClick={() => { onPlaybackRateChange(r); setSpeedOpen(false); }}
                      className={`block w-full px-3 py-1 text-right text-xs tabular-nums hover:bg-accent ${r === playbackRate ? 'text-foreground font-semibold' : 'text-muted-foreground'}`}
                    >
                      {r}&times;
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {/* Selection readout: below the controls on mobile, beside them on desktop. */}
        <div className="mt-2 sm:mt-0 flex items-center justify-center gap-2 flex-wrap text-xs tabular-nums text-muted-foreground">
          <span className="text-foreground">{formatTime(currentTime)}</span>
          <span>/</span>
          {selectionInfo ?? <span>{formatTime(selectionDuration)} selection</span>}
          {inSelection && (
            <span className="ml-1 px-1.5 py-0.5 rounded bg-warning/15 text-warning text-[10px] font-semibold uppercase tracking-wider">
              {selectionLabel}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default TransportBar;

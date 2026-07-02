import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Play, Pause, SkipBack, SkipForward, Rewind, FastForward, Square } from 'lucide-react';
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
    const onDown = (e: MouseEvent) => {
      if (speedRef.current && !speedRef.current.contains(e.target as Node)) setSpeedOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSpeedOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [speedOpen]);

  return (
    <div className="mt-3 mx-auto w-fit max-w-full px-3 py-2 rounded-lg bg-secondary/50 border border-border">
      {/* All controls on one centered row: the transport cluster and the speed
          control (a compact custom button) stay grouped together, so the speed
          sits right next to the transport at any width instead of drifting to a
          far edge on a wide modal. */}
      <div className="flex items-center justify-center gap-0.5">
        <button type="button" onClick={onSeekToStart} className={`p-1.5 rounded ${ghostBtn}`} title="Jump to START pin">
          <SkipBack className="w-4 h-4" />
        </button>
        <button type="button" onClick={() => onSeekRelative(-10)} className={`p-1.5 rounded ${ghostBtn}`} title="Back 10s">
          <Rewind className="w-4 h-4" />
        </button>
        <button type="button" onClick={onTogglePlay} className={`p-1.5 rounded-full ${primaryBtn}`} title="Play / pause (Space)">
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
            <Play className="w-4 h-4" />
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
            className={`h-8 px-2 rounded inline-flex items-center gap-1 text-xs font-semibold tabular-nums ${ghostBtn} ${playbackRate !== 1 ? 'text-foreground' : ''} focus:outline-hidden focus:ring-2 focus:ring-ring`}
            title="Playback speed"
            aria-haspopup="listbox"
            aria-expanded={speedOpen}
            aria-label="Playback speed"
          >
            {playbackRate}&times;
            <svg className="w-3 h-3 opacity-60" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {speedOpen && (
            <ul role="listbox" className="absolute right-0 bottom-full mb-1 z-20 min-w-[3.25rem] rounded-md border border-border bg-card shadow-lg py-1">
              {PLAYBACK_RATES.map((r) => (
                <li key={r}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={r === playbackRate}
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
      {/* Secondary row: selection readout, centered under the controls. */}
      <div className="mt-2 flex items-center justify-center gap-2 flex-wrap text-xs tabular-nums text-muted-foreground">
        <span className="text-foreground">{formatTime(currentTime)}</span>
        <span>/</span>
        {selectionInfo ?? <span>{formatTime(selectionDuration)} selection</span>}
        {inSelection && (
          <span className="ml-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-500 text-[10px] font-semibold uppercase tracking-wider">
            {selectionLabel}
          </span>
        )}
      </div>
    </div>
  );
}

export default TransportBar;

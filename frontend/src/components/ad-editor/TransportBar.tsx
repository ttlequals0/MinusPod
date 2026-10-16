import type { ReactNode } from 'react';
import { Play, Pause, SkipBack, SkipForward, Rewind, FastForward, Square } from 'lucide-react';
import { formatTime } from '../../utils/adReviewHelpers';
import { ghostBtn, primaryBtn, selectionBtn } from './controlStyles';
import { focusRing } from '../../components/fieldStyles';
import { touchTargetRow } from '../buttonStyles';
import { tint } from '../badgeStyles';
import SpeedMenu from './SpeedMenu';

// Shared editor controls; the host owns audio, playhead, and handlers.
// The controls share one row at every width; on a phone they split the width.
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
  return (
    <div className="mt-3 mx-auto w-full sm:w-fit max-w-full px-2 sm:px-3 py-2 rounded-lg bg-secondary/50 border border-border">
      {/* Controls and the selection readout share one row on desktop (there is
          plenty of width) and stack on mobile, so the editor is not three lines
          tall on a wide screen. The transport cluster keeps the speed control
          grouped beside it at any width. */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-center sm:gap-4">
        <div className="flex items-center justify-center gap-0.5">
          <button type="button" onClick={onSeekToStart} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${ghostBtn} ${touchTargetRow} ${focusRing}`} title="Jump to START pin">
            <SkipBack className="w-4 h-4 shrink-0" />
          </button>
          <button type="button" onClick={() => onSeekRelative(-10)} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${ghostBtn} ${touchTargetRow} ${focusRing}`} title="Back 10s">
            <Rewind className="w-4 h-4 shrink-0" />
          </button>
          <button type="button" onClick={onTogglePlay} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${primaryBtn} ${touchTargetRow} ${focusRing}`} title="Play / pause (Space)">
            {isPlaying ? <Pause className="w-5 h-5 shrink-0" /> : <Play className="w-5 h-5 shrink-0" />}
          </button>
          {onPlaySelection && (
            <button
              type="button"
              onClick={onPlaySelection}
              className={`flex-none ${selectionBtn} ${touchTargetRow} ${focusRing}`}
              title="Play the selection only"
              aria-label="Play selection"
            >
              <span aria-hidden="true" className="text-xs font-bold leading-none">[</span>
              <Play className="w-3.5 h-3.5 shrink-0" />
              <span aria-hidden="true" className="text-xs font-bold leading-none">]</span>
            </button>
          )}
          <button type="button" onClick={() => onSeekRelative(10)} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${ghostBtn} ${touchTargetRow} ${focusRing}`} title="Forward 10s">
            <FastForward className="w-4 h-4 shrink-0" />
          </button>
          <button type="button" onClick={onSeekToEnd} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${ghostBtn} ${touchTargetRow} ${focusRing}`} title="Jump to END pin">
            <SkipForward className="w-4 h-4 shrink-0" />
          </button>
          <button type="button" onClick={onStop} className={`flex-1 sm:flex-none px-0.5 py-1.5 sm:px-1.5 rounded ${ghostBtn} ${touchTargetRow} ${focusRing}`} title="Stop (pause + return to START)">
            <Square className="w-4 h-4 shrink-0" />
          </button>
          <SpeedMenu playbackRate={playbackRate} onChange={onPlaybackRateChange} className="ml-0.5 shrink-0" />
        </div>
        {/* Selection readout: below the controls on mobile, beside them on desktop. */}
        <div className="mt-2 sm:mt-0 flex items-center justify-center gap-2 flex-wrap text-xs tabular-nums text-muted-foreground">
          <span className="text-foreground">{formatTime(currentTime)}</span>
          <span>/</span>
          {selectionInfo ?? <span>{formatTime(selectionDuration)} selection</span>}
          {inSelection && (
            <span className={`ml-1 px-1.5 py-0.5 rounded ${tint.warning} text-[10px] font-semibold uppercase tracking-wider`}>
              {selectionLabel}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default TransportBar;

import { useRef } from 'react';
import { focusRing } from '../fieldStyles';
import { formatTime } from '../../utils/adReviewHelpers';

interface WindowScrubberProps {
  ariaLabel: string;
  className: string;
  totalDuration: number;
  windowStart: number;
  windowEnd: number;
  windowCenter: number;
  playheadTime: number;
  onPan: (center: number) => void;
}

// Full-episode scrubber: drag to pan the zoomed window. Does not seek audio.
export function WindowScrubber({
  ariaLabel, className, totalDuration, windowStart, windowEnd, windowCenter, playheadTime, onPan,
}: WindowScrubberProps) {
  const scrubberRef = useRef<HTMLDivElement>(null);
  const panToClientX = (clientX: number) => {
    const el = scrubberRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    onPan(frac * totalDuration);
  };
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    panToClientX(e.clientX);
    const move = (ev: PointerEvent) => panToClientX(ev.clientX);
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  };

  return (
    <div className={className}>
      <div
        ref={scrubberRef}
        role="slider"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={Math.round(totalDuration)}
        aria-valuenow={Math.round(windowCenter)}
        tabIndex={0}
        onPointerDown={onPointerDown}
        className={`relative h-3 rounded-full bg-background border border-border cursor-pointer touch-none ${focusRing}`}
      >
        <div
          className="absolute inset-y-0 bg-primary/30 rounded-full pointer-events-none"
          style={{
            left: `${(windowStart / totalDuration) * 100}%`,
            width: `${Math.max(1, ((windowEnd - windowStart) / totalDuration) * 100)}%`,
          }}
          aria-hidden
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-warning pointer-events-none"
          style={{ left: `${(playheadTime / totalDuration) * 100}%` }}
          aria-hidden
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground mt-0.5 tabular-nums">
        <span>{formatTime(windowStart)}</span>
        <span>showing {formatTime(windowEnd - windowStart)}</span>
        <span>{formatTime(windowEnd)}</span>
      </div>
    </div>
  );
}

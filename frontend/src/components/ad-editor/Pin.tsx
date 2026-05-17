import { useState } from 'react';
import { formatTime } from '../../utils/adReviewHelpers';

// Vertical drag handle above the waveform that controls the
// corresponding ad boundary. Pins ARE the user's drag interface -- the
// wavesurfer region is decorative (drag/resize disabled on it).

// Keep in sync with the constant in AdReviewModal. Duplicating one
// number here avoids a circular import for what's effectively a pin
// presentation constant.
const MIN_AD_DURATION = 1.0;

export interface PinProps {
  kind: 'start' | 'end';
  boundary: number;
  windowStart: number;
  windowDuration: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onChange: (next: number) => void;
  // Called while drag is in progress so we can scrub audio if enabled.
  onDragMove?: (next: number) => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  otherBoundary: number;        // for min-separation clamp
}

export function Pin({
  kind, boundary, windowStart, windowDuration, containerRef,
  onChange, onDragMove, onDragStart, onDragEnd, otherBoundary,
}: PinProps) {
  const [dragging, setDragging] = useState(false);

  const relX = (boundary - windowStart) / windowDuration;
  // Tolerate a tiny bit outside [0, 1] -- happens routinely on post-roll ads
  // where the LLM places adEnd a hair past where the audio file actually
  // ends, which makes relX = 1.0001 or so. Without slop the END pin
  // disappears entirely.
  const visible = relX >= -0.02 && relX <= 1.02;
  const leftPct = Math.max(0, Math.min(1, relX)) * 100;

  const isStart = kind === 'start';
  const color = isStart ? 'bg-emerald-500' : 'bg-rose-500';
  const ringColor = isStart ? 'ring-emerald-500/40' : 'ring-rose-500/40';
  const labelText = isStart ? 'START' : 'END';

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const container = containerRef.current;
    if (!container) return;

    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDragging(true);
    onDragStart?.();

    const rect = container.getBoundingClientRect();

    const computeBoundary = (clientX: number): number => {
      const xPct = (clientX - rect.left) / rect.width;
      const clampedPct = Math.max(0, Math.min(1, xPct));
      const t = windowStart + clampedPct * windowDuration;
      // Min-separation: never let start cross end (and vice-versa).
      if (isStart) return Math.min(t, otherBoundary - MIN_AD_DURATION);
      return Math.max(t, otherBoundary + MIN_AD_DURATION);
    };

    const handleMove = (ev: PointerEvent) => {
      const next = computeBoundary(ev.clientX);
      onChange(next);
      onDragMove?.(next);
    };
    const handleUp = (ev: PointerEvent) => {
      const next = computeBoundary(ev.clientX);
      onChange(next);
      setDragging(false);
      onDragEnd?.();
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };

    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  if (!visible) return null;

  // Compact pin: small colored circle pinhead, thin stem. The label
  // (with time) only shows when the pin is being dragged or hovered --
  // when idle, just the circle is visible. Negative top offsets are
  // avoided so the pinhead doesn't get clipped by the parent's
  // overflow-x scrollbox.

  return (
    <div
      onPointerDown={onPointerDown}
      style={{
        left: `${leftPct}%`,
        touchAction: 'none',
      }}
      className={`group absolute inset-y-0 -translate-x-1/2 z-10 cursor-ew-resize select-none ${
        dragging ? 'cursor-grabbing' : ''
      }`}
      role="slider"
      aria-label={`${labelText} pin · ${formatTime(boundary)}`}
      aria-valuenow={Math.round(boundary * 10) / 10}
    >
      {/* Compact circle pinhead at top. */}
      <div
        className={`absolute top-1 left-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-white ${color} shadow-md ${
          dragging ? `ring-4 ${ringColor} scale-125` : ''
        } transition-transform`}
      />
      {/* Time label -- only visible while dragging or on hover. */}
      <div
        className={`absolute -top-5 left-1/2 -translate-x-1/2 px-1.5 py-0.5 rounded ${color} text-white text-[10px] font-bold tracking-wider whitespace-nowrap shadow-md transition-opacity duration-100 pointer-events-none ${
          dragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        }`}
      >
        {labelText} {formatTime(boundary)}
      </div>
      {/* Stem -- runs from just below the circle to the bottom. */}
      <div
        className={`absolute top-[20px] bottom-0 left-1/2 -translate-x-1/2 w-0.5 ${color} ${
          dragging ? 'opacity-100' : 'opacity-80'
        }`}
      />
      {/* Touch target -- wraps the whole pin column for easy mobile grab. */}
      <div
        className="absolute inset-y-0 -inset-x-4"
        style={{ touchAction: 'none' }}
      />
    </div>
  );
}

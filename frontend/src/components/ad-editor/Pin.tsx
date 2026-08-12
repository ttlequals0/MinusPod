import { useState } from 'react';
import { formatTime } from '../../utils/adReviewHelpers';

// Vertical drag handle above the waveform that controls the
// corresponding ad boundary. Pins ARE the user's drag interface -- the
// wavesurfer region is decorative (drag/resize disabled on it).

// Default min separation between the two pins. CueMarkModal overrides to
// 0.2s for short stingers; the ad editor leaves it at the 1.0s ad floor.
const DEFAULT_MIN_SEPARATION = 1.0;

// Keyboard nudge per Arrow press, and the coarser step Shift applies.
const NUDGE_SECONDS = 0.1;
const NUDGE_SECONDS_COARSE = 1.0;

export interface PinProps {
  kind: 'start' | 'end' | 'divider';
  boundary: number;
  windowStart: number;
  windowDuration: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onChange: (next: number) => void;
  // Called while drag is in progress so we can scrub audio if enabled.
  onDragMove?: (next: number) => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  // start/end clamp against each other; a divider clamps against both
  // neighbours instead, so it takes an explicit range.
  otherBoundary?: number;
  minBoundary?: number;
  maxBoundary?: number;
  minSeparation?: number;       // override the default 1.0s floor
  // Audio length. Keyboard nudges clamp to [0, totalDuration] on top of the
  // neighbour bounds; drag is already limited to the visible window.
  totalDuration?: number;
}

export function Pin({
  kind, boundary, windowStart, windowDuration, containerRef,
  onChange, onDragMove, onDragStart, onDragEnd, otherBoundary,
  minBoundary, maxBoundary,
  minSeparation = DEFAULT_MIN_SEPARATION,
  totalDuration,
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
  const isDivider = kind === 'divider';
  const color = isDivider
    ? 'bg-warning'
    : isStart ? 'bg-success' : 'bg-destructive';
  const ringColor = isDivider
    ? 'ring-warning/40'
    : isStart ? 'ring-success/40' : 'ring-destructive/40';
  const labelText = isDivider ? 'SPLIT' : isStart ? 'START' : 'END';

  // The legal range for this pin, used by both drag and keyboard so they
  // cannot disagree.
  const rawLower = isDivider
    ? (minBoundary ?? Number.NEGATIVE_INFINITY) + minSeparation
    : isStart ? Number.NEGATIVE_INFINITY : (otherBoundary ?? 0) + minSeparation;
  const rawUpper = isDivider
    ? (maxBoundary ?? Number.POSITIVE_INFINITY) - minSeparation
    : isStart ? (otherBoundary ?? 0) - minSeparation : Number.POSITIVE_INFINITY;
  // A seeded piece under the separation floor inverts the neighbour range;
  // clamping to it would snap the pin, so only the audio bound applies then.
  const neighboursValid = rawLower <= rawUpper;
  const lowerBound = neighboursValid ? Math.max(0, rawLower) : 0;
  const upperBound = Math.min(
    totalDuration ?? Number.POSITIVE_INFINITY,
    neighboursValid ? rawUpper : Number.POSITIVE_INFINITY,
  );
  const clamp = (t: number) => Math.min(Math.max(t, lowerBound), upperBound);

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
      // Min-separation: never let a pin cross its neighbour.
      return clamp(windowStart + clampedPct * windowDuration);
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

  // role="slider" without keys was a slider screen readers announced but nobody
  // could operate. Arrows nudge, Shift takes the coarse step.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? NUDGE_SECONDS_COARSE : NUDGE_SECONDS;
    let next: number | null = null;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = boundary - step;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = boundary + step;
    if (next === null) return;
    e.preventDefault();
    e.stopPropagation();
    onChange(clamp(next));
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
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label={`${labelText} pin · ${formatTime(boundary)}`}
      aria-valuenow={Math.round(boundary * 10) / 10}
      aria-valuemin={Number.isFinite(lowerBound) ? lowerBound : undefined}
      aria-valuemax={Number.isFinite(upperBound) ? upperBound : undefined}
      aria-valuetext={formatTime(boundary)}
    >
      {/* Compact circle pinhead at top. */}
      <div
        className={`absolute top-1 left-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-card ${color} shadow-md ${
          dragging ? `ring-4 ${ringColor} scale-125 motion-reduce:scale-100` : ''
        } transition-transform motion-reduce:transition-none`}
      />
      {/* Time label -- only visible while dragging or on hover. */}
      <div
        className={`absolute -top-5 left-1/2 -translate-x-1/2 px-1.5 py-0.5 rounded ${color} text-primary-foreground text-[10px] font-bold tracking-wider whitespace-nowrap shadow-md transition-opacity duration-100 motion-reduce:transition-none pointer-events-none ${
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

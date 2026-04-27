import { Minus, Plus } from 'lucide-react';
import { DetectedAd, formatTime } from './types';

interface BoundaryControlsProps {
  variant: 'desktop' | 'mobile-mini' | 'mobile-expanded';
  selectedAd: DetectedAd;
  adjustedStart: number;
  adjustedEnd: number;
  startAdjustment: number;
  endAdjustment: number;
  onStartAdjustmentChange: (newAdj: number) => void;
  onEndAdjustmentChange: (newAdj: number) => void;
  triggerHaptic: () => void;
}

export function BoundaryControls({
  variant,
  selectedAd,
  adjustedStart,
  adjustedEnd,
  startAdjustment,
  endAdjustment,
  onStartAdjustmentChange,
  onEndAdjustmentChange,
  triggerHaptic,
}: BoundaryControlsProps) {
  const handleStartDecrement = () => {
    onStartAdjustmentChange(startAdjustment - 1);
    triggerHaptic();
  };

  const handleStartIncrement = () => {
    onStartAdjustmentChange(startAdjustment + 1);
    triggerHaptic();
  };

  const handleEndDecrement = () => {
    onEndAdjustmentChange(endAdjustment - 1);
    triggerHaptic();
  };

  const handleEndIncrement = () => {
    onEndAdjustmentChange(endAdjustment + 1);
    triggerHaptic();
  };

  const handleStartInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value) || 0;
    onStartAdjustmentChange(val);
  };

  const handleEndInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value) || 0;
    onEndAdjustmentChange(val);
  };

  if (variant === 'desktop') {
    return (
      <div className="px-4 py-3 border-b border-border bg-muted/30">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            {/* Start Adjustment */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Start</span>
              <div className="flex items-center rounded-lg bg-muted overflow-hidden">
                <button
                  onClick={handleStartDecrement}
                  className="px-2.5 py-2 hover:bg-accent active:bg-accent/80 active:scale-95 transition-all text-foreground"
                  aria-label="Decrease start adjustment"
                >
                  <Minus className="w-4 h-4" />
                </button>
                <div className="relative flex items-center border-x border-border/50">
                  <input
                    type="number"
                    value={startAdjustment}
                    onChange={handleStartInputChange}
                    className="w-12 text-center text-sm font-mono font-medium bg-transparent pr-3 py-1.5 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    step="1"
                  />
                  <span className="absolute right-1 text-[10px] text-muted-foreground pointer-events-none">s</span>
                </div>
                <button
                  onClick={handleStartIncrement}
                  className="px-2.5 py-2 hover:bg-accent active:bg-accent/80 active:scale-95 transition-all text-foreground"
                  aria-label="Increase start adjustment"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            </div>
            {/* End Adjustment */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">End</span>
              <div className="flex items-center rounded-lg bg-muted overflow-hidden">
                <button
                  onClick={handleEndDecrement}
                  className="px-2.5 py-2 hover:bg-accent active:bg-accent/80 active:scale-95 transition-all text-foreground"
                  aria-label="Decrease end adjustment"
                >
                  <Minus className="w-4 h-4" />
                </button>
                <div className="relative flex items-center border-x border-border/50">
                  <input
                    type="number"
                    value={endAdjustment}
                    onChange={handleEndInputChange}
                    className="w-12 text-center text-sm font-mono font-medium bg-transparent pr-3 py-1.5 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    step="1"
                  />
                  <span className="absolute right-1 text-[10px] text-muted-foreground pointer-events-none">s</span>
                </div>
                <button
                  onClick={handleEndIncrement}
                  className="px-2.5 py-2 hover:bg-accent active:bg-accent/80 active:scale-95 transition-all text-foreground"
                  aria-label="Increase end adjustment"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
          {/* Time display */}
          <div className="text-xs text-muted-foreground text-right">
            <span className="font-mono">
              {formatTime(adjustedStart)} - {formatTime(adjustedEnd)}
            </span>
            <span className="ml-2">
              ({formatTime(adjustedEnd - adjustedStart)})
            </span>
            {(startAdjustment !== 0 || endAdjustment !== 0) && (
              <span className="ml-2 text-primary font-mono">
                was {formatTime(selectedAd.start)} - {formatTime(selectedAd.end)}
              </span>
            )}
          </div>
        </div>
        {/* Keyboard shortcuts hint */}
        <div className="mt-2 text-xs text-muted-foreground">
          <span className="font-mono">Space</span> play/pause{' '}
          <span className="font-mono">J/K</span> nudge end{' '}
          <span className="font-mono">Shift+J/K</span> nudge start{' '}
          <span className="font-mono">C</span> confirm{' '}
          <span className="font-mono">X</span> reject{' '}
          <span className="font-mono">Esc</span> reset
        </div>
      </div>
    );
  }

  if (variant === 'mobile-mini') {
    return (
      <div className="space-y-1.5">
        {/* Start row */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide w-10 shrink-0">Start</span>
          <div className="flex-1 flex items-center rounded-lg bg-muted overflow-hidden h-10">
            <button
              onClick={handleStartDecrement}
              className="px-4 h-full active:bg-accent/80 active:scale-95 touch-manipulation flex items-center justify-center transition-all text-foreground"
              aria-label="Decrease start adjustment"
            >
              <Minus className="w-4 h-4" />
            </button>
            <div className="relative flex-1 flex items-center justify-center border-x border-border/40 h-full">
              <input
                type="number"
                value={startAdjustment}
                onChange={handleStartInputChange}
                className="w-full text-center text-base font-mono font-medium bg-transparent focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                step="1"
              />
              <span className="absolute right-2 text-xs text-muted-foreground pointer-events-none">s</span>
            </div>
            <button
              onClick={handleStartIncrement}
              className="px-4 h-full active:bg-accent/80 active:scale-95 touch-manipulation flex items-center justify-center transition-all text-foreground"
              aria-label="Increase start adjustment"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>
        {/* End row */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide w-10 shrink-0">End</span>
          <div className="flex-1 flex items-center rounded-lg bg-muted overflow-hidden h-10">
            <button
              onClick={handleEndDecrement}
              className="px-4 h-full active:bg-accent/80 active:scale-95 touch-manipulation flex items-center justify-center transition-all text-foreground"
              aria-label="Decrease end adjustment"
            >
              <Minus className="w-4 h-4" />
            </button>
            <div className="relative flex-1 flex items-center justify-center border-x border-border/40 h-full">
              <input
                type="number"
                value={endAdjustment}
                onChange={handleEndInputChange}
                className="w-full text-center text-base font-mono font-medium bg-transparent focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                step="1"
              />
              <span className="absolute right-2 text-xs text-muted-foreground pointer-events-none">s</span>
            </div>
            <button
              onClick={handleEndIncrement}
              className="px-4 h-full active:bg-accent/80 active:scale-95 touch-manipulation flex items-center justify-center transition-all text-foreground"
              aria-label="Increase end adjustment"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  // mobile-expanded
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 flex items-center gap-1.5">
        <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide shrink-0">Start</span>
        <div className="flex-1 flex items-center rounded-lg bg-muted overflow-hidden">
          <button
            onClick={handleStartDecrement}
            className="px-3 py-2.5 active:bg-accent/80 active:scale-95 touch-manipulation min-w-[40px] min-h-[40px] flex items-center justify-center transition-all text-foreground"
            aria-label="Decrease start adjustment"
          >
            <Minus className="w-4 h-4" />
          </button>
          <div className="relative flex-1 flex items-center justify-center border-x border-border/40">
            <input
              type="number"
              value={startAdjustment}
              onChange={handleStartInputChange}
              className="w-full text-center text-sm font-mono font-medium bg-transparent py-1.5 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              step="1"
            />
            <span className="absolute right-1 text-[10px] text-muted-foreground pointer-events-none">s</span>
          </div>
          <button
            onClick={handleStartIncrement}
            className="px-3 py-2.5 active:bg-accent/80 active:scale-95 touch-manipulation min-w-[40px] min-h-[40px] flex items-center justify-center transition-all text-foreground"
            aria-label="Increase start adjustment"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 flex items-center gap-1.5">
        <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide shrink-0">End</span>
        <div className="flex-1 flex items-center rounded-lg bg-muted overflow-hidden">
          <button
            onClick={handleEndDecrement}
            className="px-3 py-2.5 active:bg-accent/80 active:scale-95 touch-manipulation min-w-[40px] min-h-[40px] flex items-center justify-center transition-all text-foreground"
            aria-label="Decrease end adjustment"
          >
            <Minus className="w-4 h-4" />
          </button>
          <div className="relative flex-1 flex items-center justify-center border-x border-border/40">
            <input
              type="number"
              value={endAdjustment}
              onChange={handleEndInputChange}
              className="w-full text-center text-sm font-mono font-medium bg-transparent py-1.5 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              step="1"
            />
            <span className="absolute right-1 text-[10px] text-muted-foreground pointer-events-none">s</span>
          </div>
          <button
            onClick={handleEndIncrement}
            className="px-3 py-2.5 active:bg-accent/80 active:scale-95 touch-manipulation min-w-[40px] min-h-[40px] flex items-center justify-center transition-all text-foreground"
            aria-label="Increase end adjustment"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

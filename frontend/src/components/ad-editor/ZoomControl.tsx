import { ZoomIn, ZoomOut } from 'lucide-react';
import { ghostBtn } from './controlStyles';

// Shared waveform zoom control for the audio-editor modals: a range slider
// flanked by zoom-out / zoom-in buttons and a numeric readout. Presentational;
// the host owns the zoom state and any wheel-zoom on the waveform.
interface ZoomControlProps {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
}

function ZoomControl({ value, min, max, step = 0.1, onChange, onZoomIn, onZoomOut }: ZoomControlProps) {
  return (
    <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={onZoomOut}
        disabled={value <= min + 0.01}
        className={`p-1.5 rounded ${ghostBtn}`}
        title="Zoom out"
      >
        <ZoomOut className="w-3.5 h-3.5" />
      </button>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 accent-primary"
        title="Zoom"
      />
      <button
        type="button"
        onClick={onZoomIn}
        disabled={value >= max - 0.01}
        className={`p-1.5 rounded ${ghostBtn}`}
        title="Zoom in"
      >
        <ZoomIn className="w-3.5 h-3.5" />
      </button>
      <span className="tabular-nums w-12 text-right">
        {value < 10 ? value.toFixed(1) : Math.round(value)}×
      </span>
    </div>
  );
}

export default ZoomControl;

import { btnBase, btnGhost, touchTarget } from '../buttonStyles';

// Loading and failure lines shared by the three waveform editors.
export function WaveformLoading({ className = '' }: { className?: string }) {
  return (
    <p role="status" className={`text-sm text-muted-foreground ${className}`}>
      Loading waveform...
    </p>
  );
}

export function WaveformError({ onRetry, className = '' }: { onRetry: () => void; className?: string }) {
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${className}`}>
      <p role="alert" className="text-sm text-destructive">Could not load waveform. Try again.</p>
      <button type="button" onClick={onRetry} className={`${btnBase} ${btnGhost} ${touchTarget}`}>
        Retry
      </button>
    </div>
  );
}

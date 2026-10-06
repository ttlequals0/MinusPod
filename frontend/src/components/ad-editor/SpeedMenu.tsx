import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { PLAYBACK_RATES, ghostBtn } from './controlStyles';
import { touchTarget } from '../buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { useOutsideClick } from '../../hooks/useOutsideClick';

interface SpeedMenuProps {
  playbackRate: number;
  onChange: (rate: number) => void;
  rates?: readonly number[];
  // Spacing/positioning at the call site; the popover itself is fixed.
  className?: string;
  disabled?: boolean;
}

// Compact h-8 ghost button + popover, not a native <select> (iOS Safari sizes
// those with its own metrics Tailwind can't override). Shared by TransportBar
// and TextSelectionPanel so both playback bars match.
function SpeedMenu({ playbackRate, onChange, rates = PLAYBACK_RATES, className = '', disabled = false }: SpeedMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useOutsideClick(ref, open, () => setOpen(false));
  useEffect(() => {
    if (!open) return;
    // stopPropagation so Escape only closes the popover, not the parent modal
    // (both editors close on a window-level Escape and would discard the edit).
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className={`relative ${className}`} ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={`h-8 px-1.5 rounded inline-flex items-center gap-1 text-xs font-semibold tabular-nums ${ghostBtn} focus:outline-hidden focus:ring-2 focus:ring-ring ${touchTarget}`}
        title="Playback speed"
        aria-expanded={open}
        aria-label="Playback speed"
      >
        {playbackRate}&times;
        <ChevronDown className="w-3 h-3 opacity-60" aria-hidden="true" />
      </button>
      {open && (
        <ul className="absolute right-0 bottom-full mb-1 z-20 min-w-[3.25rem] rounded-md border border-border bg-card shadow-lg py-1">
          {rates.map((r) => (
            <li key={r}>
              <button
                type="button"
                disabled={disabled}
                aria-current={r === playbackRate}
                onClick={() => { onChange(r); setOpen(false); triggerRef.current?.focus(); }}
                className={`block w-full px-3 py-1 text-right text-xs tabular-nums hover:bg-accent ${r === playbackRate ? 'text-foreground font-semibold' : 'text-muted-foreground'} ${focusRing} max-sm:min-h-11 max-sm:min-w-11`}
              >
                {r}&times;
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default SpeedMenu;

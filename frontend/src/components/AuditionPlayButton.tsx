import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { cardActionHeight, cardActionIcon, rowActionIcon, tableActionIcon } from './rowActionStyles';

// 'row' and 'card' take the height of the marker-row / card action buttons,
// from the same source those buttons use, so every marker section lines up at
// each breakpoint. 'sm' is a compact standalone size (e.g. split-piece rows).
type Size = 'sm' | 'row' | 'card' | 'table' | 'fill';

// button is the 44px target on phones; visual is the filled span inside it.
const SIZES: Record<Size, { button: string; visual: string }> = {
  sm: { button: 'inline-flex items-center justify-center', visual: 'p-1.5' },
  row: { button: rowActionIcon, visual: 'h-8 w-8 sm:h-full sm:w-full' },
  card: { button: cardActionIcon, visual: 'h-full w-full' },
  table: { button: tableActionIcon, visual: 'h-full w-full' },
  // Shares a flex row equally with sibling controls instead of a fixed width.
  fill: {
    button: `${cardActionHeight} flex-1 basis-0 min-w-0 inline-flex items-center justify-center`,
    visual: 'h-full w-full',
  },
};

export function AuditionPlayButton({ playing, onClick, label = 'this ad', size }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  /** Required so every call site matches the controls beside it; no silent default. */
  size: Size;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={playing ? 'Pause' : `Play ${label}`}
      className={`group ${SIZES[size].button}${size === 'fill' ? '' : ' shrink-0'} touch-manipulation focus-visible:outline-hidden`}
    >
      <span
        className={`${SIZES[size].visual} inline-flex items-center justify-center rounded ${btnPrimary} transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring`}
      >
        {/* Play reads left-heavy; nudge it 1px right to center optically. */}
        {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 translate-x-px" />}
      </span>
    </button>
  );
}

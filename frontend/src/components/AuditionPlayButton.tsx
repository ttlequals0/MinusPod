import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { cardActionIcon, rowActionIcon, tableActionIcon } from './rowActionStyles';

// 'row' and 'card' take the height of the marker-row / card action buttons,
// from the same source those buttons use, so every marker section lines up at
// each breakpoint. 'sm' is a compact standalone size (e.g. split-piece rows).
const SIZES = {
  sm: 'p-1.5',
  row: rowActionIcon,
  card: cardActionIcon,
  table: tableActionIcon,
} as const;

export function AuditionPlayButton({ playing, onClick, label = 'this ad', size }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  /** Required so every call site matches the controls beside it; no silent default. */
  size: keyof typeof SIZES;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={playing ? 'Pause' : `Play ${label}`}
      className={`${SIZES[size]} rounded ${btnPrimary} transition-colors shrink-0 touch-manipulation ${focusRing}`}
    >
      {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
    </button>
  );
}

import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { cardActionIcon, rowActionIcon } from './rowActionStyles';

// 'row' and 'card' take the height of the action buttons beside them, from
// the same source those buttons use, so the play button lines up at every
// breakpoint. 'sm' is the standalone size for rows with no buttons next to it.
const SIZES = {
  sm: 'p-1.5',
  row: rowActionIcon,
  card: cardActionIcon,
} as const;

export function AuditionPlayButton({ playing, onClick, label = 'this ad', size = 'sm' }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  size?: keyof typeof SIZES;
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

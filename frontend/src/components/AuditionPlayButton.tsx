import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { rowActionIcon } from './rowActionStyles';

// One square everywhere: 32px on phones inside the 44px target, 28px from sm.
const BUTTON = `${rowActionIcon} shrink-0`;
const VISUAL = 'h-8 w-8 sm:h-7 sm:w-7';

export function AuditionPlayButton({ playing, onClick, label = 'this ad' }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={playing ? 'Pause' : `Play ${label}`}
      className={`group ${BUTTON} touch-manipulation focus-visible:outline-hidden`}
    >
      <span
        className={`${VISUAL} inline-flex items-center justify-center rounded ${btnPrimary} transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring`}
      >
        {/* Play reads left-heavy; nudge it 1px right to center optically. */}
        {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 translate-x-px" />}
      </span>
    </button>
  );
}

import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';

// Small play/pause button for auditioning a windowed audio span.
// Shared by the episode page's held/rejected marker rows, the Ad Review
// tab's rows, and the split modal. 'sm' is the compact standalone size;
// 'match' sizes to the design guide's icon-only 32x32 so the button lines
// up with the Confirm/Not-an-ad text buttons beside it.
export function AuditionPlayButton({ playing, onClick, label = 'this ad', size = 'sm' }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  size?: 'sm' | 'match';
}) {
  const sizeCls = size === 'match'
    ? 'h-8 w-8 inline-flex items-center justify-center'
    : 'p-1.5';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={playing ? 'Pause' : `Play ${label}`}
      className={`${sizeCls} rounded ${btnPrimary} transition-colors shrink-0 touch-manipulation ${focusRing}`}
    >
      {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
    </button>
  );
}

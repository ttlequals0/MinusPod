import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';

// Small round play/pause button for auditioning a windowed audio span.
// Shared by the episode page's held/rejected marker rows and the Ad Review
// tab's table rows.
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
      className={`p-1.5 rounded ${btnPrimary} transition-colors shrink-0 touch-manipulation ${focusRing}`}
    >
      {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
    </button>
  );
}

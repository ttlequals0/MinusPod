import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { iconTarget, iconVisual } from './rowActionStyles';

export function AuditionPlayButton({ playing, onClick, label = 'this ad', disabled = false, title }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  disabled?: boolean;
  /** Replaces the default tooltip, e.g. to say why the button is disabled. */
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={title ?? (playing ? 'Pause' : `Play ${label}`)}
      className={`${iconTarget} disabled:opacity-50`}
    >
      <span className={`${iconVisual} ${btnPrimary}`}>
        {/* Play reads left-heavy; nudge it 1px right to center optically. */}
        {playing ? <Pause className="w-3.5 h-3.5" aria-hidden="true" /> : <Play className="w-3.5 h-3.5 translate-x-px" aria-hidden="true" />}
      </span>
    </button>
  );
}

import { Pause, Play } from 'lucide-react';
import { btnPrimary } from './buttonStyles';
import { iconSizes, type IconSize } from './rowActionStyles';

export function AuditionPlayButton({ playing, onClick, label = 'this ad', disabled = false, title, size = 'row' }: {
  playing: boolean;
  onClick: () => void;
  /** What the button plays, e.g. "this segment" on rows kept on purpose. */
  label?: string;
  disabled?: boolean;
  /** Replaces the default tooltip, e.g. to say why the button is disabled. */
  title?: string;
  /** Match the text button beside it; see iconSizes. */
  size?: IconSize;
}) {
  const { target, visual } = iconSizes[size];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={playing ? `Pause ${label}` : `Play ${label}`}
      title={title ?? (playing ? 'Pause' : `Play ${label}`)}
      className={`${target} disabled:opacity-50`}
    >
      <span className={`${visual} ${btnPrimary}`}>
        {/* Play reads left-heavy; nudge it 1px right to center optically. */}
        {playing ? <Pause className="w-3.5 h-3.5" aria-hidden="true" /> : <Play className="w-3.5 h-3.5 translate-x-px" aria-hidden="true" />}
      </span>
    </button>
  );
}

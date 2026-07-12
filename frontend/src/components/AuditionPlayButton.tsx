import { Pause, Play } from 'lucide-react';

// Small round play/pause button for auditioning a windowed audio span.
// Shared by the episode page's held/rejected marker rows and the Ad Review
// tab's table rows.
export function AuditionPlayButton({ playing, onClick }: { playing: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={playing ? 'Pause ad' : 'Play this ad'}
      title={playing ? 'Pause' : 'Play this ad'}
      className="p-1.5 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shrink-0 touch-manipulation"
    >
      {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
    </button>
  );
}

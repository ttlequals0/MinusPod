import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

interface CoverArtSectionProps {
  artworkWatermarkEnabled: boolean;
  onArtworkWatermarkEnabledChange: (enabled: boolean) => void;
  onRefreshArtwork: () => void;
  refreshArtworkPending: boolean;
}

function CoverArtSection({
  artworkWatermarkEnabled,
  onArtworkWatermarkEnabledChange,
  onRefreshArtwork,
  refreshArtworkPending,
}: CoverArtSectionProps) {
  return (
    <CollapsibleSection title="Cover Art">
      <div className="space-y-4">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={artworkWatermarkEnabled}
              onChange={onArtworkWatermarkEnabledChange}
              ariaLabel="Overlay MinusPod badge on cover art"
            />
            <span className="text-sm font-medium text-foreground">
              Overlay MinusPod badge on cover art
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Adds a small MinusPod badge to the bottom-right corner of each served feed's cover art, so the filtered version is easy to tell apart from the original in your podcast app. Off by default.
          </p>
        </div>

        <div className="pt-4 border-t border-border">
          <button
            type="button"
            onClick={onRefreshArtwork}
            disabled={refreshArtworkPending}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${refreshArtworkPending ? 'animate-spin' : ''}`} />
            {refreshArtworkPending ? 'Refreshing artwork...' : 'Refresh all artwork'}
          </button>
          <p className="mt-2 text-sm text-muted-foreground">
            Re-pulls covers and rebuilds the served feeds so a badge change takes effect. Your podcast app still re-fetches on its own schedule.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default CoverArtSection;

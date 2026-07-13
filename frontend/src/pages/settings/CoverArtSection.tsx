import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import { BYTES_PER_MB } from './settingsUtils';


interface CoverArtSectionProps {
  artworkWatermarkEnabled: boolean;
  onArtworkWatermarkEnabledChange: (enabled: boolean) => void;
  maxArtworkBytes: number;
  onMaxArtworkBytesChange: (bytes: number) => void;
  onRefreshArtwork: () => void;
  refreshArtworkPending: boolean;
}

function CoverArtSection({
  artworkWatermarkEnabled,
  onArtworkWatermarkEnabledChange,
  maxArtworkBytes,
  onMaxArtworkBytesChange,
  onRefreshArtwork,
  refreshArtworkPending,
}: CoverArtSectionProps) {
  const maxArtworkMb = Math.round((maxArtworkBytes / BYTES_PER_MB) * 10) / 10;
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
          <label htmlFor="maxArtworkMb" className="block text-sm font-medium text-foreground mb-2">
            Max artwork size (MB)
          </label>
          <div className="flex items-center gap-3">
            <NumberInput
              id="maxArtworkMb"
              value={maxArtworkMb}
              min={0.1}
              max={50}
              step={0.1}
              fallback={25}
              onCommit={(mb) => {
                // Commit only real edits: re-encoding the displayed value
                // on a mere focus/blur would rewrite a stored value that
                // does not sit on the display rounding grid.
                if (mb !== maxArtworkMb) onMaxArtworkBytesChange(Math.round(mb * BYTES_PER_MB));
              }}
            />
            <span className="text-sm text-muted-foreground">MB (0.1-50)</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Cover art downloads over this size are skipped. Default 25 MB.
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

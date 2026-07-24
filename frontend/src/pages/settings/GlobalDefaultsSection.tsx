import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';

interface GlobalDefaultsSectionProps {
  autoProcessEnabled: boolean;
  onAutoProcessEnabledChange: (enabled: boolean) => void;
  rssRefreshIntervalMinutes: number;
  onRssRefreshIntervalMinutesChange: (value: number) => void;
  maxFeedEpisodes: number;
  onMaxFeedEpisodesChange: (n: number) => void;
  onlyExposeProcessedDefault: boolean;
  onOnlyExposeProcessedDefaultChange: (enabled: boolean) => void;
}

function GlobalDefaultsSection({
  autoProcessEnabled,
  onAutoProcessEnabledChange,
  rssRefreshIntervalMinutes,
  onRssRefreshIntervalMinutesChange,
  maxFeedEpisodes,
  onMaxFeedEpisodesChange,
  onlyExposeProcessedDefault,
  onOnlyExposeProcessedDefaultChange,
}: GlobalDefaultsSectionProps) {
  return (
    <CollapsibleSection
      title="Global Defaults"
      subtitle="Applied to every feed unless overridden on the feed's own settings."
    >
      <div className="space-y-6">
        {/* Auto-process new episodes */}
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={autoProcessEnabled}
              onChange={onAutoProcessEnabledChange}
              ariaLabel="Auto-process new episodes"
            />
            <span className="text-sm font-medium text-foreground">
              Auto-process new episodes
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            When a feed refresh discovers a new episode, queue it for processing automatically. Per-feed Auto-Process can override this.
          </p>
        </div>

        {/* Feed refresh interval */}
        <div className="pt-4 border-t border-border">
          <label htmlFor="rssRefreshIntervalMinutes" className="block text-sm font-medium text-foreground mb-2">
            Feed refresh interval
          </label>
          <div className="flex items-center gap-3">
            <NumberInput
              id="rssRefreshIntervalMinutes"
              value={rssRefreshIntervalMinutes}
              min={5}
              max={1440}
              step={1}
              fallback={15}
              onCommit={onRssRefreshIntervalMinutesChange}
            />
            <span className="text-sm text-muted-foreground">5 to 1440</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Minutes between background RSS refresh passes. Default 15.
          </p>
        </div>

        {/* Max feed episodes */}
        <div className="pt-4 border-t border-border">
          <label
            htmlFor="maxFeedEpisodesGlobal"
            className="block text-sm font-medium text-foreground mb-2"
          >
            Max episodes per served feed
          </label>
          <div className="flex items-center gap-3">
            <NumberInput
              id="maxFeedEpisodesGlobal"
              value={maxFeedEpisodes}
              min={10}
              max={500}
              fallback={10}
              parse={(s) => parseInt(s, 10)}
              onCommit={onMaxFeedEpisodesChange}
            />
            <span className="text-sm text-muted-foreground">episodes (10-500)</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Caps how many recent episodes appear in each podcast's served RSS feed. Per-feed Max Episodes can override this.
          </p>
        </div>

        {/* Only expose processed episodes */}
        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={onlyExposeProcessedDefault}
              onChange={onOnlyExposeProcessedDefaultChange}
              ariaLabel="Only expose processed episodes in feed"
            />
            <span className="text-sm font-medium text-foreground">
              Only expose processed episodes in feed
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Hides upstream episodes that haven't finished processing from served RSS feeds, so podcast apps don't auto-download an episode that would 503. Per-feed override is available on each feed's settings.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default GlobalDefaultsSection;

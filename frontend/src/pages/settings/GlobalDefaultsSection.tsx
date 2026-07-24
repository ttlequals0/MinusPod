import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import SegmentActionToggle from '../../components/SegmentActionToggle';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, DEFAULT_SEGMENT_ACTION,
  type SegmentCategory, type SegmentAction,
} from '../../utils/segmentCategory';

interface GlobalDefaultsSectionProps {
  autoProcessEnabled: boolean;
  onAutoProcessEnabledChange: (enabled: boolean) => void;
  rssRefreshIntervalMinutes: number;
  onRssRefreshIntervalMinutesChange: (value: number) => void;
  podpingEnabled: boolean;
  onPodpingEnabledChange: (enabled: boolean) => void;
  maxFeedEpisodes: number;
  onMaxFeedEpisodesChange: (n: number) => void;
  onlyExposeProcessedDefault: boolean;
  onOnlyExposeProcessedDefaultChange: (enabled: boolean) => void;
  segmentCategoryActions: Partial<Record<SegmentCategory, SegmentAction>>;
  onSegmentCategoryActionChange: (category: SegmentCategory, action: SegmentAction) => void;
}

function GlobalDefaultsSection({
  autoProcessEnabled,
  onAutoProcessEnabledChange,
  rssRefreshIntervalMinutes,
  onRssRefreshIntervalMinutesChange,
  podpingEnabled,
  onPodpingEnabledChange,
  maxFeedEpisodes,
  onMaxFeedEpisodesChange,
  onlyExposeProcessedDefault,
  onOnlyExposeProcessedDefaultChange,
  segmentCategoryActions,
  onSegmentCategoryActionChange,
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

        {/* Podping notifications */}
        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={podpingEnabled}
              onChange={onPodpingEnabledChange}
              ariaLabel="Podping notifications"
            />
            <span className="text-sm font-medium text-foreground">
              Podping notifications
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Listen for Podping publish notifications and refresh a feed as soon as its host announces a new episode. Only some hosts send Podping; feeds keep refreshing on the normal schedule either way.
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

        {/* Segment actions (issue #565): per-category remove/beep/keep matrix. */}
        <div className="pt-4 border-t border-border">
          <details className="group">
            <summary className="text-sm text-primary hover:underline cursor-pointer list-none">
              Segment actions
            </summary>
            <div className="mt-3 space-y-3">
              <p className="text-sm text-muted-foreground">
                Choose what happens to each kind of detected segment. Remove cuts it out, Beep replaces it with a tone, Keep leaves it in.
              </p>
              <div className="space-y-2">
                {SEGMENT_CATEGORIES.map((category) => (
                  <div key={category} className="flex items-center justify-between gap-3 flex-wrap">
                    <span className="text-sm text-foreground">{SEGMENT_CATEGORY_LABELS[category]}</span>
                    <SegmentActionToggle
                      value={segmentCategoryActions[category] ?? DEFAULT_SEGMENT_ACTION}
                      onChange={(action) => onSegmentCategoryActionChange(category, action)}
                      ariaLabel={`${SEGMENT_CATEGORY_LABELS[category]} action`}
                    />
                  </div>
                ))}
              </div>
            </div>
          </details>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default GlobalDefaultsSection;

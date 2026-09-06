import CollapsibleSection from '../../components/CollapsibleSection';
import { selectBase } from '../../components/fieldStyles';
import { LOW_AD_YIELD_ACTION_LABELS } from '../../utils/lowAdYield';
import type { EpisodeLogLevel, LowAdYieldAction } from '../../api/types';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';

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
  lowAdYieldAction: LowAdYieldAction;
  onLowAdYieldActionChange: (action: LowAdYieldAction) => void;
  episodeLogRetentionDays: number;
  onEpisodeLogRetentionDaysChange: (days: number) => void;
  episodeLogLevel: EpisodeLogLevel;
  onEpisodeLogLevelChange: (level: EpisodeLogLevel) => void;
  textRecurrenceHints: boolean;
  onTextRecurrenceHintsChange: (enabled: boolean) => void;
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
  lowAdYieldAction,
  onLowAdYieldActionChange,
  episodeLogRetentionDays,
  onEpisodeLogRetentionDaysChange,
  episodeLogLevel,
  onEpisodeLogLevelChange,
  textRecurrenceHints,
  onTextRecurrenceHintsChange,
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

        {/* Low ad yield response */}
        <div className="pt-4 border-t border-border">
          <label htmlFor="lowAdYieldAction" className="block text-sm font-medium text-foreground mb-2">
            When an episode detects fewer ads than usual
          </label>
          <select
            id="lowAdYieldAction"
            value={lowAdYieldAction}
            onChange={(e) => onLowAdYieldActionChange(e.target.value as LowAdYieldAction)}
            className={`w-full ${selectBase}`}
          >
            {(Object.keys(LOW_AD_YIELD_ACTION_LABELS) as LowAdYieldAction[]).map((action) => (
              <option key={action} value={action}>{LOW_AD_YIELD_ACTION_LABELS[action]}</option>
            ))}
          </select>
          <p className="mt-2 text-sm text-muted-foreground">
            When an episode finishes with far less ad time removed than its feed usually yields, run this action automatically (once per episode).
          </p>
        </div>

        {/* Episode run logs */}
        <div className="pt-4 border-t border-border">
          <label
            htmlFor="episodeLogRetentionDays"
            className="block text-sm font-medium text-foreground mb-2"
          >
            Keep episode run logs for
          </label>
          <div className="flex items-center gap-3">
            <NumberInput
              id="episodeLogRetentionDays"
              value={episodeLogRetentionDays}
              min={0}
              max={365}
              fallback={30}
              parse={(s) => parseInt(s, 10)}
              onCommit={onEpisodeLogRetentionDaysChange}
            />
            <span className="text-sm text-muted-foreground">days (0-365)</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Each run writes its pipeline log to disk, readable on the episode page. 0 keeps
            nothing and deletes what is already stored. A feed can opt out in its own settings.
          </p>
          <label
            htmlFor="episodeLogLevel"
            className="block text-sm font-medium text-foreground mt-4 mb-2"
          >
            Detail kept in a run log
          </label>
          <select
            id="episodeLogLevel"
            value={episodeLogLevel}
            onChange={(e) => onEpisodeLogLevelChange(e.target.value as EpisodeLogLevel)}
            className={`w-full ${selectBase}`}
          >
            <option value="debug">Everything the pipeline logs</option>
            <option value="info">Info and above</option>
          </select>
          <p className="mt-2 text-sm text-muted-foreground">
            Run logs keep what the server already logs, so debug lines appear only when
            LOG_LEVEL is debug too.
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

        {/* Text recurrence hints */}
        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={textRecurrenceHints}
              onChange={onTextRecurrenceHintsChange}
              ariaLabel="Text recurrence hints"
            />
            <span className="text-sm font-medium text-foreground">
              Text recurrence hints
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Tells the ad detector which wording repeats across a show's recent episodes, as a hint for intros, credits, and other boilerplate. A hint only; nothing is cut from text matches alone. Experimental; off by default.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default GlobalDefaultsSection;

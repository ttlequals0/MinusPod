import CollapsibleSection from '../../components/CollapsibleSection';
import ConnectionTestButton from './ConnectionTestButton';
import type { ConnectionTestResult } from '../../api/providers';

interface PodcastIndexSectionProps {
  searchProvider: string;
  onSearchProviderChange: (provider: string) => void;
  podcastIndexApiKeyConfigured: boolean | undefined;
  podcastIndexApiKey: string;
  podcastIndexApiSecret: string;
  onApiKeyChange: (key: string) => void;
  onApiSecretChange: (secret: string) => void;
  onConnectionTest: () => Promise<ConnectionTestResult>;
}

const STATUS_BADGE_STYLES = {
  green: { bg: 'bg-green-500/10 text-success', dot: 'bg-green-500' },
  muted: { bg: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/50' },
} as const;

function StatusBadge({ variant, label }: { variant: 'green' | 'muted'; label: string }) {
  const s = STATUS_BADGE_STYLES[variant];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${s.bg}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {label}
    </span>
  );
}

function PodcastIndexSection({
  searchProvider,
  onSearchProviderChange,
  podcastIndexApiKeyConfigured,
  podcastIndexApiKey,
  podcastIndexApiSecret,
  onApiKeyChange,
  onApiSecretChange,
  onConnectionTest,
}: PodcastIndexSectionProps) {
  // The test uses the saved credentials; block it while unsaved drafts sit
  // in the form so it cannot green-light stale values.
  const draftsPending = podcastIndexApiKey !== '' || podcastIndexApiSecret !== '';
  const usingPodcastIndex = searchProvider === 'podcastindex';
  return (
    <CollapsibleSection title="Podcast Search" storageKey="settings-section-podcast-index">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Search for podcasts by name when adding feeds.
        </p>

        <div>
          <label htmlFor="podcastSearchProvider" className="block text-sm font-medium text-foreground mb-2">
            Search provider
          </label>
          <select
            id="podcastSearchProvider"
            value={searchProvider}
            onChange={(e) => onSearchProviderChange(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
          >
            <option value="itunes">iTunes (no setup needed)</option>
            <option value="podcastindex">PodcastIndex.org (API key required)</option>
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            {usingPodcastIndex
              ? 'Searches the PodcastIndex.org directory using your API credentials.'
              : "Searches Apple's podcast directory. Nothing to configure."}
          </p>
        </div>

        {usingPodcastIndex && (
          <>
            <p className="text-sm text-muted-foreground">
              Get free API credentials at{' '}
              <a href="https://api.podcastindex.org" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                api.podcastindex.org
              </a>
            </p>

            <div>
              <label htmlFor="podcastIndexApiKey" className="block text-sm font-medium text-foreground mb-2">
                API Key
              </label>
              <input
                type="password"
                id="podcastIndexApiKey"
                value={podcastIndexApiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                placeholder={podcastIndexApiKeyConfigured ? '(configured - enter new to change)' : 'Your PodcastIndex API key'}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
              />
            </div>

            <div>
              <label htmlFor="podcastIndexApiSecret" className="block text-sm font-medium text-foreground mb-2">
                API Secret
              </label>
              <input
                type="password"
                id="podcastIndexApiSecret"
                value={podcastIndexApiSecret}
                onChange={(e) => onApiSecretChange(e.target.value)}
                placeholder={podcastIndexApiKeyConfigured ? '(configured - enter new to change)' : 'Your PodcastIndex API secret'}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
              />
            </div>

            <div>
              <p className="text-sm font-medium text-foreground mb-1">Status</p>
              {podcastIndexApiKeyConfigured ? (
                <StatusBadge variant="green" label="Configured" />
              ) : (
                <StatusBadge variant="muted" label="Not configured" />
              )}
              <ConnectionTestButton
                key={`${podcastIndexApiKeyConfigured}|${draftsPending}`}
                onTest={onConnectionTest}
                disabled={draftsPending || !podcastIndexApiKeyConfigured}
                disabledReason={draftsPending
                  ? 'Save changes first -- the test uses the saved credentials.'
                  : 'Enter and save API credentials first.'}
              />
            </div>
          </>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default PodcastIndexSection;

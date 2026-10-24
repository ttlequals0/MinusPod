import CollapsibleSection from '../../components/CollapsibleSection';
import ConnectionTestButton from './ConnectionTestButton';
import type { ConnectionTestResult } from '../../api/providers';
import { StatusDotBadge } from '../../components/StatusDotBadge';
import { focusRing, inputBase, selectBase, labelBase } from '../../components/fieldStyles';

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
    <CollapsibleSection title="Podcast search" storageKey="settings-section-podcast-index">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Search for podcasts by name when adding feeds.
        </p>

        <div>
          <label htmlFor="podcastSearchProvider" className={labelBase}>
            Search provider
          </label>
          <select
            id="podcastSearchProvider"
            value={searchProvider}
            onChange={(e) => onSearchProviderChange(e.target.value)}
            className={`w-full ${selectBase}`}
            aria-describedby="podcastSearchProvider-help"
          >
            <option value="itunes">iTunes (no setup needed)</option>
            <option value="podcastindex">PodcastIndex.org (API key required)</option>
          </select>
          <p id="podcastSearchProvider-help" className="mt-1 text-sm text-muted-foreground">
            {usingPodcastIndex
              ? 'Searches the PodcastIndex.org directory using your API credentials.'
              : "Searches Apple's podcast directory. Nothing to configure."}
          </p>
        </div>

        {usingPodcastIndex && (
          <>
            <p className="text-sm text-muted-foreground">
              Get free API credentials at{' '}
              <a href="https://api.podcastindex.org" target="_blank" rel="noopener noreferrer" className={`text-primary hover:underline ${focusRing}`}>
                api.podcastindex.org
              </a>
            </p>

            {/* A form wrapper: Chrome warns about a password input outside one. */}
            <form onSubmit={(e) => e.preventDefault()} className="space-y-4">
              <div>
                <label htmlFor="podcastIndexApiKey" className={labelBase}>
                  API Key
                </label>
                <input
                  type="password"
                  id="podcastIndexApiKey"
                  autoComplete="off"
                  value={podcastIndexApiKey}
                  onChange={(e) => onApiKeyChange(e.target.value)}
                  placeholder={podcastIndexApiKeyConfigured ? '(configured - enter new to change)' : 'Your PodcastIndex API key'}
                  className={`${inputBase} w-full font-mono`}
                />
              </div>

              <div>
                <label htmlFor="podcastIndexApiSecret" className={labelBase}>
                  API Secret
                </label>
                <input
                  type="password"
                  id="podcastIndexApiSecret"
                  autoComplete="off"
                  value={podcastIndexApiSecret}
                  onChange={(e) => onApiSecretChange(e.target.value)}
                  placeholder={podcastIndexApiKeyConfigured ? '(configured - enter new to change)' : 'Your PodcastIndex API secret'}
                  className={`${inputBase} w-full font-mono`}
                />
              </div>
            </form>

            <div>
              <p className="text-sm font-medium text-foreground mb-1">Status</p>
              {podcastIndexApiKeyConfigured ? (
                <StatusDotBadge tone="success" label="Configured" />
              ) : (
                <StatusDotBadge tone="neutral" label="Not configured" />
              )}
              <ConnectionTestButton
                key={`${podcastIndexApiKeyConfigured}|${draftsPending}`}
                onTest={onConnectionTest}
                disabled={draftsPending || !podcastIndexApiKeyConfigured}
                disabledReason={draftsPending
                  ? 'Save credentials before testing.'
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

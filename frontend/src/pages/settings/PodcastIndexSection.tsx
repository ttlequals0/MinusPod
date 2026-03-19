import CollapsibleSection from '../../components/CollapsibleSection';

interface PodcastIndexSectionProps {
  podcastIndexApiKeyConfigured: boolean | undefined;
  podcastIndexApiKey: string;
  podcastIndexApiSecret: string;
  onApiKeyChange: (key: string) => void;
  onApiSecretChange: (secret: string) => void;
}

function PodcastIndexSection({
  podcastIndexApiKeyConfigured,
  podcastIndexApiKey,
  podcastIndexApiSecret,
  onApiKeyChange,
  onApiSecretChange,
}: PodcastIndexSectionProps) {
  function StatusBadge({ variant, label }: { variant: 'green' | 'muted'; label: string }) {
    const styles = {
      green: { bg: 'bg-green-500/10 text-green-600 dark:text-green-400', dot: 'bg-green-500' },
      muted: { bg: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/50' },
    };
    const s = styles[variant];
    return (
      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${s.bg}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
        {label}
      </span>
    );
  }

  return (
    <CollapsibleSection title="Podcast Search" storageKey="settings-section-podcast-index">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Search for podcasts by name when adding feeds. Get free API credentials at{' '}
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
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
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
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
          />
        </div>

        <div>
          <p className="text-sm font-medium text-foreground mb-1">Status</p>
          {podcastIndexApiKeyConfigured ? (
            <StatusBadge variant="green" label="Configured" />
          ) : (
            <StatusBadge variant="muted" label="Not configured" />
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default PodcastIndexSection;

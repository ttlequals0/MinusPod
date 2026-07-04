import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import CopyButton from '../../components/CopyButton';
import { getSettings, updateSettings, regenerateFeedKey } from '../../api/settings';
import { regenerateAllFeeds } from '../../api/feeds';

function mutationError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function AuthenticatedFeedsSection() {
  const queryClient = useQueryClient();

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const enabled = settings?.feedAuthEnabled?.value ?? false;
  const feedAuthKey = settings?.feedAuthKey ?? null;

  // ['episode'] is invalidated too: cached episode detail carries keyed
  // processedUrl/vtt/chapters URLs that go stale on enable/disable/rotate.
  const invalidateKeyedUrls = () => {
    queryClient.invalidateQueries({ queryKey: ['settings'] });
    queryClient.invalidateQueries({ queryKey: ['feeds'] });
    queryClient.invalidateQueries({ queryKey: ['episode'] });
  };

  const toggleMutation = useMutation({
    mutationFn: (checked: boolean) => updateSettings({ feedAuthEnabled: checked }),
    onSuccess: invalidateKeyedUrls,
  });

  const regenerateKeyMutation = useMutation({
    mutationFn: regenerateFeedKey,
    onSuccess: invalidateKeyedUrls,
  });

  const regenerateFeedsMutation = useMutation({
    mutationFn: regenerateAllFeeds,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  function handleRegenerateKey() {
    if (!window.confirm('Every subscribed app immediately loses access until re-subscribed with the new key. Continue?')) return;
    regenerateKeyMutation.mutate();
  }

  return (
    <CollapsibleSection title="Authenticated Feeds" subtitle="Require a key in feed URLs">
      <div className="space-y-4">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={enabled}
              onChange={(checked) => toggleMutation.mutate(checked)}
              disabled={toggleMutation.isPending}
              ariaLabel="Require key in feed URLs"
            />
            <span className="text-sm font-medium text-foreground">
              Require key in feed URLs
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            When enabled, every feed and episode URL carries a private key, and requests without it are rejected with 401. Off by default.
          </p>
          {toggleMutation.isError && (
            <p className="mt-2 text-sm text-destructive">
              {mutationError(toggleMutation.error, 'Failed to update setting')}
            </p>
          )}
        </div>

        {enabled && feedAuthKey && (
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-border bg-background font-mono text-sm break-all">
              {feedAuthKey}
            </div>
            <CopyButton text={feedAuthKey} label="Copy key" className="shrink-0 px-2 py-1.5" />
          </div>
        )}

        {enabled && (
          <div className="pt-4 border-t border-border space-y-4">
            <div>
              <button
                type="button"
                onClick={handleRegenerateKey}
                disabled={regenerateKeyMutation.isPending}
                className="px-3 py-1.5 text-sm rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
              >
                {regenerateKeyMutation.isPending ? 'Regenerating key...' : 'Regenerate key'}
              </button>
              {regenerateKeyMutation.isSuccess && (
                <p className="mt-2 text-sm text-green-600 dark:text-green-400">Key regenerated</p>
              )}
              {regenerateKeyMutation.isError && (
                <p className="mt-2 text-sm text-destructive">
                  {mutationError(regenerateKeyMutation.error, 'Failed to regenerate key')}
                </p>
              )}
            </div>

            <div>
              <button
                type="button"
                onClick={() => regenerateFeedsMutation.mutate()}
                disabled={regenerateFeedsMutation.isPending}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
              >
                <RefreshCw className={`w-4 h-4 ${regenerateFeedsMutation.isPending ? 'animate-spin' : ''}`} />
                {regenerateFeedsMutation.isPending ? 'Regenerating feeds...' : 'Regenerate feeds'}
              </button>
              {regenerateFeedsMutation.isSuccess && regenerateFeedsMutation.data && (
                <p className="mt-2 text-sm text-green-600 dark:text-green-400">
                  Regenerated {regenerateFeedsMutation.data.feedCount} feed{regenerateFeedsMutation.data.feedCount === 1 ? '' : 's'}
                </p>
              )}
              {regenerateFeedsMutation.isError && (
                <p className="mt-2 text-sm text-destructive">
                  {mutationError(regenerateFeedsMutation.error, 'Failed to regenerate feeds')}
                </p>
              )}
            </div>

            <p className="text-sm text-muted-foreground">
              After enabling or rotating the key, re-add the feeds in your podcast apps (or re-import the modified OPML export, which includes the key). Served feeds also self-update on their next authenticated fetch.
            </p>
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default AuthenticatedFeedsSection;

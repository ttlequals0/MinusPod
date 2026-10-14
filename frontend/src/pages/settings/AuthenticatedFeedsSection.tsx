import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import CopyButton from '../../components/CopyButton';
import SubscriberKeysPanel from '../../components/SubscriberKeysPanel';
import AllSubscriberKeys from './AllSubscriberKeys';
import DownloadHistorySettings from './DownloadHistorySettings';
import { feedDisplayTitle } from '../../utils/feedTitle';
import { sortFeeds } from '../../utils/feedSort';
import { getSettings, updateSettings, regenerateFeedKey } from '../../api/settings';
import { feedsQueryOptions, regenerateAllFeeds } from '../../api/feeds';
import { btnSecondary } from '../../components/buttonStyles';
import { getErrorMessage } from '../../api/client';
import { ConfirmModal } from '../../components/Modal';
import { focusRing } from '../../components/fieldStyles';

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

  const [confirmRegenerate, setConfirmRegenerate] = useState(false);
  const { data: feedsData } = useQuery(feedsQueryOptions);
  const feeds = sortFeeds(feedsData?.feeds ?? [], 'title');
  const [selectedFeed, setSelectedFeed] = useState('');
  const activeFeed = selectedFeed || feeds[0]?.slug || '';

  function handleRegenerateKey() {
    setConfirmRegenerate(true);
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
              {getErrorMessage(toggleMutation.error, 'Failed to update setting')}
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
                className={`px-3 py-1.5 text-sm rounded-md ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
              >
                {regenerateKeyMutation.isPending ? 'Regenerating key...' : 'Regenerate key'}
              </button>
              {regenerateKeyMutation.isSuccess && (
                <p className="mt-2 text-sm text-success">Key regenerated</p>
              )}
              {regenerateKeyMutation.isError && (
                <p className="mt-2 text-sm text-destructive">
                  {getErrorMessage(regenerateKeyMutation.error, 'Failed to regenerate key')}
                </p>
              )}
            </div>

            <div>
              <button
                type="button"
                onClick={() => regenerateFeedsMutation.mutate()}
                disabled={regenerateFeedsMutation.isPending}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
              >
                <RefreshCw className={`w-4 h-4 ${regenerateFeedsMutation.isPending ? 'animate-spin' : ''}`} />
                {regenerateFeedsMutation.isPending ? 'Regenerating feeds...' : 'Regenerate feeds'}
              </button>
              {regenerateFeedsMutation.isSuccess && regenerateFeedsMutation.data && (
                <p className="mt-2 text-sm text-success">
                  Regenerated {regenerateFeedsMutation.data.feedCount} feed{regenerateFeedsMutation.data.feedCount === 1 ? '' : 's'}
                </p>
              )}
              {regenerateFeedsMutation.isError && (
                <p className="mt-2 text-sm text-destructive">
                  {getErrorMessage(regenerateFeedsMutation.error, 'Failed to regenerate feeds')}
                </p>
              )}
            </div>

            <p className="text-sm text-muted-foreground">
              After enabling or rotating the key, re-add the feeds in your podcast apps (or re-import the modified OPML export, which includes the key). Served feeds also self-update on their next authenticated fetch.
            </p>
          </div>
        )}

        <div className="pt-4 border-t border-border">
          <h3 className="text-base font-semibold text-foreground mb-1">Subscriber feed URLs</h3>
          <p className="text-sm text-muted-foreground mb-4">Create a separate feed URL for each subscriber. Revoking one URL does not affect other subscribers.</p>
          <AllSubscriberKeys hasFeeds={feeds.length > 0} />
          {feeds.length === 0 ? <p className="mt-4 text-sm text-muted-foreground">Add a feed before creating subscriber keys.</p> : (
            <div className="mt-6 space-y-3">
              <h4 className="text-sm font-semibold text-foreground">Create a subscriber feed</h4>
              <label className="block text-sm font-medium text-foreground" htmlFor="subscriberFeed">Feed</label>
              <select id="subscriberFeed" value={activeFeed} onChange={(event) => setSelectedFeed(event.target.value)} className={`w-full px-3 py-2 rounded-lg border border-input bg-background text-foreground ${focusRing}`}>
                {feeds.map((feed) => <option key={feed.slug} value={feed.slug}>{feedDisplayTitle(feed)}</option>)}
              </select>
              <SubscriberKeysPanel key={activeFeed} slug={activeFeed} />
            </div>
          )}
        </div>

        <DownloadHistorySettings />
      </div>
      {confirmRegenerate && (
        <ConfirmModal
          title="Regenerate feed key?"
          confirmLabel="Regenerate key"
          busyLabel="Regenerating..."
          pending={regenerateKeyMutation.isPending}
          onCancel={() => setConfirmRegenerate(false)}
          onConfirm={() => { setConfirmRegenerate(false); regenerateKeyMutation.mutate(); }}
        >
          <p>Every subscribed app immediately loses access until it is re-subscribed with the new key.</p>
        </ConfirmModal>
      )}
    </CollapsibleSection>
  );
}

export default AuthenticatedFeedsSection;

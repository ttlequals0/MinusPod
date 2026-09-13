import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import CopyButton from '../../components/CopyButton';
import { getSettings, updateSettings, regenerateFeedKey } from '../../api/settings';
import {
  createSubscriberKey, feedsQueryOptions, getSubscriberKeys, regenerateAllFeeds,
  revokeSubscriberKey, deleteSubscriberKeyRecord,
} from '../../api/feeds';
import { btnPrimary, btnOutline, btnSecondary } from '../../components/buttonStyles';
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
  const feeds = feedsData?.feeds ?? [];
  const [selectedFeed, setSelectedFeed] = useState('');
  const [subscriberLabel, setSubscriberLabel] = useState('');
  const [createdFeedUrl, setCreatedFeedUrl] = useState<string | null>(null);
  const [subscriberError, setSubscriberError] = useState<string | null>(null);
  const activeFeed = selectedFeed || feeds[0]?.slug || '';
  const { data: subscriberKeys = [] } = useQuery({
    queryKey: ['subscriber-keys', activeFeed],
    queryFn: () => getSubscriberKeys(activeFeed),
    enabled: Boolean(activeFeed),
  });
  const createKeyMutation = useMutation({
    mutationFn: () => createSubscriberKey(activeFeed, subscriberLabel.trim()),
    onSuccess: (created) => {
      setCreatedFeedUrl(created.feedUrl);
      setSubscriberLabel('');
      setSubscriberError(null);
      queryClient.invalidateQueries({ queryKey: ['subscriber-keys', activeFeed] });
    },
    onError: (error) => setSubscriberError(getErrorMessage(error, 'Failed to create subscriber key')),
  });
  const revokeKeyMutation = useMutation({
    mutationFn: (id: string) => revokeSubscriberKey(activeFeed, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscriber-keys', activeFeed] }),
    onError: (error) => setSubscriberError(getErrorMessage(error, 'Failed to revoke subscriber key')),
  });
  const deleteKeyMutation = useMutation({
    mutationFn: (id: string) => deleteSubscriberKeyRecord(activeFeed, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['subscriber-keys', activeFeed] }),
    onError: (error) => setSubscriberError(getErrorMessage(error, 'Failed to delete subscriber key record')),
  });
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);

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
          {feeds.length === 0 ? <p className="text-sm text-muted-foreground">Add a feed before creating subscriber keys.</p> : (
            <div className="space-y-3">
              <label className="block text-sm font-medium text-foreground" htmlFor="subscriberFeed">Feed</label>
              <select id="subscriberFeed" value={activeFeed} onChange={(event) => { setSelectedFeed(event.target.value); setCreatedFeedUrl(null); }} className={`w-full px-3 py-2 rounded-lg border border-input bg-background text-foreground ${focusRing}`}>
                {feeds.map((feed) => <option key={feed.slug} value={feed.slug}>{feed.title}</option>)}
              </select>
              <label className="block text-sm font-medium text-foreground" htmlFor="subscriberLabel">Subscriber label</label>
              <div className="flex gap-2">
                <input id="subscriberLabel" value={subscriberLabel} maxLength={100} onChange={(event) => setSubscriberLabel(event.target.value)} placeholder="Living room" className={`min-w-0 flex-1 px-3 py-2 rounded-lg border border-input bg-background text-foreground ${focusRing}`} />
                <button type="button" onClick={() => createKeyMutation.mutate()} disabled={createKeyMutation.isPending} className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 ${focusRing}`}>Create</button>
              </div>
              {createdFeedUrl && <div className="rounded-md border border-success/40 bg-success/10 p-3 text-sm"><p className="font-medium text-success mb-2">Copy this URL now. It will not be shown again.</p><div className="flex gap-2"><input readOnly value={createdFeedUrl} aria-label="New subscriber feed URL" className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 font-mono text-xs" /><CopyButton text={createdFeedUrl} label="Copy subscriber feed URL" className={`shrink-0 px-3 py-1 ${btnOutline}`} /></div></div>}
              {subscriberError && <p className="text-sm text-destructive">{subscriberError}</p>}
              <div className="space-y-2">
                {subscriberKeys.map((key) => <div key={key.id} className="flex items-center justify-between gap-3 rounded-lg border border-border p-3"><div className="min-w-0"><p className="truncate text-sm font-medium text-foreground">{key.label || 'Unlabeled subscriber'}</p><p className="text-xs text-muted-foreground">Created {new Date(key.created_at).toLocaleDateString()}{key.revoked_at ? ' - revoked' : ''}</p></div>{key.revoked_at ? <button type="button" onClick={() => setKeyToDelete(key.id)} disabled={deleteKeyMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}>Delete</button> : <button type="button" onClick={() => revokeKeyMutation.mutate(key.id)} disabled={revokeKeyMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}>Revoke</button>}</div>)}
                {subscriberKeys.length === 0 && <p className="text-sm text-muted-foreground">No subscriber keys for this feed.</p>}
              </div>
            </div>
          )}
        </div>
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
      {keyToDelete && (
        <ConfirmModal
          title="Delete revoked subscriber key record?"
          confirmLabel="Delete"
          busyLabel="Deleting..."
          pending={deleteKeyMutation.isPending}
          onCancel={() => setKeyToDelete(null)}
          onConfirm={() => deleteKeyMutation.mutate(keyToDelete, { onSuccess: () => setKeyToDelete(null) })}
        >
          <p>This only removes the revoked key record. The URL remains invalid.</p>
        </ConfirmModal>
      )}
    </CollapsibleSection>
  );
}

export default AuthenticatedFeedsSection;

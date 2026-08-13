import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import CopyButton from '../../components/CopyButton';
import { getSettings, updateSettings, regenerateFeedKey } from '../../api/settings';
import { regenerateAllFeeds } from '../../api/feeds';
import { btnSecondary, btnOutline } from '../../components/buttonStyles';
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

  function handleRegenerateKey() {
    setConfirmRegenerate(true);
  }

  const blockedAgents = settings?.jitBlockedUserAgents?.value ?? [];
  const [addingAgent, setAddingAgent] = useState(false);
  const [agentInput, setAgentInput] = useState('');
  const [agentError, setAgentError] = useState<string | null>(null);

  const agentsMutation = useMutation({
    mutationFn: (agents: string[]) => updateSettings({ jitBlockedUserAgents: agents }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const addBlockedAgent = () => {
    const pattern = agentInput.trim();
    if (!pattern) return;
    if (blockedAgents.includes(pattern)) {
      setAgentInput('');
      setAddingAgent(false);
      return;
    }
    setAgentError(null);
    agentsMutation.mutate([...blockedAgents, pattern], {
      onSuccess: () => {
        setAgentInput('');
        setAddingAgent(false);
      },
      onError: (e) => setAgentError(getErrorMessage(e, 'Failed to add agent')),
    });
  };

  const removeBlockedAgent = (agent: string) => {
    setAgentError(null);
    agentsMutation.mutate(blockedAgents.filter((a) => a !== agent), {
      onError: (e) => setAgentError(getErrorMessage(e, 'Failed to remove agent')),
    });
  };

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
          <span className="block text-sm font-medium text-foreground mb-2">
            Agents that skip processing
          </span>
          {blockedAgents.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {blockedAgents.map((agent) => (
                <span
                  key={agent}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded bg-c-blue/15 text-c-blue"
                >
                  {agent}
                  <button
                    type="button"
                    onClick={() => removeBlockedAgent(agent)}
                    disabled={agentsMutation.isPending}
                    className={`text-c-blue/60 dark:text-c-blue/60 hover:text-destructive dark:hover:text-destructive disabled:opacity-50 ${focusRing}`}
                    aria-label={`Remove ${agent}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            {!addingAgent ? (
              <button
                type="button"
                onClick={() => setAddingAgent(true)}
                disabled={agentsMutation.isPending}
                className={`px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
              >
                + Add agent
              </button>
            ) : (
              <>
                <input
                  type="text"
                  autoFocus
                  value={agentInput}
                  onChange={(e) => setAgentInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addBlockedAgent();
                    }
                  }}
                  placeholder="^atc/"
                  aria-label="New blocked agent pattern"
                  maxLength={200}
                  className={`px-2 py-1 text-xs bg-secondary border border-border rounded flex-1 min-w-0 ${focusRing}`}
                />
                <button
                  type="button"
                  onClick={addBlockedAgent}
                  disabled={agentsMutation.isPending || !agentInput.trim()}
                  className={`px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
                >
                  Add
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAddingAgent(false);
                    setAgentInput('');
                    setAgentError(null);
                  }}
                  className={`px-2 py-1 text-xs rounded ${btnOutline} ${focusRing}`}
                >
                  Cancel
                </button>
              </>
            )}
          </div>
          {agentError && (
            <p className="mt-2 text-sm text-destructive">{agentError}</p>
          )}
          <p className="mt-2 text-sm text-muted-foreground">
            Agents listed here are served the original audio instead of triggering processing. Case-insensitive, matches anywhere in the agent string. Start a pattern with ^ to match only the beginning, for example ^atc/.
          </p>
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
    </CollapsibleSection>
  );
}

export default AuthenticatedFeedsSection;

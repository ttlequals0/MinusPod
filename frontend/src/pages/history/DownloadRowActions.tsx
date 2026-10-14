import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../../api/client';
import { ALL_SUBSCRIBER_KEYS_QUERY_KEY, revokeSubscriberKey } from '../../api/feeds';
import { updateSettings } from '../../api/settings';
import type { DownloadHistoryEntry } from '../../api/types';
import { btnOutline } from '../../components/buttonStyles';
import DropdownMenu, { type DropdownMenuItem } from '../../components/DropdownMenu';
import { ConfirmModal } from '../../components/Modal';
import { blockPatternFor, userAgentIsBlocked } from '../../utils/userAgent';

interface Props {
  row: DownloadHistoryEntry;
  // Current JIT blocked list; undefined until settings load.
  blocked: string[] | undefined;
  triggerClassName?: string;
}

type Pending = 'block' | 'revoke' | null;

function DownloadRowActions({ row, blocked, triggerClassName = 'px-3 py-1.5' }: Props) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);

  const pattern = blockPatternFor(row.userAgent);
  const alreadyBlocked = userAgentIsBlocked(row.userAgent, blocked ?? []);

  const close = () => {
    setPending(null);
    setError(null);
  };

  const blockMutation = useMutation({
    mutationFn: () => updateSettings({ jitBlockedUserAgents: [...(blocked ?? []), pattern] }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      close();
    },
    onError: (e) => setError(getErrorMessage(e, 'Failed to block user agent')),
  });

  const revokeMutation = useMutation({
    mutationFn: () => revokeSubscriberKey(row.podcastSlug, row.subscriberKeyId as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['subscriber-keys', row.podcastSlug] });
      queryClient.invalidateQueries({ queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY });
      queryClient.invalidateQueries({ queryKey: ['download-history'] });
      close();
    },
    onError: (e) => setError(getErrorMessage(e, 'Failed to revoke subscriber')),
  });

  const open = (next: Pending) => {
    setError(null);
    setPending(next);
  };

  const items: DropdownMenuItem[] = [{
    title: 'Block user agent',
    onClick: () => open('block'),
    disabled: alreadyBlocked || blocked === undefined,
    tooltip: alreadyBlocked ? 'Already blocked' : undefined,
  }];
  if (row.subscriberKeyId) {
    items.push({
      title: 'Revoke subscriber',
      onClick: () => open('revoke'),
      disabled: row.subscriberRevoked,
      tooltip: row.subscriberRevoked ? 'Already revoked' : undefined,
    });
  }

  const errorLine = error && (
    <p role="alert" className="text-sm text-destructive">{error}</p>
  );

  return (
    <>
      <DropdownMenu
        triggerLabel="Actions"
        triggerClassName={`${triggerClassName} text-sm rounded ${btnOutline} inline-flex items-center gap-1 transition-colors`}
        chevronClassName="w-3.5 h-3.5"
        align="right"
        items={items}
      />
      {pending === 'block' && (
        <ConfirmModal
          title="Block user agent"
          confirmLabel="Block"
          busyLabel="Blocking..."
          destructive={false}
          pending={blockMutation.isPending}
          onCancel={close}
          onConfirm={() => blockMutation.mutate()}
        >
          <p>This adds the following pattern to the JIT blocked user agents list:</p>
          <p><code className="font-mono break-all">{pattern}</code></p>
          <p className="text-muted-foreground">
            Matching clients are redirected to the original audio instead of triggering
            just-in-time processing. Feeds, subscriber access and already processed episodes are unaffected.
          </p>
          {errorLine}
        </ConfirmModal>
      )}
      {pending === 'revoke' && (
        <ConfirmModal
          title="Revoke subscriber"
          confirmLabel="Revoke"
          busyLabel="Revoking..."
          pending={revokeMutation.isPending}
          onCancel={close}
          onConfirm={() => revokeMutation.mutate()}
        >
          <p>
            Revoke <strong>{row.subscriberLabel || 'Unlabeled'}</strong> on {row.podcastTitle}?
            Podcast apps using this feed URL stop receiving the feed and audio.
          </p>
          {errorLine}
        </ConfirmModal>
      )}
    </>
  );
}

export default DownloadRowActions;

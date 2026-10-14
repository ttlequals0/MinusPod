import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../../api/client';
import { getSettings, updateSettings } from '../../api/settings';
import type { DownloadHistoryEntry } from '../../api/types';
import { btnOutline } from '../../components/buttonStyles';
import DropdownMenu, { type DropdownMenuItem } from '../../components/DropdownMenu';
import { ConfirmModal } from '../../components/Modal';
import { useSubscriberKeyActions } from '../../hooks/useSubscriberKeyActions';
import { blockPatternFor } from '../../utils/userAgent';

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
  const [blockError, setBlockError] = useState<string | null>(null);
  const { revokeMutation, error: revokeError, clearError } = useSubscriberKeyActions();

  const pattern = blockPatternFor(row.userAgent);
  const alreadyBlocked = row.userAgentBlocked;

  const close = () => {
    setPending(null);
    setBlockError(null);
    clearError();
  };

  const finish = () => {
    queryClient.invalidateQueries({ queryKey: ['download-history'] });
    close();
  };

  const blockMutation = useMutation({
    mutationFn: async () => {
      // The PUT replaces the whole list, so build it from a fresh read.
      const fresh = await queryClient.fetchQuery({ queryKey: ['settings'], queryFn: getSettings, staleTime: 0 });
      const list = fresh.jitBlockedUserAgents.value;
      if (!list.includes(pattern)) await updateSettings({ jitBlockedUserAgents: [...list, pattern] });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      finish();
    },
    onError: (e) => setBlockError(getErrorMessage(e, 'Failed to block user agent')),
  });

  const open = (next: Pending) => {
    close();
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

  const error = pending === 'block' ? blockError : revokeError;
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
            Matching clients requesting an episode that has not been processed yet are redirected
            to the original audio instead of starting processing. Local feeds, processed episodes,
            feed access and subscriber access are unaffected.
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
          onConfirm={() => revokeMutation.mutate(
            { slug: row.podcastSlug, id: row.subscriberKeyId as string },
            { onSuccess: finish },
          )}
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

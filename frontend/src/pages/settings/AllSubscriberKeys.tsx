import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAllSubscriberKeys, revokeSubscriberKey, deleteSubscriberKeyRecord } from '../../api/feeds';
import type { SubscriberKeyWithFeed } from '../../api/feeds';
import { ALL_SUBSCRIBER_KEYS_QUERY_KEY } from '../../components/SubscriberKeysPanel';
import { ConfirmModal } from '../../components/Modal';
import { getErrorMessage } from '../../api/client';
import { btnOutline } from '../../components/buttonStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { focusRing } from '../../components/fieldStyles';

const thClass = 'px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider';
const actionClass = `px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`;

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : 'Never';
}

function StatusBadge({ revoked }: { revoked: boolean }) {
  return (
    <span className={`${badgeBase} ${revoked ? tint.destructive : tint.success}`}>
      {revoked ? 'Revoked' : 'Active'}
    </span>
  );
}

function AllSubscriberKeys() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [keyToDelete, setKeyToDelete] = useState<SubscriberKeyWithFeed | null>(null);

  const { data: keys = [] } = useQuery({
    queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY,
    queryFn: getAllSubscriberKeys,
  });

  const invalidate = (slug: string) => {
    queryClient.invalidateQueries({ queryKey: ['subscriber-keys', slug] });
    queryClient.invalidateQueries({ queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY });
  };
  const revokeMutation = useMutation({
    mutationFn: (key: SubscriberKeyWithFeed) => revokeSubscriberKey(key.feedSlug, key.id),
    onSuccess: (_, key) => invalidate(key.feedSlug),
    onError: (err) => setError(getErrorMessage(err, 'Failed to revoke subscriber key')),
  });
  const deleteMutation = useMutation({
    mutationFn: (key: SubscriberKeyWithFeed) => deleteSubscriberKeyRecord(key.feedSlug, key.id),
    onSuccess: (_, key) => invalidate(key.feedSlug),
    onError: (err) => setError(getErrorMessage(err, 'Failed to delete subscriber key record')),
  });

  function actionButton(key: SubscriberKeyWithFeed) {
    return key.revokedAt ? (
      <button type="button" onClick={() => setKeyToDelete(key)} disabled={deleteMutation.isPending} className={actionClass}>
        Delete record
      </button>
    ) : (
      <button type="button" onClick={() => revokeMutation.mutate(key)} disabled={revokeMutation.isPending} className={actionClass}>
        Revoke
      </button>
    );
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-foreground">All subscriber feeds</h4>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {keys.length === 0 ? (
        <p className="text-sm text-muted-foreground">No subscriber feeds yet. Create one below.</p>
      ) : (
        <>
          <div className="hidden sm:block bg-card border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-muted/50">
                  <th className={thClass}>Feed</th>
                  <th className={thClass}>Label</th>
                  <th className={thClass}>Created</th>
                  <th className={thClass}>Last used</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id} className="border-t border-border hover:bg-muted/50">
                    <td className="px-4 py-3 text-foreground">{key.feedTitle}</td>
                    <td className="px-4 py-3 text-foreground">{key.label || 'Unlabeled subscriber'}</td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(key.createdAt)}</td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(key.lastUsedAt)}</td>
                    <td className="px-4 py-3"><StatusBadge revoked={Boolean(key.revokedAt)} /></td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">{actionButton(key)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sm:hidden space-y-2">
            {keys.map((key) => (
              <div key={key.id} className="bg-card border border-border rounded-lg p-4 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-sm font-medium text-foreground">{key.feedTitle}</span>
                  <StatusBadge revoked={Boolean(key.revokedAt)} />
                </div>
                <p className="text-sm text-foreground">{key.label || 'Unlabeled subscriber'}</p>
                <div className="flex gap-4 text-xs text-muted-foreground">
                  <span>Created {formatDate(key.createdAt)}</span>
                  <span>Last used {formatDate(key.lastUsedAt)}</span>
                </div>
                <div>{actionButton(key)}</div>
              </div>
            ))}
          </div>
        </>
      )}
      {keyToDelete && (
        <ConfirmModal
          title="Delete revoked subscriber key record?"
          confirmLabel="Delete"
          busyLabel="Deleting..."
          pending={deleteMutation.isPending}
          onCancel={() => setKeyToDelete(null)}
          onConfirm={() => deleteMutation.mutate(keyToDelete, { onSuccess: () => setKeyToDelete(null) })}
        >
          <p>This only removes the revoked key record. The URL remains invalid.</p>
        </ConfirmModal>
      )}
    </div>
  );
}

export default AllSubscriberKeys;

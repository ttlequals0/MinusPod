import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAllSubscriberKeys, ALL_SUBSCRIBER_KEYS_QUERY_KEY } from '../../api/feeds';
import type { SubscriberKeyWithFeed } from '../../api/feeds';
import DeleteKeyRecordModal from '../../components/DeleteKeyRecordModal';
import { useSubscriberKeyActions } from '../../hooks/useSubscriberKeyActions';
import { btnOutline, btnDestructive } from '../../components/buttonStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { focusRing } from '../../components/fieldStyles';
import { formatDate, keyLabel } from '../../utils/format';

const thClass = 'px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider';
const actionBase = `px-3 py-1 text-sm rounded disabled:opacity-50 ${focusRing}`;
const revokeClass = `${actionBase} ${btnDestructive}`;
const deleteClass = `${actionBase} ${btnOutline}`;

function StatusBadge({ revoked }: { revoked: boolean }) {
  return (
    <span className={`${badgeBase} ${revoked ? tint.destructive : tint.success}`}>
      {revoked ? 'Revoked' : 'Active'}
    </span>
  );
}

function AllSubscriberKeys({ hasFeeds }: { hasFeeds: boolean }) {
  const [keyToDelete, setKeyToDelete] = useState<SubscriberKeyWithFeed | null>(null);
  const { revokeMutation, deleteMutation, error } = useSubscriberKeyActions();

  const { data: keys = [], isError } = useQuery({
    queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY,
    queryFn: getAllSubscriberKeys,
  });
  const toRef = (key: SubscriberKeyWithFeed) => ({ slug: key.feedSlug, id: key.id });

  function actionButton(key: SubscriberKeyWithFeed) {
    return key.revokedAt ? (
      <button type="button" onClick={() => setKeyToDelete(key)} disabled={deleteMutation.isPending} className={deleteClass}>
        Delete record
      </button>
    ) : (
      <button type="button" onClick={() => revokeMutation.mutate(toRef(key))} disabled={revokeMutation.isPending} className={revokeClass}>
        Revoke
      </button>
    );
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-foreground">All subscriber feeds</h4>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {isError && (
        <p className="text-sm text-destructive">
          {keys.length > 0
            ? 'Failed to refresh subscriber feeds. Showing the last loaded list.'
            : 'Failed to load subscriber feeds.'}
        </p>
      )}
      {keys.length === 0 ? (
        !isError && (
          <p className="text-sm text-muted-foreground">
            {hasFeeds ? 'No subscriber feeds yet. Create one below.' : 'No subscriber feeds yet.'}
          </p>
        )
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
                    <td className="px-4 py-3 text-foreground">{keyLabel(key)}</td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(key.createdAt)}</td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{key.lastUsedAt ? formatDate(key.lastUsedAt) : 'Never'}</td>
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
                <p className="text-sm text-foreground">{keyLabel(key)}</p>
                <div className="flex gap-4 text-xs text-muted-foreground">
                  <span>Created {formatDate(key.createdAt)}</span>
                  <span>Last used {key.lastUsedAt ? formatDate(key.lastUsedAt) : 'Never'}</span>
                </div>
                <div>{actionButton(key)}</div>
              </div>
            ))}
          </div>
        </>
      )}
      {keyToDelete && (
        <DeleteKeyRecordModal
          pending={deleteMutation.isPending}
          onCancel={() => setKeyToDelete(null)}
          onConfirm={() => deleteMutation.mutate(toRef(keyToDelete), { onSuccess: () => setKeyToDelete(null) })}
        />
      )}
    </div>
  );
}

export default AllSubscriberKeys;

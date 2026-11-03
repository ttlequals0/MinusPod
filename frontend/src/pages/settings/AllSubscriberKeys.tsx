import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAllSubscriberKeys, ALL_SUBSCRIBER_KEYS_QUERY_KEY } from '../../api/feeds';
import type { SubscriberKeyWithFeed } from '../../api/feeds';
import DeleteKeyRecordModal from '../../components/DeleteKeyRecordModal';
import { useSubscriberKeyActions } from '../../hooks/useSubscriberKeyActions';
import { formatDateTime, keyLabel } from '../../utils/format';
import { btnBase, btnOutline, btnDestructive } from '../../components/buttonStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { tableBody, tableCard, tableHead, tableRow, thBase } from '../../components/tableStyles';
import { subHeading } from '../../components/pageStyles';

// The settings card is 768 px wide, so this six-column table uses px-3 cells.
const head = `${thBase} px-3 py-3 text-left`;
const cell = 'px-3 py-3 text-sm';
const cellMuted = `${cell} text-muted-foreground`;
const revokeClass = `${btnBase} ${btnDestructive}`;
const deleteClass = `${btnBase} ${btnOutline}`;

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
      <h4 className={subHeading}>All subscriber feeds</h4>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {isError && (
        <p className="text-sm text-destructive">
          {keys.length > 0
            ? 'Could not refresh subscriber feeds. Showing the last loaded list.'
            : 'Could not load subscriber feeds.'}
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
          <div className={`hidden sm:block ${tableCard}`}>
            <div role="region" aria-label="Subscriber feeds; scroll for more columns" tabIndex={0} className="overflow-x-auto">
            <table className="w-full">
              <thead className={tableHead}>
                <tr>
                  <th className={head}>Feed</th>
                  <th className={head}>Label</th>
                  <th className={head}>Created</th>
                  <th className={head}>Last used</th>
                  <th className={head}>Status</th>
                  <th className={head}><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className={tableBody}>
                {keys.map((key) => (
                  <tr key={key.id} className={tableRow}>
                    <td className={`${cell} text-foreground`}>{key.feedTitle}</td>
                    <td className={`${cell} text-foreground`}>{keyLabel(key)}</td>
                    <td className={`${cellMuted} whitespace-nowrap`}>{formatDateTime(key.createdAt)}</td>
                    <td className={`${cellMuted} whitespace-nowrap`}>{formatDateTime(key.lastUsedAt ?? null)}</td>
                    <td className={cell}><StatusBadge revoked={Boolean(key.revokedAt)} /></td>
                    <td className={`${cell} text-right whitespace-nowrap`}>{actionButton(key)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
          <div className="sm:hidden space-y-2">
            {keys.map((key) => (
              <div key={key.id} className="bg-muted/40 rounded-lg p-4 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-sm font-medium text-foreground">{key.feedTitle}</span>
                  <StatusBadge revoked={Boolean(key.revokedAt)} />
                </div>
                <p className="text-sm text-foreground">{keyLabel(key)}</p>
                <div className="flex gap-4 text-xs text-muted-foreground">
                  <span>Created {formatDateTime(key.createdAt)}</span>
                  <span>Last used {formatDateTime(key.lastUsedAt ?? null)}</span>
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

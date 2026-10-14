import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import CopyButton from './CopyButton';
import DeleteKeyRecordModal from './DeleteKeyRecordModal';
import { useSubscriberKeyActions } from '../hooks/useSubscriberKeyActions';
import { createSubscriberKey, getSubscriberKeys } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import { formatDate, keyLabel } from '../utils/format';
import { btnPrimary, btnOutline, btnDestructive } from './buttonStyles';
import { focusRing } from './fieldStyles';

interface SubscriberKeysPanelProps {
  slug: string;
}

function SubscriberKeysPanel({ slug }: SubscriberKeysPanelProps) {
  const [label, setLabel] = useState('');
  const [createdFeedUrl, setCreatedFeedUrl] = useState<string | null>(null);
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);
  const labelId = `subscriberLabel-${slug}`;

  const { data: keys = [] } = useQuery({
    queryKey: ['subscriber-keys', slug],
    queryFn: () => getSubscriberKeys(slug),
  });

  const { revokeMutation, deleteMutation, error, setError, clearError, invalidate } = useSubscriberKeyActions();

  const createMutation = useMutation({
    mutationFn: () => createSubscriberKey(slug, label.trim()),
    onMutate: clearError,
    onSuccess: (created) => {
      setCreatedFeedUrl(created.feedUrl);
      setLabel('');
      invalidate(slug);
    },
    onError: (err) => setError(getErrorMessage(err, 'Failed to create subscriber key')),
  });

  return (
    <div className="space-y-3">
      <label className="block text-sm font-medium text-foreground" htmlFor={labelId}>Subscriber label</label>
      <div className="flex gap-2">
        <input
          id={labelId}
          value={label}
          maxLength={100}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Living room"
          className={`min-w-0 flex-1 px-3 py-2 rounded-lg border border-input bg-background text-foreground ${focusRing}`}
        />
        <button
          type="button"
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 ${focusRing}`}
        >
          Create
        </button>
      </div>
      {createdFeedUrl && (
        <div className="rounded-md border border-success/40 bg-success/10 p-3 text-sm">
          <p className="font-medium text-success mb-2">Copy this URL now. It will not be shown again.</p>
          <div className="flex gap-2">
            <input
              readOnly
              value={createdFeedUrl}
              aria-label="New subscriber feed URL"
              className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 font-mono text-xs"
            />
            <CopyButton text={createdFeedUrl} label="Copy subscriber feed URL" className={`shrink-0 px-3 py-1 ${btnOutline}`} />
          </div>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="space-y-2">
        {keys.map((key) => (
          <div key={key.id} className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{keyLabel(key)}</p>
              <p className="text-xs text-muted-foreground">
                Created {formatDate(key.created_at)}{key.revoked_at ? ' - revoked' : ''}
              </p>
            </div>
            {key.revoked_at ? (
              <button type="button" onClick={() => setKeyToDelete(key.id)} disabled={deleteMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}>Delete</button>
            ) : (
              <button type="button" onClick={() => revokeMutation.mutate({ slug, id: key.id })} disabled={revokeMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnDestructive} disabled:opacity-50 ${focusRing}`}>Revoke</button>
            )}
          </div>
        ))}
        {keys.length === 0 && <p className="text-sm text-muted-foreground">No subscriber keys for this feed.</p>}
      </div>
      {keyToDelete && (
        <DeleteKeyRecordModal
          pending={deleteMutation.isPending}
          onCancel={() => setKeyToDelete(null)}
          onConfirm={() => deleteMutation.mutate({ slug, id: keyToDelete }, { onSuccess: () => setKeyToDelete(null) })}
        />
      )}
    </div>
  );
}

export default SubscriberKeysPanel;

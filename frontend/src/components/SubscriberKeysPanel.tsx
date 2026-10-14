import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CopyButton from './CopyButton';
import { ConfirmModal } from './Modal';
import {
  createSubscriberKey, getSubscriberKeys, revokeSubscriberKey, deleteSubscriberKeyRecord,
} from '../api/feeds';
import { getErrorMessage } from '../api/client';
import { btnPrimary, btnOutline } from './buttonStyles';
import { focusRing } from './fieldStyles';

export const ALL_SUBSCRIBER_KEYS_QUERY_KEY = ['subscriber-keys-all'] as const;

interface SubscriberKeysPanelProps {
  slug: string;
}

function SubscriberKeysPanel({ slug }: SubscriberKeysPanelProps) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState('');
  const [createdFeedUrl, setCreatedFeedUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keyToDelete, setKeyToDelete] = useState<string | null>(null);
  const labelId = `subscriberLabel-${slug}`;

  const { data: keys = [] } = useQuery({
    queryKey: ['subscriber-keys', slug],
    queryFn: () => getSubscriberKeys(slug),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['subscriber-keys', slug] });
    queryClient.invalidateQueries({ queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY });
  };

  const createMutation = useMutation({
    mutationFn: () => createSubscriberKey(slug, label.trim()),
    onSuccess: (created) => {
      setCreatedFeedUrl(created.feedUrl);
      setLabel('');
      setError(null);
      invalidate();
    },
    onError: (err) => setError(getErrorMessage(err, 'Failed to create subscriber key')),
  });
  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeSubscriberKey(slug, id),
    onSuccess: invalidate,
    onError: (err) => setError(getErrorMessage(err, 'Failed to revoke subscriber key')),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSubscriberKeyRecord(slug, id),
    onSuccess: invalidate,
    onError: (err) => setError(getErrorMessage(err, 'Failed to delete subscriber key record')),
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
              <p className="truncate text-sm font-medium text-foreground">{key.label || 'Unlabeled subscriber'}</p>
              <p className="text-xs text-muted-foreground">
                Created {new Date(key.created_at).toLocaleDateString()}{key.revoked_at ? ' - revoked' : ''}
              </p>
            </div>
            {key.revoked_at ? (
              <button type="button" onClick={() => setKeyToDelete(key.id)} disabled={deleteMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}>Delete</button>
            ) : (
              <button type="button" onClick={() => revokeMutation.mutate(key.id)} disabled={revokeMutation.isPending} className={`px-3 py-1 text-sm rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}>Revoke</button>
            )}
          </div>
        ))}
        {keys.length === 0 && <p className="text-sm text-muted-foreground">No subscriber keys for this feed.</p>}
      </div>
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

export default SubscriberKeysPanel;

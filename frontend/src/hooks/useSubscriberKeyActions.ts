import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { revokeSubscriberKey, deleteSubscriberKeyRecord, ALL_SUBSCRIBER_KEYS_QUERY_KEY } from '../api/feeds';
import { getErrorMessage } from '../api/client';

export interface SubscriberKeyRef {
  slug: string;
  id: string;
}

// Revoke and delete-record mutations shared by the per-feed panel and the all-feeds table.
export function useSubscriberKeyActions() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const invalidate = (slug: string) => {
    queryClient.invalidateQueries({ queryKey: ['subscriber-keys', slug] });
    queryClient.invalidateQueries({ queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY });
  };

  const revokeMutation = useMutation({
    mutationFn: (key: SubscriberKeyRef) => revokeSubscriberKey(key.slug, key.id),
    onMutate: () => setError(null),
    onSuccess: (_data, key) => invalidate(key.slug),
    onError: (err) => setError(getErrorMessage(err, 'Failed to revoke subscriber key')),
  });
  const deleteMutation = useMutation({
    mutationFn: (key: SubscriberKeyRef) => deleteSubscriberKeyRecord(key.slug, key.id),
    onMutate: () => setError(null),
    onSuccess: (_data, key) => invalidate(key.slug),
    onError: (err) => setError(getErrorMessage(err, 'Failed to delete subscriber key record')),
  });

  return { revokeMutation, deleteMutation, error, invalidate };
}

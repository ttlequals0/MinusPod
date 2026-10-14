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
  const clearError = () => setError(null);

  const invalidate = (slug: string) => {
    queryClient.invalidateQueries({ queryKey: ['subscriber-keys', slug] });
    queryClient.invalidateQueries({ queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY });
  };

  const keyMutationOptions = (
    fn: (key: SubscriberKeyRef) => Promise<unknown>,
    errorMessage: string,
  ) => ({
    mutationFn: fn,
    onMutate: clearError,
    onSuccess: (_data: unknown, key: SubscriberKeyRef) => invalidate(key.slug),
    onError: (err: unknown) => setError(getErrorMessage(err, errorMessage)),
  });

  const revokeMutation = useMutation(keyMutationOptions(
    (key) => revokeSubscriberKey(key.slug, key.id), 'Failed to revoke subscriber key'));
  const deleteMutation = useMutation(keyMutationOptions(
    (key) => deleteSubscriberKeyRecord(key.slug, key.id), 'Failed to delete subscriber key record'));

  return { revokeMutation, deleteMutation, error, setError, clearError, invalidate };
}

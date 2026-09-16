import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../api/client';
import { modelsQueryOptionsFor, refreshModels } from '../api/settings';
import type { ProviderSlot } from '../api/types';

/** One catalog a Refresh rebuilds: the slot, and the provider it is keyed by. */
export interface CatalogTarget {
  provider: string;
  slot: ProviderSlot;
}

export interface ModelsRefresh {
  refresh: () => void;
  isPending: boolean;
  error: string | null;
}

// One mutation per Refresh button: a shared instance drops the first click's
// pending state as soon as the second click rebinds the observer.
export function useModelsRefresh(targets: CatalogTarget[]): ModelsRefresh {
  const queryClient = useQueryClient();
  // Outside the mutation so a failure stays on screen through the next attempt
  // and clears only on success, in the section whose button started it.
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async () => {
      const slots = Array.from(new Set(targets.map((t) => t.slot)));
      await Promise.all(slots.map((slot) => refreshModels(slot)));
      return targets;
    },
    onSuccess: (rebuilt) => {
      setError(null);
      // Only the rebuilt slots are stale; the ['models'] prefix would refetch
      // catalogs this refresh never touched.
      for (const target of rebuilt) {
        queryClient.invalidateQueries({
          queryKey: modelsQueryOptionsFor(target.provider, target.slot).queryKey,
        });
      }
    },
    onError: (e: unknown) =>
      setError(getErrorMessage(e, 'Could not refresh this provider\'s model list.')),
  });
  return { refresh: () => mutation.mutate(), isPending: mutation.isPending, error };
}

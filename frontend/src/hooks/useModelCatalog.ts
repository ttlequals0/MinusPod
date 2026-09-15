import { useQuery } from '@tanstack/react-query';
import { modelsQueryOptionsFor } from '../api/settings';
import type { ClaudeModel, ProviderSlot } from '../api/types';

export interface ModelCatalog {
  models: ClaudeModel[] | undefined;
  isLoading: boolean;
  isError: boolean;
}

/** One stage's model catalog, keyed by the provider and slot it resolves to. */
export function useModelCatalog(
  provider: string,
  slot: ProviderSlot,
  enabled: boolean,
): ModelCatalog {
  const { data, isLoading, isError } = useQuery({
    ...modelsQueryOptionsFor(provider, slot),
    // Gate on the provider too: it is an empty placeholder until hydration runs.
    enabled: enabled && !!provider,
  });
  return { models: data, isLoading, isError };
}

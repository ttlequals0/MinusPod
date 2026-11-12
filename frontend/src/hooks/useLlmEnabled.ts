import { useQuery } from '@tanstack/react-query';
import { getSettings } from '../api/settings';
import type { Settings } from '../api/types';

// True until the saved settings say the LLM provider is None.
export function isLlmEnabled(settings: Pick<Settings, 'llmEnabled'> | undefined): boolean {
  return settings?.llmEnabled !== false;
}

export function useLlmEnabled(): boolean {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  return isLlmEnabled(data);
}

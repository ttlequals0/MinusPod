import { useQuery } from '@tanstack/react-query';
import { getSettings } from '../api/settings';

// Reads the saved llmEnabled flag from the shared settings query; true until it loads.
export function useLlmEnabled(): boolean {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  return data?.llmEnabled !== false;
}

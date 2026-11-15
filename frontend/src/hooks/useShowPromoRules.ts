import { useQuery } from '@tanstack/react-query';
import { getSettings } from '../api/settings';
import type { ShowPromoRules } from '../api/types';

// Same query key as the settings page, so no extra request; undefined until loaded,
// when callers skip their pre-checks and the server still judges.
export function useShowPromoRules(): ShowPromoRules | undefined {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  return data?.showPromoRules;
}

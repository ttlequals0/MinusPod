import { useQuery } from '@tanstack/react-query';
import { getSettings } from '../api/settings';
import type { ShowPromoRules } from '../api/types';

// The server's rules for learning a show pattern, read from the settings the
// page already loads (same query key, so no extra request). Undefined until
// loaded; callers then skip their pre-checks and the server still judges.
export function useShowPromoRules(): ShowPromoRules | undefined {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  return data?.showPromoRules;
}

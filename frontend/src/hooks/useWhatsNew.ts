import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getChangelog, getHealth, getSettings } from '../api/settings';
import { useLocalStorageState } from './useLocalStorageState';

export const WHATS_NEW_SEEN_KEY = 'whatsNewSeenVersion';

/** Opens the changelog once per browser after the app version changes, or on its first load. */
export function useWhatsNew(enabled: boolean) {
  const [seen, setSeen] = useLocalStorageState(WHATS_NEW_SEEN_KEY, '');
  const [closed, setClosed] = useState(false);
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    enabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const current = health.data?.version;
  const upgraded = !!current && seen !== current;
  const settings = useQuery({ queryKey: ['settings'], queryFn: getSettings, enabled: enabled && upgraded });
  const optedIn = settings.data ? settings.data.whatsNewOnUpgrade?.value !== false : undefined;
  const changelog = useQuery({
    queryKey: ['changelog', seen],
    queryFn: () => getChangelog(seen ? { since: seen } : { limit: 1 }),
    enabled: enabled && upgraded && optedIn === true,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const sections = changelog.data?.sections;
  const nothingNew = !!changelog.data && !sections?.length;
  // A fresh browser only gets the running version's own section.
  const stale = !!changelog.data && !seen && sections?.[0]?.version !== current;

  useEffect(() => {
    if (!enabled || !current) return;
    if (optedIn === false || nothingNew || stale) setSeen(current);
  }, [enabled, current, optedIn, nothingNew, stale, setSeen]);

  const close = useCallback(() => {
    if (current) setSeen(current);
    setClosed(true);
  }, [current, setSeen]);

  return {
    open: enabled && upgraded && optedIn === true && !closed && !stale && !nothingNew,
    current: current ?? '',
    sections,
    close,
  };
}

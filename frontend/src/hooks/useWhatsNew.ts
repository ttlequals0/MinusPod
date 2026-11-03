import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getChangelog, getHealth, getSettings } from '../api/settings';
import { useLocalStorageState } from './useLocalStorageState';

export const WHATS_NEW_SEEN_KEY = 'whatsNewSeenVersion';

/** Opens the changelog once per browser after the app version changes. */
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
  const upgraded = !!current && !!seen && seen !== current;
  const settings = useQuery({ queryKey: ['settings'], queryFn: getSettings, enabled: enabled && upgraded });
  const optedIn = settings.data ? settings.data.whatsNewOnUpgrade?.value !== false : undefined;
  const changelog = useQuery({
    queryKey: ['changelog', seen],
    queryFn: () => getChangelog({ since: seen }),
    enabled: enabled && upgraded && optedIn === true,
  });
  const sections = changelog.data?.sections;

  useEffect(() => {
    if (!enabled || !current) return;
    if (!seen || optedIn === false || sections?.length === 0) setSeen(current);
  }, [enabled, current, seen, optedIn, sections, setSeen]);

  const close = useCallback(() => {
    if (current) setSeen(current);
    setClosed(true);
  }, [current, setSeen]);

  return {
    open: enabled && !!current && !!seen && seen !== current && optedIn === true && !closed && (sections?.length ?? 0) > 0,
    current: current ?? '',
    sections,
    close,
  };
}

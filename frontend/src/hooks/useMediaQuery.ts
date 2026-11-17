import { useCallback, useSyncExternalStore } from 'react';

/** Whether a CSS media query matches now, updating when it changes. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((notify: () => void) => {
    const list = window.matchMedia?.(query);
    list?.addEventListener('change', notify);
    return () => list?.removeEventListener('change', notify);
  }, [query]);
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}

import { createContext, useContext } from 'react';

// The set of CollapsibleSection storage-keys whose title or settings match the
// active settings search, or null when no search is active. CollapsibleSection
// reads it to self-filter; null outside a page that provides the context.
export const SettingsSearchContext = createContext<Set<string> | null>(null);

export function useSettingsSearch(): Set<string> | null {
  return useContext(SettingsSearchContext);
}

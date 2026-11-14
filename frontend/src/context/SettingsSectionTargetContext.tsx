import { createContext, useContext } from 'react';

// The section a /settings#<id> link points at. `seq` changes per navigation so clicking the same link again
// re-runs the reveal. null outside a page that provides it or when the URL has no hash.
export interface SettingsSectionTarget {
  id: string;
  seq: string;
}

export const SettingsSectionTargetContext = createContext<SettingsSectionTarget | null>(null);

export function useSettingsSectionTarget(): SettingsSectionTarget | null {
  return useContext(SettingsSectionTargetContext);
}

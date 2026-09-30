import { createContext, useContext, useState, type ReactNode } from 'react';

// A bump-counter signal telling every CollapsibleSection under the provider to
// snap to `open`. `seq` increments on each Expand all / Collapse all click so a
// repeated click with the same `open` value (e.g. Expand all twice) still
// fires the effect. null outside the Settings page (or before any click)
// leaves CollapsibleSection behaving normally.
export interface SettingsBulkCollapseSignal {
  seq: number;
  open: boolean;
}

const SettingsBulkCollapseContext = createContext<SettingsBulkCollapseSignal | null>(null);

export function SettingsBulkCollapseProvider({
  value,
  children,
}: {
  value: SettingsBulkCollapseSignal | null;
  children: ReactNode;
}) {
  return (
    <SettingsBulkCollapseContext.Provider value={value}>
      {children}
    </SettingsBulkCollapseContext.Provider>
  );
}

export function useSettingsBulkCollapse(): SettingsBulkCollapseSignal | null {
  return useContext(SettingsBulkCollapseContext);
}

// Owns the signal for a page's Expand all / Collapse all controls.
export function useBulkCollapseSignal(): [SettingsBulkCollapseSignal | null, (open: boolean) => void] {
  const [signal, setSignal] = useState<SettingsBulkCollapseSignal | null>(null);
  const trigger = (open: boolean) => setSignal((prev) => ({ seq: (prev?.seq ?? 0) + 1, open }));
  return [signal, trigger];
}

import { useEffect, useRef, type ReactNode } from 'react';
import SectionSearchInput from './SectionSearchInput';
import SectionBulkControls from './SectionBulkControls';
import { SettingsSearchContext, SettingsSearchRegionContext } from '../context/SettingsSearchContext';
import { SettingsBulkCollapseProvider, useBulkCollapseSignal } from '../context/SettingsBulkCollapseContext';
import { useSectionSearch } from '../hooks/useSectionSearch';

interface SearchableSectionGroupProps {
  placeholder: string;
  ariaLabel: string;
  clearLabel: string;
  onMatchKeysChange?: (matches: Set<string> | null) => void;
  // Changing this value clears an active search, so a deep-linked section is not filtered out.
  revealKey?: string;
  children: ReactNode;
}

// Search box, Expand all / Collapse all and the empty state for the CollapsibleSections it wraps.
export default function SearchableSectionGroup({
  placeholder,
  ariaLabel,
  clearLabel,
  onMatchKeysChange,
  revealKey,
  children,
}: SearchableSectionGroupProps) {
  const regionRef = useRef<HTMLDivElement>(null);
  const { query, matchKeys, run, clear } = useSectionSearch(regionRef);
  useEffect(() => {
    onMatchKeysChange?.(matchKeys);
  }, [matchKeys, onMatchKeysChange]);
  useEffect(() => {
    if (revealKey) clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealKey]);
  const [bulkCollapseSignal, triggerBulkCollapse] = useBulkCollapseSignal();

  return (
    <>
      <SectionSearchInput
        value={query}
        onChange={run}
        onClear={clear}
        placeholder={placeholder}
        ariaLabel={ariaLabel}
        clearLabel={clearLabel}
      />

      <SectionBulkControls disabled={matchKeys !== null} onToggleAll={triggerBulkCollapse} />

      <SettingsBulkCollapseProvider value={bulkCollapseSignal}>
        <SettingsSearchContext.Provider value={matchKeys}>
          <SettingsSearchRegionContext.Provider value={true}>
            <div ref={regionRef} className="space-y-4">
              {matchKeys !== null && matchKeys.size === 0 && (
                <p className="text-sm text-muted-foreground px-1">
                  No settings match "{query.trim()}".
                </p>
              )}
              {children}
            </div>
          </SettingsSearchRegionContext.Provider>
        </SettingsSearchContext.Provider>
      </SettingsBulkCollapseProvider>
    </>
  );
}

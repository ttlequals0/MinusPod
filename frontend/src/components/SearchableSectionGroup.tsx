import { useRef, type ReactNode } from 'react';
import SectionSearchInput from './SectionSearchInput';
import SectionBulkControls from './SectionBulkControls';
import { SettingsSearchContext } from '../context/SettingsSearchContext';
import { SettingsBulkCollapseProvider, useBulkCollapseSignal } from '../context/SettingsBulkCollapseContext';
import { useSectionSearch } from '../hooks/useSectionSearch';

interface SearchableSectionGroupProps {
  placeholder: string;
  ariaLabel: string;
  clearLabel: string;
  children: ReactNode;
}

// Search box, Expand all / Collapse all and the empty state for the CollapsibleSections it wraps.
export default function SearchableSectionGroup({
  placeholder,
  ariaLabel,
  clearLabel,
  children,
}: SearchableSectionGroupProps) {
  const regionRef = useRef<HTMLDivElement>(null);
  const { query, matchKeys, run, clear } = useSectionSearch(regionRef);
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
          <div ref={regionRef} className="space-y-4">
            {matchKeys !== null && matchKeys.size === 0 && (
              <p className="text-sm text-muted-foreground px-1">
                No settings match "{query.trim()}".
              </p>
            )}
            {children}
          </div>
        </SettingsSearchContext.Provider>
      </SettingsBulkCollapseProvider>
    </>
  );
}

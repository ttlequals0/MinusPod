import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useLocalStorageState, readStoredValue } from '../hooks/useLocalStorageState';
import { useSettingsSearch } from '../context/SettingsSearchContext';
import { useSettingsBulkCollapse } from '../context/SettingsBulkCollapseContext';

// Mirror of a CollapsibleSection's persisted open state, for hosts that need
// to know whether their section is open (e.g. to gate a query on visibility)
// without owning it. Seeds from the same localStorage key the section writes,
// then tracks toggles: pass `storageKey` and wire the setter to `onToggle`.
// Lives here so knowledge of the storage-key contract stays next to the
// component that owns it.
export function useCollapsibleOpen(
  storageKey: string,
  defaultOpen = false,
): [boolean, (isOpen: boolean) => void] {
  const [open, setOpen] = useState(
    () => readStoredValue<boolean>(storageKey, defaultOpen) === true,
  );
  return [open, setOpen];
}

interface CollapsibleSectionProps {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
  headerRight?: ReactNode;
  storageKey?: string;
  onToggle?: (isOpen: boolean) => void;
  // When true, children are only mounted while the section is open. Use for
  // children that misbehave when rendered into a zero-size collapsed container
  // (e.g. recharts ResponsiveContainer logs width(-1)/height(-1)). Default
  // false keeps children mounted while collapsed, preserving their state.
  unmountWhenClosed?: boolean;
}

function CollapsibleSection({
  title,
  subtitle,
  defaultOpen = false,
  children,
  headerRight,
  storageKey,
  onToggle,
  unmountWhenClosed = false,
}: CollapsibleSectionProps) {
  const resolvedKey = storageKey || `settings-section-${title.toLowerCase().replace(/\s+/g, '-')}`;

  const [isOpen, setIsOpen] = useLocalStorageState<boolean>(resolvedKey, defaultOpen);

  const contentRef = useRef<HTMLDivElement>(null);
  const [maxHeight, setMaxHeight] = useState<string>(isOpen ? 'none' : '0px');

  // Settings search: the Settings page publishes the set of matching section
  // keys via context (null = no search); data-search-key on the card lets its
  // scan find this section. Inert outside Settings (default null).
  const matchKeys = useSettingsSearch();
  const searching = matchKeys !== null;
  const matchesSearch = searching && matchKeys.has(resolvedKey);
  const hiddenBySearch = searching && !matchesSearch;
  const expanded = searching ? matchesSearch : isOpen;
  let contentMaxHeight = maxHeight;
  if (searching) contentMaxHeight = matchesSearch ? 'none' : '0px';

  // Settings bulk expand/collapse: Expand all / Collapse all bump `seq` on
  // each click, telling every section to snap to `open`. Goes through the
  // same setIsOpen + onToggle path a manual click takes, so localStorage
  // persistence and host mirrors (useCollapsibleOpen) stay coherent. Ignored
  // during search, matching how manual toggles are ignored during search.
  // lastAppliedSeq seeds from the signal's seq at mount time so a section
  // that mounts after a click already happened (e.g. behind an async data
  // load) doesn't retroactively apply a stale signal -- only a seq that
  // changes AFTER mount is a real, freshly-clicked signal.
  const bulkSignal = useSettingsBulkCollapse();
  const lastAppliedSeq = useRef(bulkSignal?.seq);
  useEffect(() => {
    if (bulkSignal == null || searching) return;
    if (bulkSignal.seq === lastAppliedSeq.current) return;
    lastAppliedSeq.current = bulkSignal.seq;
    setIsOpen(bulkSignal.open);
    onToggle?.(bulkSignal.open);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkSignal?.seq]);

  useEffect(() => {
    if (isOpen) {
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
        const timer = setTimeout(() => setMaxHeight('none'), 300);
        return () => clearTimeout(timer);
      }
    } else {
      // Collapse: first set explicit height, then 0
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
        requestAnimationFrame(() => {
          setMaxHeight('0px');
        });
      }
    }
  }, [isOpen]);

  // Intentionally no dependency array: re-measures content height after every
  // render so dynamic child changes (e.g. conditional content, async loads)
  // are reflected in the animation. Cost is negligible (single DOM read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Skip while a search is active: a non-matching section is display:none, so
    // scrollHeight reads 0 and would clobber the stored height, collapsing the
    // section for a frame when the search clears.
    if (!searching && isOpen && maxHeight !== 'none') {
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
      }
    }
  });

  return (
    <div data-search-key={resolvedKey} className={`bg-card rounded-lg border border-border${hiddenBySearch ? ' hidden' : ''}`}>
      <button
        type="button"
        onClick={() => {
          // While searching, expansion follows the match, not isOpen, so a
          // toggle would silently flip the persisted state with no visible
          // effect. Ignore it until the search is cleared.
          if (searching) return;
          const next = !isOpen;
          setIsOpen(next);
          onToggle?.(next);
        }}
        className="w-full flex items-center justify-between p-4 sm:p-6 text-left"
      >
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          {subtitle && expanded && (
            <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2 ml-4 shrink-0">
          {headerRight && (
            <div onClick={(e) => e.stopPropagation()}>
              {headerRight}
            </div>
          )}
          <svg
            className={`w-5 h-5 text-muted-foreground transition-transform duration-200 ${
              expanded ? 'rotate-180' : ''
            }`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      <div
        ref={contentRef}
        style={{ maxHeight: contentMaxHeight }}
        className={`overflow-hidden ${!searching && maxHeight !== 'none' && maxHeight !== '0px' ? 'transition-[max-height] duration-300 ease-in-out' : ''}`}
      >
        <div className="px-4 pb-4 sm:px-6 sm:pb-6">
          {(!unmountWhenClosed || isOpen || matchesSearch) && children}
        </div>
      </div>
    </div>
  );
}

export default CollapsibleSection;

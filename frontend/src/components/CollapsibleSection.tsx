import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useLocalStorageState, readStoredValue } from '../hooks/useLocalStorageState';
import { useInSettingsSearchRegion, useSettingsSearch } from '../context/SettingsSearchContext';
import { useSettingsBulkCollapse } from '../context/SettingsBulkCollapseContext';
import ChevronCaret from './ChevronCaret';
import { focusRing } from './fieldStyles';
import { cardTitle, subHeading } from './pageStyles';

// Mirror of a CollapsibleSection's persisted open state, for hosts that gate work (e.g. a query) on it without
// owning it. Seeds from the section's localStorage key, then tracks toggles: pass `storageKey` and wire the
// setter to `onToggle`. Lives here to keep the storage-key contract next to its owner.
/** Search matches override saved openness when gating section queries. */
export function sectionVisible(matchKeys: Set<string> | null, storageKey: string, open: boolean): boolean {
  return matchKeys !== null ? matchKeys.has(storageKey) : open;
}

export function useSectionVisible(storageKey: string, open: boolean): boolean {
  return sectionVisible(useSettingsSearch(), storageKey, open);
}

export function useCollapsibleOpen(
  storageKey: string,
  defaultOpen = false,
): [boolean, (isOpen: boolean) => void] {
  const [open, setOpen] = useState(
    () => readStoredValue<boolean>(storageKey, defaultOpen) === true,
  );
  // Re-read a new key so reused feed panels restore that feed's saved state.
  const [prevKey, setPrevKey] = useState(storageKey);
  if (storageKey !== prevKey) {
    setPrevKey(storageKey);
    setOpen(readStoredValue<boolean>(storageKey, defaultOpen) === true);
  }
  return [open, setOpen];
}

interface CollapsibleSectionProps {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
  headerRight?: ReactNode;
  storageKey?: string;
  variant?: 'card' | 'subsection';
  onToggle?: (isOpen: boolean) => void;
  // When true, children mount only while open. For children that misbehave in a zero-size collapsed container
  // (e.g. recharts ResponsiveContainer logs width(-1)/height(-1)). Default false keeps children mounted and
  // their state preserved.
  unmountWhenClosed?: boolean;
  // Expands the section regardless of isOpen, without writing that to storage: for a host that needs it visible
  // on demand (e.g. a validation error inside) without clobbering the user's preference. Does not affect the
  // toggle click, which still flips the underlying isOpen.
  forceOpen?: boolean;
}

function CollapsibleSection({
  title,
  subtitle,
  defaultOpen = false,
  children,
  headerRight,
  storageKey,
  variant = 'card',
  onToggle,
  unmountWhenClosed = false,
  forceOpen = false,
}: CollapsibleSectionProps) {
  const subsection = variant === 'subsection';
  const resolvedKey = storageKey || `settings-section-${title.toLowerCase().replace(/\s+/g, '-')}`;

  const [isOpen, setIsOpen] = useLocalStorageState<boolean>(resolvedKey, defaultOpen);
  const openState = isOpen || forceOpen;

  const contentRef = useRef<HTMLDivElement>(null);
  const [maxHeight, setMaxHeight] = useState<string>(openState ? 'none' : '0px');

  // Matching section keys (data-search-key on the card), null when no search is
  // active or outside a page that provides the context.
  const matchKeys = useSettingsSearch();
  const searching = matchKeys !== null;
  // A searchable region keeps collapsed children mounted (hidden) so the search can read their text.
  const keepMounted = useInSettingsSearchRegion();
  const matchesSearch = searching && matchKeys.has(resolvedKey);
  const hiddenBySearch = searching && !matchesSearch;
  const expanded = searching ? matchesSearch : openState;
  let contentMaxHeight = maxHeight;
  if (searching) contentMaxHeight = matchesSearch ? 'none' : '0px';
  // 'none' is fully open, '0px' collapsed, a pixel height mid-animation.
  const animating = contentMaxHeight !== 'none' && contentMaxHeight !== '0px';

  // Settings bulk expand/collapse: each click bumps `seq`, snapping every section to `open` through the same
  // setIsOpen + onToggle path as a manual click. Ignored during search. lastAppliedSeq seeds from the signal's
  // seq at mount, so a late-mounting section ignores a stale signal.
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
    if (openState) {
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
  }, [openState]);

  // Intentionally no dependency array: re-measures content height after every
  // render so dynamic child changes (e.g. conditional content, async loads)
  // are reflected in the animation. Cost is negligible (single DOM read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Skip while a search is active: a non-matching section is display:none, so
    // scrollHeight reads 0 and would clobber the stored height, collapsing the
    // section for a frame when the search clears.
    if (!searching && openState && maxHeight !== 'none') {
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
      }
    }
  });

  return (
    <div
      data-search-key={resolvedKey}
      className={`${subsection ? 'border-t border-border first:border-t-0' : 'bg-card rounded-lg border border-border'}${hiddenBySearch ? ' hidden' : ''}`}
    >
      {/* The row div (not a wrapping button) carries the click target so a
          button passed via headerRight never nests inside a button. */}
      <div
        className={`w-full flex justify-between cursor-pointer ${subsection ? 'min-h-11 items-center sm:py-3' : 'items-start p-4 sm:p-6'}`}
        onClick={() => {
          // While searching, expansion follows the match, not isOpen, so a
          // toggle would silently flip the persisted state with no visible
          // effect. Ignore it until the search is cleared.
          if (searching) return;
          const next = !isOpen;
          setIsOpen(next);
          onToggle?.(next);
        }}
      >
        {/* No handler of its own: mouse and keyboard activation bubble to
            the row's single toggle handler above. */}
        <button
          type="button"
          aria-expanded={expanded}
          className={`flex-1 min-w-0 text-left ${subsection ? 'flex min-h-11 flex-col items-start justify-center sm:min-h-0' : ''} ${focusRing}`}
        >
          {subsection
            ? <h3 className={subHeading}>{title}</h3>
            : <h2 className={cardTitle}>{title}</h2>}
          {subtitle && expanded && (
            <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>
          )}
        </button>
        {/* h-7 pins the cluster to the title line, so the caret rotates in
            place instead of dropping when an expanded subtitle grows the
            header (#597). */}
        <div className="flex items-center gap-2 ml-4 shrink-0 h-7">
          {headerRight && (
            <div onClick={(e) => e.stopPropagation()}>
              {headerRight}
            </div>
          )}
          <ChevronCaret expanded={expanded} className="w-5 h-5" />
        </div>
      </div>

      {/* Clip only while collapsed or animating, so an open body does not cut off a focus ring. */}
      <div
        ref={contentRef}
        inert={!expanded}
        style={{ maxHeight: contentMaxHeight }}
        className={`${contentMaxHeight !== 'none' ? 'overflow-hidden' : ''} ${animating ? 'transition-[max-height] duration-300 ease-in-out' : ''}`}
      >
        <div className={subsection ? 'pb-4' : 'px-4 pb-4 sm:px-6 sm:pb-6'}>
          {(!unmountWhenClosed || keepMounted || openState || matchesSearch) && children}
        </div>
      </div>
    </div>
  );
}

export default CollapsibleSection;

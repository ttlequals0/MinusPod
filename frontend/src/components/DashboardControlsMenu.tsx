import { useEffect, useRef, useState } from 'react';
import { SlidersHorizontal, ChevronDown } from 'lucide-react';
import { useOutsideClick } from '../hooks/useOutsideClick';
import { btnSecondary } from './buttonStyles';
import { focusRing, selectBase } from './fieldStyles';
import type { FeedSortBy } from '../utils/feedSort';

// Below Tailwind's sm breakpoint the panel drops straight down, centered under
// the row like DropdownMenu, so it never hangs off to one side on a phone.
const PHONE_MAX_WIDTH_PX = 640;

interface DashboardControlsMenuProps {
  dashboardView: 'podcasts' | 'episodes';
  viewMode: 'grid' | 'list';
  onViewModeChange: (mode: 'grid' | 'list') => void;
  sortBy: FeedSortBy;
  onSortChange: (sort: FeedSortBy) => void;
  episodesPerPodcast: number;
  onEpisodesPerPodcastChange: (n: number) => void;
  perPodcastMin: number;
  perPodcastMax: number;
}

const segClass = (active: boolean) =>
  `inline-flex items-center justify-center min-w-11 h-11 px-3 text-sm transition-colors ${
    active ? 'bg-primary text-primary-foreground' : btnSecondary
  } ${focusRing}`;

// Layout, sort, and per-podcast collapse into one popover so the dashboard
// toolbar stays a single row on a phone instead of wrapping or clipping.
function DashboardControlsMenu({
  dashboardView, viewMode, onViewModeChange, sortBy, onSortChange,
  episodesPerPodcast, onEpisodesPerPodcastChange, perPodcastMin, perPodcastMax,
}: DashboardControlsMenuProps) {
  const [open, setOpen] = useState(false);
  const [phoneTop, setPhoneTop] = useState<number | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useOutsideClick(rootRef, open, () => setOpen(false));

  const close = () => { setOpen(false); triggerRef.current?.focus(); };

  const toggle = () => {
    if (!open) {
      const rect = rootRef.current?.getBoundingClientRect();
      setPhoneTop(rect && window.innerWidth < PHONE_MAX_WIDTH_PX ? rect.bottom + 4 : null);
    }
    setOpen((o) => !o);
  };

  // Escape closes and returns focus to the trigger, matching DropdownMenu.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="relative shrink-0" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        className={`h-11 min-w-11 px-2.5 sm:px-4 text-sm rounded ${btnSecondary} transition-colors inline-flex items-center justify-center gap-2 whitespace-nowrap ${focusRing}`}
        aria-haspopup="true"
        aria-expanded={open}
        title="Layout and sort"
        aria-label="Layout and sort"
      >
        <SlidersHorizontal className="w-5 h-5 sm:hidden" />
        <span className="hidden sm:inline">View</span>
        <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          role="group"
          aria-label="Layout and sort"
          style={phoneTop !== null ? { top: phoneTop } : undefined}
          className={`${phoneTop !== null ? 'fixed left-1/2 -translate-x-1/2' : 'absolute right-0 mt-1'} w-64 max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg z-10 p-3 space-y-3`}
        >
          {dashboardView === 'podcasts' && (
            <div className="space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">Layout</span>
              <div className="flex h-11 border border-border rounded overflow-hidden w-full">
                <button onClick={() => onViewModeChange('grid')} className={`${segClass(viewMode === 'grid')} flex-1`} aria-label="Grid view" title="Grid view">Grid</button>
                <button onClick={() => onViewModeChange('list')} className={`${segClass(viewMode === 'list')} flex-1`} aria-label="List view" title="List view">List</button>
              </div>
            </div>
          )}
          <div className="space-y-1.5">
            <span className="block text-xs font-medium text-muted-foreground">Sort</span>
            <div className="flex h-11 border border-border rounded overflow-hidden w-full">
              <button onClick={() => onSortChange('recent')} className={`${segClass(sortBy === 'recent')} flex-1`} aria-label="Sort by recent" title="Sort by most recent episode">Recent</button>
              <button onClick={() => onSortChange('title')} className={`${segClass(sortBy === 'title')} flex-1`} aria-label="Sort by title" title="Sort alphabetically">Title</button>
            </div>
          </div>
          {dashboardView === 'episodes' && (
            <label className="block space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">Episodes per podcast</span>
              <select
                aria-label="Episodes per podcast"
                value={episodesPerPodcast}
                onChange={(e) => onEpisodesPerPodcastChange(Number(e.target.value))}
                className={`${selectBase} h-11 w-full`}
              >
                {Array.from({ length: perPodcastMax - perPodcastMin + 1 }, (_, i) => perPodcastMin + i).map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
    </div>
  );
}

export default DashboardControlsMenu;

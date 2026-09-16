import { useRef, useState } from 'react';
import { SlidersHorizontal, ChevronDown } from 'lucide-react';
import { usePopoverTabs } from '../hooks/usePopover';
import Popover from './Popover';
import SegmentedToggle from './SegmentedToggle';
import { btnSecondary } from './buttonStyles';
import { focusRing, selectBase } from './fieldStyles';
import type { FeedSortBy } from '../utils/feedSort';

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

const LAYOUT_OPTIONS = [
  { value: 'grid' as const, label: 'Grid', title: 'Grid view' },
  { value: 'list' as const, label: 'List', title: 'List view' },
];
const SORT_OPTIONS = [
  { value: 'recent' as const, label: 'Recent', ariaLabel: 'Sort by recent', title: 'Sort by most recent episode' },
  { value: 'title' as const, label: 'Title', ariaLabel: 'Sort by title', title: 'Sort alphabetically' },
];

// Layout, sort, and per-podcast collapse into one popover so the dashboard
// toolbar stays a single row on a phone instead of wrapping or clipping.
function DashboardControlsMenu({
  dashboardView, viewMode, onViewModeChange, sortBy, onSortChange,
  episodesPerPodcast, onEpisodesPerPodcastChange, perPodcastMin, perPodcastMax,
}: DashboardControlsMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { triggerProps, popoverProps } = usePopoverTabs({ open, setOpen, triggerRef });

  return (
    <div className="shrink-0" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        {...triggerProps}
        onClick={() => setOpen((o) => !o)}
        className={`h-11 min-w-11 px-2.5 sm:px-4 text-sm rounded ${btnSecondary} transition-colors inline-flex items-center justify-center gap-2 whitespace-nowrap ${focusRing}`}
        title="Layout and sort"
        aria-label="View options"
      >
        <SlidersHorizontal className="w-5 h-5 sm:hidden" />
        <span className="hidden sm:inline">View</span>
        <ChevronDown className={`w-4 h-4 hidden sm:block transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <Popover
        open={open}
        anchorRef={rootRef}
        align="right"
        role="group"
        aria-label="Layout and sort"
        className="w-64 p-3 space-y-3"
        {...popoverProps}
      >
        {open && (
          <>
            {dashboardView === 'podcasts' && (
              <div className="space-y-1.5">
                <span className="block text-xs font-medium text-muted-foreground">Layout</span>
                <SegmentedToggle
                  options={LAYOUT_OPTIONS}
                  value={viewMode}
                  onChange={onViewModeChange}
                  ariaLabel="Layout"
                  variant="toolbar"
                  fill
                />
              </div>
            )}
            <div className="space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">Sort</span>
              <SegmentedToggle
                options={SORT_OPTIONS}
                value={sortBy}
                onChange={onSortChange}
                ariaLabel="Sort"
                variant="toolbar"
                fill
              />
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
          </>
        )}
      </Popover>
    </div>
  );
}

export default DashboardControlsMenu;

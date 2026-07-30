import { ChevronDown, ChevronUp } from 'lucide-react';
import type { DetectionSort, DetectionStatusFilter } from '../../api/detections';
import { SEGMENT_CATEGORY_FILTER_OPTIONS } from '../../utils/segmentCategory';

const SORT_OPTIONS: Array<[DetectionSort, string]> = [
  ['date', 'Published'],
  ['confidence', 'Confidence'],
  ['podcast', 'Podcast'],
];

const SELECT_CLASS =
  'flex-1 sm:flex-none min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded';

interface FeedOption {
  slug: string;
  title: string;
}

interface StatusConfig {
  value: DetectionStatusFilter;
  onChange: (next: DetectionStatusFilter) => void;
  options: Array<[DetectionStatusFilter, string]>;
}

interface Props {
  // Distinguishes the two tabs' label/control pairs when both are mounted.
  idPrefix: string;
  feeds: FeedOption[] | undefined;
  feed: string;
  onFeedChange: (next: string) => void;
  category: string;
  onCategoryChange: (next: string) => void;
  q: string;
  onQChange: (next: string) => void;
  sort: DetectionSort;
  onSortChange: (next: DetectionSort) => void;
  order: 'asc' | 'desc';
  onOrderChange: (next: 'asc' | 'desc') => void;
  // Ad Review filters by review state; Detected Ads is already scoped to cut
  // ads, so it passes nothing and the select is omitted.
  status?: StatusConfig;
}

// Shared filter bar for the Ad Review and Detected Ads tabs. The two were
// byte-identical apart from the status select, and users move between the tabs
// constantly, so the layout is worth keeping in one place.
export function DetectionFilterBar({
  idPrefix, feeds, feed, onFeedChange, category, onCategoryChange,
  q, onQChange, sort, onSortChange, order, onOrderChange, status,
}: Props) {
  return (
    <div className="bg-card rounded-lg border border-border p-4 mb-6 flex flex-wrap gap-4 items-center">
      {status && (
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor={`${idPrefix}-status`} className="text-sm text-muted-foreground shrink-0">Status</label>
          <select
            id={`${idPrefix}-status`}
            value={status.value}
            onChange={(e) => status.onChange(e.target.value as DetectionStatusFilter)}
            className={SELECT_CLASS}
          >
            {status.options.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      )}
      <div className="flex items-center gap-2 w-full sm:w-auto min-w-0">
        <label htmlFor={`${idPrefix}-feed`} className="text-sm text-muted-foreground shrink-0">Podcast</label>
        <select
          id={`${idPrefix}-feed`}
          value={feed}
          onChange={(e) => onFeedChange(e.target.value)}
          className="flex-1 sm:flex-none min-w-0 max-w-full sm:max-w-72 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
        >
          <option value="">All podcasts</option>
          {feeds?.map((f) => (
            <option key={f.slug} value={f.slug}>{f.title}</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-2 w-full sm:w-auto">
        <label htmlFor={`${idPrefix}-category`} className="text-sm text-muted-foreground shrink-0">Category</label>
        <select
          id={`${idPrefix}-category`}
          value={category}
          onChange={(e) => onCategoryChange(e.target.value)}
          className={SELECT_CLASS}
        >
          {SEGMENT_CATEGORY_FILTER_OPTIONS.map(([value, label]) => (
            <option key={value || 'all'} value={value}>{label}</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-2 w-full sm:flex-1 sm:min-w-[200px]">
        <label htmlFor={`${idPrefix}-q`} className="text-sm text-muted-foreground shrink-0">Search</label>
        <input
          id={`${idPrefix}-q`}
          type="text"
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          placeholder="Sponsor or reason"
          className="w-full min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
        />
      </div>
      {/* Neither the rows nor the cards have sortable headers, so sorting
          lives in the filter bar at every width. */}
      <div className="flex items-center gap-2 w-full sm:w-auto">
        <label htmlFor={`${idPrefix}-sort`} className="text-sm text-muted-foreground shrink-0">Sort</label>
        <select
          id={`${idPrefix}-sort`}
          value={sort}
          onChange={(e) => onSortChange(e.target.value as DetectionSort)}
          className={SELECT_CLASS}
        >
          {SORT_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => onOrderChange(order === 'desc' ? 'asc' : 'desc')}
          aria-label={order === 'desc' ? 'Switch to ascending order' : 'Switch to descending order'}
          className="px-3 py-1.5 bg-secondary border border-border rounded text-muted-foreground"
        >
          {order === 'desc'
            ? <ChevronDown className="w-4 h-4" aria-hidden />
            : <ChevronUp className="w-4 h-4" aria-hidden />}
        </button>
      </div>
    </div>
  );
}

export default DetectionFilterBar;

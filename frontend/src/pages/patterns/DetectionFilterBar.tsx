import { ChevronDown, ChevronUp } from 'lucide-react';
import type {
  DetectionReviewerFilter, DetectionSort, DetectionStatusFilter,
} from '../../api/detections';
import { SEGMENT_CATEGORY_FILTER_OPTIONS } from '../../utils/segmentCategory';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { inputBase, selectBase, filterLabel, filterControl } from '../../components/fieldStyles';

const SORT_OPTIONS: Array<[DetectionSort, string]> = [
  ['date', 'Published'],
  ['confidence', 'Confidence'],
  ['podcast', 'Podcast'],
];

const REVIEWER_OPTIONS: Array<[DetectionReviewerFilter, string]> = [
  ['', 'Any'],
  ['adjusted', 'Adjusted'],
  ['unadjusted', 'Not adjusted'],
];

const SELECT_CLASS = `${filterControl} ${selectBase}`;

interface FeedOption {
  slug: string;
  title: string;
}

interface StatusConfig {
  value: DetectionStatusFilter;
  onChange: (next: DetectionStatusFilter) => void;
  options: Array<[DetectionStatusFilter, string]>;
}

interface HoldReasonConfig {
  value: string;
  onChange: (next: string) => void;
  options: Array<[string, string]>;
}

interface Props {
  // Distinguishes the two tabs' label/control pairs when both are mounted.
  idPrefix: string;
  feeds: FeedOption[] | undefined;
  feed: string;
  onFeedChange: (next: string) => void;
  category: string;
  onCategoryChange: (next: string) => void;
  reviewer: DetectionReviewerFilter;
  onReviewerChange: (next: DetectionReviewerFilter) => void;
  q: string;
  onQChange: (next: string) => void;
  sort: DetectionSort;
  onSortChange: (next: DetectionSort) => void;
  order: 'asc' | 'desc';
  onOrderChange: (next: 'asc' | 'desc') => void;
  // Ad review filters by review state; Detected Ads is already scoped to cut
  // ads, so it passes nothing and the select is omitted.
  status?: StatusConfig;
  // Only meaningful while the status filter selects pending holds.
  holdReason?: HoldReasonConfig;
}

// Shared filter bar for the Ad review and Detected ads tabs. The two were
// byte-identical apart from the status select, and users move between the tabs
// constantly, so the layout is worth keeping in one place.
export function DetectionFilterBar({
  idPrefix, feeds, feed, onFeedChange, category, onCategoryChange,
  reviewer, onReviewerChange, q, onQChange, sort, onSortChange, order, onOrderChange, status,
  holdReason,
}: Props) {
  return (
    <div className="bg-card rounded-lg border border-border p-4 mb-6 grid grid-cols-1 min-[375px]:grid-cols-2 lg:grid-cols-4 gap-4 items-end">
      {status && (
        <div className="min-w-0">
          <label htmlFor={`${idPrefix}-status`} className={filterLabel}>Status</label>
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
      {holdReason && (
        <div className="min-w-0">
          <label htmlFor={`${idPrefix}-hold-reason`} className={filterLabel}>Hold reason</label>
          <select
            id={`${idPrefix}-hold-reason`}
            value={holdReason.value}
            onChange={(e) => holdReason.onChange(e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">Any reason</option>
            {holdReason.options.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      )}
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-feed`} className={filterLabel}>Podcast</label>
        <select
          id={`${idPrefix}-feed`}
          value={feed}
          onChange={(e) => onFeedChange(e.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">All podcasts</option>
          {feeds?.map((f) => (
            <option key={f.slug} value={f.slug}>{f.title}</option>
          ))}
        </select>
      </div>
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-category`} className={filterLabel}>Category</label>
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
      <div className="min-w-0">
        <label htmlFor={`${idPrefix}-reviewer`} className={filterLabel}>Reviewer</label>
        <select
          id={`${idPrefix}-reviewer`}
          value={reviewer}
          onChange={(e) => onReviewerChange(e.target.value as DetectionReviewerFilter)}
          className={SELECT_CLASS}
        >
          {REVIEWER_OPTIONS.map(([value, label]) => (
            <option key={value || 'any'} value={value}>{label}</option>
          ))}
        </select>
      </div>
      {/* Neither the rows nor the cards have sortable headers, so sorting
          lives in the filter bar at every width. */}
      <div className="min-w-0 min-[375px]:col-span-2 lg:col-span-1">
        <label htmlFor={`${idPrefix}-sort`} className={filterLabel}>Sort</label>
        <div className="flex min-w-0 items-center gap-1 sm:gap-2">
          <select
            id={`${idPrefix}-sort`}
            value={sort}
            onChange={(e) => onSortChange(e.target.value as DetectionSort)}
            className={`flex-1 ${SELECT_CLASS}`}
          >
            {SORT_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => onOrderChange(order === 'desc' ? 'asc' : 'desc')}
            aria-label={order === 'desc' ? 'Switch to ascending order' : 'Switch to descending order'}
            className={`${btnBase} ${btnSecondary} shrink-0`}
          >
            {order === 'desc'
              ? <ChevronDown className="w-4 h-4" aria-hidden />
              : <ChevronUp className="w-4 h-4" aria-hidden />}
          </button>
        </div>
      </div>
      <div className="min-w-0 min-[375px]:col-span-2 lg:col-span-4">
        <label htmlFor={`${idPrefix}-q`} className={filterLabel}>Search</label>
        <input
          id={`${idPrefix}-q`}
          type="text"
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          placeholder="Sponsor or reason"
          className={`${filterControl} ${inputBase}`}
        />
      </div>
    </div>
  );
}

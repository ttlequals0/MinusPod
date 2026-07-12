import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  getDetections,
  type DetectionSort,
  type DetectionStatusFilter,
  type ReviewDetection,
} from '../../api/detections';
import { getFeeds } from '../../api/feeds';
import { Pagination } from '../../components/Pagination';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatTimestamp, formatDate } from '../../utils/format';

const STATUS_OPTIONS: Array<[DetectionStatusFilter, string]> = [
  ['needs_review', 'Needs review'],
  ['pending', 'Pending review'],
  ['rejected', 'Rejected'],
  ['accepted', 'Accepted'],
  ['all', 'All'],
];

const STATUS_BADGE: Record<ReviewDetection['status'], [string, string]> = {
  accepted: ['Accepted', 'bg-green-500/10 text-green-600 dark:text-green-400'],
  rejected: ['Rejected', 'bg-red-500/10 text-red-600 dark:text-red-400'],
  pending: ['Pending review', 'bg-amber-500/10 text-amber-600 dark:text-amber-400'],
};

const RESOLUTION_BADGE: Record<ReviewDetection['resolution'], [string, string]> = {
  unresolved: ['Unresolved', 'bg-secondary text-muted-foreground'],
  confirmed: ['Confirmed', 'bg-green-500/10 text-green-600 dark:text-green-400'],
  dismissed: ['Dismissed', 'bg-secondary text-muted-foreground'],
};

export default function AdReviewTab() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<DetectionStatusFilter>('needs_review');
  const [feed, setFeed] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<DetectionSort>('date');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', page, status, feed, q, sort, order],
    queryFn: () => getDetections({
      page,
      status,
      feed: feed || undefined,
      q: q || undefined,
      sort,
      order,
    }),
  });

  const { data: feeds } = useQuery({ queryKey: ['feeds'], queryFn: getFeeds });

  const sortHeader = (key: DetectionSort, label: string) => (
    <button
      type="button"
      onClick={() => {
        if (sort === key) {
          setOrder(order === 'desc' ? 'asc' : 'desc');
        } else {
          setSort(key);
          setOrder('desc');
        }
        setPage(1);
      }}
      className="flex items-center gap-1 font-medium hover:text-foreground"
    >
      {label}
      {sort === key && (
        order === 'desc'
          ? <ChevronDown className="w-3.5 h-3.5" aria-hidden />
          : <ChevronUp className="w-3.5 h-3.5" aria-hidden />
      )}
    </button>
  );

  const th = 'px-3 py-2 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider whitespace-nowrap';
  const td = 'px-3 py-2 text-sm text-muted-foreground whitespace-nowrap';

  return (
    <div>
      <div className="bg-card rounded-lg border border-border p-4 mb-6 flex flex-wrap gap-4 items-center">
        <div className="flex items-center gap-2">
          <label htmlFor="ad-review-status" className="text-sm text-muted-foreground">Status</label>
          <select
            id="ad-review-status"
            value={status}
            onChange={(e) => { setStatus(e.target.value as DetectionStatusFilter); setPage(1); }}
            className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="ad-review-feed" className="text-sm text-muted-foreground">Podcast</label>
          <select
            id="ad-review-feed"
            value={feed}
            onChange={(e) => { setFeed(e.target.value); setPage(1); }}
            className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            <option value="">All podcasts</option>
            {feeds?.map((f) => (
              <option key={f.slug} value={f.slug}>{f.title}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <label htmlFor="ad-review-q" className="text-sm text-muted-foreground">Search</label>
          <input
            id="ad-review-q"
            type="text"
            value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1); }}
            placeholder="Sponsor or reason"
            className="w-full px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          />
        </div>
      </div>

      {isLoading && <LoadingSpinner className="py-12" />}
      {error && (
        <div className="text-destructive text-sm">
          Failed to load detections.
        </div>
      )}
      {!isLoading && !error && data && data.total === 0 && (
        <div className="text-muted-foreground text-sm py-8 text-center">
          No detections need review.
        </div>
      )}
      {!isLoading && !error && data && data.total > 0 && (
        <>
          <div className="overflow-x-auto bg-card rounded-lg border border-border">
            <table className="w-full divide-y divide-border">
              <thead className="bg-muted/50">
                <tr>
                  <th className={th}>{sortHeader('podcast', 'Podcast')}</th>
                  <th className={th}>Episode</th>
                  <th className={th}>{sortHeader('date', 'Published')}</th>
                  <th className={th}>Time</th>
                  <th className={th}>Sponsor</th>
                  <th className={th}>{sortHeader('confidence', 'Confidence')}</th>
                  <th className={th}>Stage</th>
                  <th className={th}>Status</th>
                  <th className={th}>Resolution</th>
                  <th className={th}>Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.detections.map((d) => (
                  <tr key={`${d.feedSlug}-${d.episodeId}-${d.start}-${d.end}`} className="hover:bg-accent/50 transition-colors">
                    <td className={td}>{d.feedTitle}</td>
                    <td className="px-3 py-2 text-sm">
                      <Link to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`} className="text-primary hover:underline">
                        {d.episodeTitle}
                      </Link>
                    </td>
                    <td className={td}>{formatDate(d.publishDate)}</td>
                    <td className={td}>
                      {formatTimestamp(d.start)} - {formatTimestamp(d.end)} ({Math.round(d.end - d.start)}s)
                    </td>
                    <td className="px-3 py-2 text-sm text-foreground">{d.sponsor || '-'}</td>
                    <td className={td}>{d.confidence != null ? d.confidence.toFixed(2) : '-'}</td>
                    <td className={td}>{d.detectionStage || '-'}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className={`px-2 py-0.5 rounded text-xs ${STATUS_BADGE[d.status][1]}`}>
                        {STATUS_BADGE[d.status][0]}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className={`px-2 py-0.5 rounded text-xs ${RESOLUTION_BADGE[d.resolution][1]}`}>
                        {RESOLUTION_BADGE[d.resolution][0]}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" data-testid="row-actions" />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={data.page} totalPages={data.totalPages} total={data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}

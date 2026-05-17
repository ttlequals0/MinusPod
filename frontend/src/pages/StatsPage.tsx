import { useState, useMemo, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import { getDashboardStats, getStatsByDay, getStatsByPodcast, getReviewerStats } from '../api/stats';
import { getFeeds } from '../api/feeds';
import { formatTokenCount } from './settings/settingsUtils';
import LoadingSpinner from '../components/LoadingSpinner';

function useThemeColors() {
  const [colors, setColors] = useState({ primary: '', card: '', border: '', foreground: '', muted: '' });
  useEffect(() => {
    function resolve(name: string) {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : '';
    }
    function update() {
      const next = {
        primary: resolve('--primary'),
        card: resolve('--card'),
        border: resolve('--border'),
        foreground: resolve('--card-foreground'),
        muted: resolve('--muted-foreground'),
      };
      setColors(prev =>
        prev.primary === next.primary && prev.card === next.card && prev.border === next.border
        && prev.foreground === next.foreground && prev.muted === next.muted
          ? prev : next
      );
    }
    update();
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
    return () => obs.disconnect();
  }, []);
  return colors;
}

type PodcastSortField = 'podcastTitle' | 'episodeCount' | 'totalAds' | 'avgAds' | 'avgTimeSavedSeconds' | 'avgEpisodeLengthSeconds' | 'totalCost' | 'avgTokensPerEpisode';

interface SortThProps {
  field: PodcastSortField;
  label: string;
  align?: 'left' | 'right';
  className?: string;
}

/**
 * Build a SortTh component bound to the current sort state. Callers render
 * <SortTh field=... label=... /> per column without repeating
 * sortField/sortDir/onSort on every instance (8x in the desktop table).
 */
function makeSortTh(
  sortField: PodcastSortField,
  sortDir: 'asc' | 'desc',
  onSort: (f: PodcastSortField) => void,
) {
  return function SortTh({ field, label, align = 'right', className = '' }: SortThProps) {
    return (
      <th
        className={`px-4 py-3 text-${align} text-xs font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:bg-accent/50 ${className}`}
        onClick={() => onSort(field)}
      >
        <div className={`flex items-center gap-1 ${align === 'right' ? 'justify-end' : ''}`}>
          {label}
          {sortField === field && (
            <span className="text-foreground">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>
          )}
        </div>
      </th>
    );
  };
}

function ReviewerStatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-secondary/50 rounded-md p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatCost(cost: number): string {
  return `$${cost.toFixed(4)}`;
}

function generateChartColors(primary: string, count: number): string[] {
  if (!primary) return Array(count).fill('#6366f1');
  const match = primary.match(/hsl\((\d[\d.]*)\s*[ ,]\s*(\d[\d.]*)%?\s*[ ,]\s*(\d[\d.]*)%?\)/);
  if (!match) return Array(count).fill(primary);
  const hue = parseFloat(match[1]);
  const sat = parseFloat(match[2]);
  return Array.from({ length: count }, (_, i) => {
    const h = (hue + i * 20) % 360;
    const l = 50 + (i % 4) * 8;
    return `hsl(${h}, ${Math.max(sat, 55)}%, ${l}%)`;
  });
}

export default function StatsPage() {
  const theme = useThemeColors();
  const [podcastFilter, setPodcastFilter] = useState('');

  const tooltipStyle = useMemo(() => ({
    contentStyle: { backgroundColor: theme.card || '#1a1a2e', border: `1px solid ${theme.border || '#333'}`, color: theme.foreground || '#fff' },
    labelStyle: { color: theme.foreground || '#fff' },
  }), [theme.card, theme.border, theme.foreground]);

  const { data: feeds } = useQuery({
    queryKey: ['feeds'],
    queryFn: getFeeds,
  });

  const { data: dashboard, isLoading: dashLoading } = useQuery({
    queryKey: ['stats-dashboard', podcastFilter],
    queryFn: () => getDashboardStats(podcastFilter || undefined),
  });

  const { data: byDay, isLoading: dayLoading } = useQuery({
    queryKey: ['stats-by-day', podcastFilter],
    queryFn: () => getStatsByDay(podcastFilter || undefined),
  });

  // Defer until the dashboard query confirms there are reviews to summarize.
  // Avoids a wasted round-trip on every Stats page mount when the reviewer
  // (off by default) has never run.
  const { data: reviewer } = useQuery({
    queryKey: ['stats', 'reviewer', podcastFilter],
    queryFn: () => getReviewerStats(podcastFilter || undefined),
    enabled: !!dashboard,
  });

  const { data: byPodcast, isLoading: podLoading } = useQuery({
    queryKey: ['stats-by-podcast'],
    queryFn: getStatsByPodcast,
  });

  const [sortField, setSortField] = useState<PodcastSortField>('totalAds');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const handleSort = (field: PodcastSortField) => {
    if (sortField === field) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const SortTh = useMemo(
    () => makeSortTh(sortField, sortDir, handleSort),
    // handleSort closes over sortField/sortDir so re-binding when either
    // changes is enough; eslint can't infer the indirection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortField, sortDir]
  );

  const sortedPodcasts = useMemo(() => {
    if (!byPodcast?.podcasts) return [];
    return [...byPodcast.podcasts].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];
      if (typeof aVal === 'string') return sortDir === 'asc' ? aVal.localeCompare(bVal as string) : (bVal as string).localeCompare(aVal);
      return sortDir === 'asc' ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
    });
  }, [byPodcast, sortField, sortDir]);

  const topPodcasts = useMemo(() => {
    if (!byPodcast?.podcasts) return [];
    return byPodcast.podcasts.slice(0, 10);
  }, [byPodcast]);

  const chartColors = useMemo(
    () => generateChartColors(theme.primary, topPodcasts.length),
    [theme.primary, topPodcasts.length]
  );

  if (dashLoading && dayLoading && podLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Stats</h1>
        <select
          value={podcastFilter}
          onChange={(e) => setPodcastFilter(e.target.value)}
          className="w-full sm:w-auto px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm"
        >
          <option value="">All Podcasts</option>
          {feeds?.map((feed) => (
            <option key={feed.slug} value={feed.slug}>
              {feed.title}
            </option>
          ))}
        </select>
      </div>

      {/* Summary Cards */}
      {dashboard && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
          <StatCard
            label="Avg Time Saved"
            value={formatDuration(dashboard.avgTimeSavedSeconds)}
            min={formatDuration(dashboard.minTimeSavedSeconds)}
            max={formatDuration(dashboard.maxTimeSavedSeconds)}
          />
          <StatCard
            label="Avg Ads Removed"
            value={dashboard.avgAdsRemoved.toFixed(1)}
            min={String(dashboard.minAdsRemoved)}
            max={String(dashboard.maxAdsRemoved)}
          />
          <StatCard
            label="Avg Cost"
            value={formatCost(dashboard.avgCostPerEpisode)}
            min={formatCost(dashboard.minCostPerEpisode)}
            max={formatCost(dashboard.maxCostPerEpisode)}
          />
          <StatCard
            label="Avg Processing Time"
            value={formatDuration(dashboard.avgProcessingTimeSeconds)}
            min={formatDuration(dashboard.minProcessingTimeSeconds)}
            max={formatDuration(dashboard.maxProcessingTimeSeconds)}
          />
          <StatCard
            label="Avg Episode Length"
            value={formatDuration(dashboard.avgEpisodeLengthSeconds)}
            min={formatDuration(dashboard.minEpisodeLengthSeconds)}
            max={formatDuration(dashboard.maxEpisodeLengthSeconds)}
          />
          <StatCard
            label="Avg Tokens/Episode"
            value={formatTokenCount(dashboard.avgInputTokens + dashboard.avgOutputTokens)}
            min={`In: ${formatTokenCount(dashboard.avgInputTokens)}`}
            max={`Out: ${formatTokenCount(dashboard.avgOutputTokens)}`}
          />
        </div>
      )}

      {/* Totals Row */}
      {dashboard && (
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Episodes</p>
            <p className="text-xl font-bold text-foreground">{dashboard.totalEpisodesProcessed}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Ads Removed</p>
            <p className="text-xl font-bold text-foreground">{dashboard.totalAdsRemoved}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Time Saved</p>
            <p className="text-xl font-bold text-foreground">{formatDuration(dashboard.totalTimeSavedSeconds)}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total LLM Cost</p>
            <p className="text-xl font-bold text-foreground">{formatCost(dashboard.totalLlmCost)}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Tokens</p>
            <p className="text-xl font-bold text-foreground">{formatTokenCount(dashboard.totalInputTokens + dashboard.totalOutputTokens)}</p>
            <p className="text-xs text-muted-foreground mt-1">In: {formatTokenCount(dashboard.totalInputTokens)} / Out: {formatTokenCount(dashboard.totalOutputTokens)}</p>
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Top Podcasts by Ads */}
        {topPodcasts.length > 0 && (
          <div className="bg-card rounded-lg border border-border p-4">
            <h2 className="text-lg font-semibold text-foreground mb-4">Top Podcasts by Ads Removed</h2>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={topPodcasts} layout="vertical" margin={{ left: 20, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.border || '#333'} />
                <XAxis type="number" tick={{ fill: theme.foreground || '#fff', fontSize: 12 }} />
                <YAxis
                  dataKey="podcastTitle"
                  type="category"
                  width={130}
                  tick={{ fill: theme.foreground || '#fff', fontSize: 12 }}
                  tickFormatter={(v: string) => v.length > 18 ? v.slice(0, 16) + '..' : v}
                />
                <Tooltip {...tooltipStyle} />
                <Bar dataKey="totalAds" name="Total Ads" radius={[0, 4, 4, 0]}>
                  {topPodcasts.map((_, i) => (
                    <Cell key={i} fill={chartColors[i]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Episodes by Day of Week */}
        {byDay?.days && (
          <div className="bg-card rounded-lg border border-border p-4">
            <h2 className="text-lg font-semibold text-foreground mb-4">Episodes Processed by Day</h2>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={byDay.days} margin={{ left: 0, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.border || '#333'} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: theme.foreground || '#fff', fontSize: 12 }}
                  tickFormatter={(v: string) => v.slice(0, 3)}
                />
                <YAxis tick={{ fill: theme.foreground || '#fff', fontSize: 12 }} />
                <Tooltip {...tooltipStyle} />
                <Bar dataKey="count" name="Episodes" fill={theme.primary || '#6366f1'} fillOpacity={0.85} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Ad Reviewer Stats. Renders whenever the query has loaded; all-zero
          counts are the visible signal that the reviewer is configured but
          has not yet run on any episode. */}
      {reviewer && (
        <div className="bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
          <h2 className="text-lg font-semibold text-foreground mb-4">Ad Reviewer Stats</h2>
          {reviewer.totalReviews === 0 && (
            <p className="text-sm text-muted-foreground mb-4">
              No reviews yet. Enable Ad Reviewer in Settings, Experiments section, and reprocess an episode to see stats here.
            </p>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
            <ReviewerStatCard label="Total reviews" value={reviewer.totalReviews} />
            <ReviewerStatCard label="Confirmed" value={reviewer.verdictCounts.confirmed} />
            <ReviewerStatCard label="Adjusted" value={reviewer.verdictCounts.adjust} />
            <ReviewerStatCard label="Rejected" value={reviewer.verdictCounts.reject} />
            <ReviewerStatCard label="Resurrected" value={reviewer.verdictCounts.resurrect} />
            <ReviewerStatCard label="Failed" value={reviewer.verdictCounts.failure} />
            <ReviewerStatCard label="Pass 1 adjusts" value={reviewer.pass1AdjustmentCount} />
            <ReviewerStatCard label="Pass 2 adjusts" value={reviewer.pass2AdjustmentCount} />
            <ReviewerStatCard label="Avg shift" value={`${reviewer.avgBoundaryShiftSeconds}s`} />
          </div>
        </div>
      )}

      {/* Podcast Stats Table */}
      {/* Mobile Card Layout */}
      {sortedPodcasts.length > 0 && (
        <div className="sm:hidden space-y-3">
          <h2 className="text-lg font-semibold text-foreground">All Podcasts</h2>
          {sortedPodcasts.map((p) => (
            <div key={p.podcastSlug} className="bg-card rounded-lg border border-border p-4">
              <p className="text-sm font-medium text-foreground mb-2">{p.podcastTitle}</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <span className="text-muted-foreground">Episodes</span>
                <span className="text-foreground text-right">{p.episodeCount}</span>
                <span className="text-muted-foreground">Total Ads</span>
                <span className="text-foreground text-right">{p.totalAds}</span>
                <span className="text-muted-foreground">Avg Ads</span>
                <span className="text-foreground text-right">{p.avgAds}</span>
                <span className="text-muted-foreground">Avg Time Saved</span>
                <span className="text-foreground text-right">{formatDuration(p.avgTimeSavedSeconds)}</span>
                <span className="text-muted-foreground">Avg Length</span>
                <span className="text-foreground text-right">{formatDuration(p.avgEpisodeLengthSeconds)}</span>
                <span className="text-muted-foreground">Total Cost</span>
                <span className="text-foreground text-right">{formatCost(p.totalCost)}</span>
                <span className="text-muted-foreground">Tokens (In/Out)</span>
                <span className="text-foreground text-right">{formatTokenCount(p.totalInputTokens)} / {formatTokenCount(p.totalOutputTokens)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Desktop Table Layout */}
      {sortedPodcasts.length > 0 && (
        <div className="hidden sm:block bg-card rounded-lg border border-border overflow-hidden">
          <h2 className="text-lg font-semibold text-foreground p-4 pb-2">All Podcasts</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <SortTh field="podcastTitle" label="Podcast" align="left" />
                  <SortTh field="episodeCount" label="Episodes" />
                  <SortTh field="totalAds" label="Total Ads" />
                  <SortTh field="avgAds" label="Avg Ads" />
                  <SortTh field="avgTimeSavedSeconds" label="Avg Time Saved" />
                  <SortTh field="avgEpisodeLengthSeconds" label="Avg Length" className="hidden lg:table-cell" />
                  <SortTh field="totalCost" label="Total Cost" className="hidden lg:table-cell" />
                  <SortTh field="avgTokensPerEpisode" label="Avg Tokens" className="hidden lg:table-cell" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sortedPodcasts.map((p) => (
                  <tr key={p.podcastSlug} className="hover:bg-muted/50">
                    <td className="px-4 py-3 text-sm text-foreground font-medium truncate max-w-[200px]">{p.podcastTitle}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{p.episodeCount}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{p.totalAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{p.avgAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{formatDuration(p.avgTimeSavedSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden lg:table-cell">{formatDuration(p.avgEpisodeLengthSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden lg:table-cell">{formatCost(p.totalCost)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden lg:table-cell">
                      <span>{formatTokenCount(p.avgTokensPerEpisode)}</span>
                      <span className="text-xs text-muted-foreground ml-1">({formatTokenCount(p.totalInputTokens)}/{formatTokenCount(p.totalOutputTokens)})</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, min, max }: { label: string; value: string; min: string; max: string }) {
  return (
    <div className="bg-card rounded-lg border border-border p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="text-xl font-bold text-foreground">{value}</p>
      <p className="text-xs text-muted-foreground mt-1">Min: {min} / Max: {max}</p>
    </div>
  );
}

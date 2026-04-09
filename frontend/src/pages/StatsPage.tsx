import { useState, useMemo, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import { getDashboardStats, getStatsByDay, getStatsByPodcast } from '../api/stats';
import { getFeeds } from '../api/feeds';
import LoadingSpinner from '../components/LoadingSpinner';

function useThemeColors() {
  const [colors, setColors] = useState({ primary: '', card: '', border: '', foreground: '', muted: '' });
  useEffect(() => {
    function resolve(name: string) {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : '';
    }
    function update() {
      setColors({
        primary: resolve('--primary'),
        card: resolve('--card'),
        border: resolve('--border'),
        foreground: resolve('--card-foreground'),
        muted: resolve('--muted-foreground'),
      });
    }
    update();
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
    return () => obs.disconnect();
  }, []);
  return colors;
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

  const { data: byPodcast, isLoading: podLoading } = useQuery({
    queryKey: ['stats-by-podcast'],
    queryFn: getStatsByPodcast,
  });

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
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
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
        </div>
      )}

      {/* Totals Row */}
      {dashboard && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
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

      {/* Podcast Stats Table */}
      {byPodcast?.podcasts && byPodcast.podcasts.length > 0 && (
        <div className="bg-card rounded-lg border border-border overflow-hidden">
          <h2 className="text-lg font-semibold text-foreground p-4 pb-2">All Podcasts</h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[600px]">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Podcast</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider">Episodes</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider">Total Ads</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider hidden md:table-cell">Avg Ads</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider hidden md:table-cell">Avg Time Saved</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider hidden lg:table-cell">Avg Length</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider hidden lg:table-cell">Total Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {byPodcast.podcasts.map((p) => (
                  <tr key={p.podcastSlug} className="hover:bg-muted/50">
                    <td className="px-4 py-3 text-sm text-foreground font-medium truncate max-w-[200px]">{p.podcastTitle}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{p.episodeCount}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right">{p.totalAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden md:table-cell">{p.avgAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden md:table-cell">{formatDuration(p.avgTimeSavedSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden lg:table-cell">{formatDuration(p.avgEpisodeLengthSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right hidden lg:table-cell">{formatCost(p.totalCost)}</td>
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

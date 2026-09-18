import { Fragment, useState, useMemo, type ReactNode } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import {
  getDashboardStats, getStatsByDay, getStatsByPodcast, getReviewerStats, getAddressingStats,
  getModelUsageStats, getEpisodeCostStats, getEpisodeCostRuns, getLedgerFilterOptions,
  getSpendAttempts, ModelUsageQueryParams, EpisodeCostQueryParams,
} from '../api/stats';
import ProcessingRunsTable from '../components/ProcessingRunsTable';
import { getCueAggregateStats } from '../api/cueDetections';
import { feedsQueryOptions } from '../api/feeds';
import { feedDisplayTitle } from '../utils/feedTitle';
import { formatTokenCount } from './settings/settingsUtils';
import { formatCost, formatDateTime, formatStatsDuration as formatDuration } from '../utils/format';
import { SkeletonStatCards, SkeletonChart, SkeletonRows } from '../components/Skeleton';
import { useThemeColors } from '../hooks/useThemeColors';
import { Pagination } from '../components/Pagination';
import { SortHeader, useSortState } from '../components/SortHeader';
import DisclosureButton from '../components/DisclosureButton';
import CostAmount from '../components/CostAmount';
import { selectBase, inputBase, focusRing } from '../components/fieldStyles';
import { btnSecondary } from '../components/buttonStyles';
import { getErrorMessage } from '../api/client';
import { EpisodeCostStat, ModelUsageSortField, EpisodeCostSortField, ModelUsageStat, SpendAttempt } from '../api/types';

type PodcastSortField = 'podcastTitle' | 'episodeCount' | 'runCount' | 'totalAds' | 'avgAds' | 'avgTimeSavedSeconds' | 'avgEpisodeLengthSeconds' | 'totalCost' | 'avgTokensPerEpisode';

function ReviewerStatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-secondary/50 rounded-md p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums text-foreground">{value}</p>
    </div>
  );
}

// Label + value only, no background -- used inside an already-shaded parent
// (e.g. the addressing-mode card), where a second background would nest a
// card inside a card.
function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex min-w-0 flex-col">
      <p className="flex-1 text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums text-foreground">{value}</p>
    </div>
  );
}

function generateChartColors(primary: string, count: number): string[] {
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

const LEDGER_LIMIT = 20;

// Spend filters, sort and page live in the URL so a cost investigation can be
// reloaded or handed to someone else. Values equal to the default are left
// out of the query rather than written into it.
const STATS_PARAM_DEFAULTS = {
  podcast: '', from: '', to: '', provider: '', model: '',
  muSort: 'knownCostUsd', muDir: 'desc', muPage: '1',
  ecSort: 'lastActivityAt', ecDir: 'desc', ecPage: '1',
} as const;

type StatsParam = keyof typeof STATS_PARAM_DEFAULTS;

function useStatsParams() {
  const [params, setParams] = useSearchParams();
  const read = (key: StatsParam) => params.get(key) ?? STATS_PARAM_DEFAULTS[key];
  // One patch per interaction: two separate setters in the same handler would
  // each build from the pre-update query and the second would drop the first.
  const write = (changes: Partial<Record<StatsParam, string>>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value && value !== STATS_PARAM_DEFAULTS[key as StatsParam]) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };
  const readPage = (key: StatsParam) => Math.max(1, Number(read(key)) || 1);
  return { read, readPage, write };
}

// Option list that always contains the current selection, so a filter whose
// value has no data in the selected scope is still shown as selected.
function withSelection(options: string[], selected: string): { value: string; inScope: boolean }[] {
  const rows = options.map((value) => ({ value, inScope: true }));
  if (selected && !options.includes(selected)) {
    rows.unshift({ value: selected, inScope: false });
  }
  return rows;
}

function modelUsageKey(stat: ModelUsageStat): string {
  return `${stat.provider}::${stat.model}`;
}

// Counts the calls that were billable but came back without a price, so an
// unpriced model reads as a coverage gap rather than as $0 spent.
function formatCoverage(calls: number, unknownCostCount: number): string {
  if (unknownCostCount === 0) return 'Fully priced';
  return `${unknownCostCount} of ${calls} calls unpriced`;
}

// Failed list query: an empty table would otherwise read as "no spend".
function QueryErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="bg-destructive/10 text-destructive rounded-lg p-3 mb-3 flex flex-wrap items-center justify-between gap-3">
      <span className="text-sm">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className={`px-3 py-1.5 text-sm ${btnSecondary} rounded transition-colors ${focusRing}`}
      >
        Retry
      </button>
    </div>
  );
}

function ModelUsageDetail({ stat }: { stat: ModelUsageStat }) {
  return (
    <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
      <div>
        <dt className="text-muted-foreground">Distinct episodes</dt>
        <dd className="text-foreground">{stat.distinctEpisodes}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Input tokens</dt>
        <dd className="text-foreground">{formatTokenCount(stat.inputTokens)}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Output tokens</dt>
        <dd className="text-foreground">{formatTokenCount(stat.outputTokens)}</dd>
      </div>
    </dl>
  );
}

function ModelUsageTable({
  items, sortField, sortDir, onSort, expanded, onToggle,
}: {
  items: ModelUsageStat[];
  sortField: ModelUsageSortField;
  sortDir: 'asc' | 'desc';
  onSort: (field: ModelUsageSortField) => void;
  expanded: Set<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <>
      <div className="hidden sm:block bg-card border border-border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table aria-label="Provider and model usage" className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="w-8 px-2" aria-hidden="true" />
                <SortHeader field="provider" label="Provider" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="model" label="Model" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="calls" label="Calls" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="knownCostUsd" label="Known Cost" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="unknownCostCount" label="Coverage" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                    No model usage recorded for this filter
                  </td>
                </tr>
              ) : items.map((stat) => {
                const key = modelUsageKey(stat);
                const isOpen = expanded.has(key);
                const label = isOpen
                  ? `Hide usage detail for ${stat.provider} ${stat.model}`
                  : `Show usage detail for ${stat.provider} ${stat.model}`;
                return (
                  <Fragment key={key}>
                    <tr className="hover:bg-muted/50">
                      <td className="pl-4 py-3">
                        <DisclosureButton expanded={isOpen} onToggle={() => onToggle(key)} label={label} />
                      </td>
                      <td className="px-4 py-3 text-sm text-foreground">{stat.provider}</td>
                      <td className="px-4 py-3 text-sm text-foreground">{stat.model}</td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{stat.calls}</td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">
                        <CostAmount amount={parseFloat(stat.knownCostUsd)} unpricedCount={stat.unknownCostCount} />
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{formatCoverage(stat.calls, stat.unknownCostCount)}</td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-muted/20">
                        <td colSpan={6} className="px-4 py-3">
                          <ModelUsageDetail stat={stat} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="sm:hidden space-y-3">
        <div className="flex items-center gap-2">
          <select
            aria-label="Sort model usage by"
            value={sortField}
            onChange={(e) => onSort(e.target.value as ModelUsageSortField)}
            className={`flex-1 min-w-0 min-h-11 ${selectBase}`}
          >
            <option value="provider">Provider</option>
            <option value="model">Model</option>
            <option value="calls">Calls</option>
            <option value="knownCostUsd">Known spend</option>
            <option value="unknownCostCount">Unpriced calls</option>
          </select>
          <button
            type="button"
            onClick={() => onSort(sortField)}
            aria-label={sortDir === 'asc' ? 'Sort model usage descending' : 'Sort model usage ascending'}
            className={`min-h-11 min-w-11 inline-flex items-center justify-center rounded ${btnSecondary} transition-colors ${focusRing}`}
          >
            <span aria-hidden="true">{sortDir === 'asc' ? '\u2191' : '\u2193'}</span>
          </button>
        </div>
        {items.length === 0 ? (
          <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">
            No model usage recorded for this filter
          </div>
        ) : items.map((stat) => {
          const key = modelUsageKey(stat);
          const isOpen = expanded.has(key);
          const label = isOpen ? 'Hide usage detail' : 'Show usage detail';
          return (
            <div key={key} className="bg-card rounded-lg border border-border p-4">
              <div className="mb-2">
                <p className="text-sm font-medium text-foreground break-words">{stat.provider} / {stat.model}</p>
                <p className="mt-1.5 text-sm text-muted-foreground text-right">
                  <CostAmount amount={parseFloat(stat.knownCostUsd)} unpricedCount={stat.unknownCostCount} />
                </p>
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>{stat.calls} calls</span>
                <span>{formatCoverage(stat.calls, stat.unknownCostCount)}</span>
              </div>
              <div className="mt-2">
                <DisclosureButton expanded={isOpen} onToggle={() => onToggle(key)} label={label} showLabel />
              </div>
              {isOpen && (
                <div className="mt-2">
                  <ModelUsageDetail stat={stat} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

// Columns of the contributing-calls panel below. One definition drives the
// table and the stacked rows so the two layouts cannot drift.
const ATTEMPT_COLUMNS: {
  label: string; align: 'left' | 'right'; render: (a: SpendAttempt) => ReactNode;
}[] = [
  { label: 'Phase', align: 'left',
    render: (a) => `${a.phase}${a.invokingPass ? ` (pass ${a.invokingPass})` : ''}` },
  { label: 'Provider', align: 'left', render: (a) => `${a.provider} / ${a.credentialSlot}` },
  { label: 'Model', align: 'left', render: (a) => a.returnedModel ?? a.model },
  { label: 'Status', align: 'left', render: (a) => a.status },
  { label: 'Tokens', align: 'right',
    render: (a) => formatTokenCount((a.inputTokens ?? 0) + (a.outputTokens ?? 0)) },
  { label: 'Cost', align: 'right',
    render: (a) => (a.costUsd == null ? 'Unknown' : formatCost(parseFloat(a.costUsd))) },
  { label: 'When', align: 'left', render: (a) => formatDateTime(a.finalizedAt ?? a.createdAt) },
];

// The ledger rows one Incomplete amount was summed from, so an unpriced call
// can be named rather than left as a gap in a total.
function SpendAttemptsPanel({ slug, episodeId }: { slug: string; episodeId: string }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['spend-attempts', slug, episodeId],
    queryFn: () => getSpendAttempts({ slug, episodeId }),
  });
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading contributing calls...</p>;
  if (isError || !data) {
    return <QueryErrorPanel message="Could not load the contributing calls." onRetry={() => { void refetch(); }} />;
  }
  if (data.attempts.length === 0) {
    return <p className="text-sm text-muted-foreground">No recorded calls for this episode.</p>;
  }
  const cell = (align: 'left' | 'right') =>
    `py-1 pr-3 ${align === 'right' ? 'text-right tabular-nums' : 'text-left'}`;
  return (
    <div className="text-xs">
      <p className="text-muted-foreground mb-2">
        {data.unknownCostCount} of {data.total} calls have no recorded price. Known spend{' '}
        {formatCost(parseFloat(data.knownCostUsd))}.
        {data.truncated && ' Only the first 200 calls are listed; the totals cover them all.'}
      </p>
      <div className="hidden sm:block overflow-x-auto">
        <table aria-label="Contributing calls" className="w-full">
          <thead>
            <tr className="text-muted-foreground">
              {ATTEMPT_COLUMNS.map((c) => (
                <th key={c.label} className={`font-medium ${cell(c.align)}`}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.attempts.map((a) => (
              <tr key={a.attemptId} className="border-t border-border/40">
                {ATTEMPT_COLUMNS.map((c) => (
                  <td key={c.label} className={`${cell(c.align)} break-all`}>{c.render(a)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sm:hidden space-y-2">
        {data.attempts.map((a) => (
          <dl key={a.attemptId} className="rounded border border-border/40 p-2 space-y-0.5">
            {ATTEMPT_COLUMNS.map((c) => (
              <div key={c.label} className="flex justify-between gap-2">
                <dt className="text-muted-foreground shrink-0">{c.label}</dt>
                <dd className={`break-all ${c.align === 'right' ? 'tabular-nums' : ''}`}>{c.render(a)}</dd>
              </div>
            ))}
          </dl>
        ))}
      </div>
    </div>
  );
}

// Collapsed row summary: the most-expensive model first, then a +N count of
// the rest. Full list stays in the title tooltip.
function ModelsSummary({ stat }: { stat: EpisodeCostStat }) {
  if (stat.modelsUsed.length === 0) return <span>None recorded</span>;
  const top = stat.topModel || stat.modelsUsed[0];
  const extra = stat.modelsUsed.length - 1;
  return (
    <span title={stat.modelsUsed.join(', ')}>
      {top}{extra > 0 && <span className="text-muted-foreground"> +{extra}</span>}
    </span>
  );
}

// The run/phase breakdown shown when an episode-costs row is expanded. Reuses
// the episode page's ProcessingRunsTable so both surfaces are identical.
function EpisodeRunsDetail({ slug, episodeId }: { slug: string; episodeId: string }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['episode-cost-runs', slug, episodeId],
    queryFn: () => getEpisodeCostRuns(slug, episodeId),
  });
  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading run breakdown...</p>;
  }
  if (isError) {
    return <QueryErrorPanel message="Could not load the run breakdown." onRetry={() => refetch()} />;
  }
  const runs = data?.runs ?? [];
  if (runs.length === 0) {
    return <p className="text-sm text-muted-foreground">No recorded runs for this episode.</p>;
  }
  return <ProcessingRunsTable runs={runs} />;
}

// Mobile cards have no column headers, so the same sort fields the desktop
// table exposes are offered through a select.
const EPISODE_COST_SORT_OPTIONS: { field: EpisodeCostSortField; label: string }[] = [
  { field: 'lastActivityAt', label: 'Last activity' },
  { field: 'podcastTitle', label: 'Podcast' },
  { field: 'episodeTitle', label: 'Episode' },
  { field: 'runCount', label: 'Runs' },
  { field: 'latestRunCostUsd', label: 'Latest run cost' },
  { field: 'cumulativeCostUsd', label: 'Cumulative cost' },
];

function episodeCostKey(stat: EpisodeCostStat): string {
  return `${stat.podcastSlug}-${stat.episodeId}`;
}

function EpisodeCostTable({
  items, sortField, sortDir, onSort, expanded, onToggle,
}: {
  items: EpisodeCostStat[];
  sortField: EpisodeCostSortField;
  sortDir: 'asc' | 'desc';
  onSort: (field: EpisodeCostSortField) => void;
  expanded: Set<string>;
  onToggle: (key: string) => void;
}) {
  const [attemptRows, toggleAttemptRows] = useExpandedKeys();
  return (
    <>
      <div className="hidden sm:block bg-card border border-border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table aria-label="Episode costs" className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="w-8 px-2" aria-hidden="true" />
                <SortHeader field="podcastTitle" label="Podcast" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="episodeTitle" label="Episode" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Models Used
                </th>
                <SortHeader field="runCount" label="Runs" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="latestRunCostUsd" label="Latest Run" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="cumulativeCostUsd" label="Cumulative" align="right" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
                <SortHeader field="lastActivityAt" label="Last Activity" sortField={sortField} sortDirection={sortDir} onSort={onSort} />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">
                    No episode costs recorded for this filter
                  </td>
                </tr>
              ) : items.map((stat) => {
                const key = episodeCostKey(stat);
                const isOpen = expanded.has(key);
                const label = isOpen
                  ? `Hide run breakdown for ${stat.episodeTitle}`
                  : `Show run breakdown for ${stat.episodeTitle}`;
                return (
                  <Fragment key={key}>
                    <tr className="hover:bg-muted/50">
                      <td className="pl-4 py-3">
                        <DisclosureButton expanded={isOpen} onToggle={() => onToggle(key)} label={label} />
                      </td>
                      <td className="px-4 py-3">
                        <Link to={`/feeds/${stat.podcastSlug}`} className={`text-primary hover:underline text-sm truncate max-w-[150px] block ${focusRing}`} title={stat.podcastTitle}>
                          {stat.podcastTitle}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <Link to={`/feeds/${stat.podcastSlug}/episodes/${stat.episodeId}`} className={`text-primary hover:underline text-sm truncate max-w-[200px] block ${focusRing}`} title={stat.episodeTitle}>
                          {stat.episodeTitle}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground truncate max-w-[160px]">
                        <ModelsSummary stat={stat} />
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{stat.runCount}</td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">
                        <CostAmount amount={parseFloat(stat.latestRunCostUsd)} unpriced={stat.latestRunUnknownCount > 0} unpricedCount={stat.latestRunUnknownCount} />
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">
                        <CostAmount
                          amount={parseFloat(stat.cumulativeCostUsd)}
                          unpriced={stat.hasUnknownCost}
                          unpricedCount={stat.unknownCostCount}
                          onInspect={() => toggleAttemptRows(key)}
                          inspectExpanded={attemptRows.has(key)}
                        />
                      </td>
                      <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap tabular-nums">{formatDateTime(stat.lastActivityAt)}</td>
                    </tr>
                    {attemptRows.has(key) && (
                      <tr className="bg-muted/20">
                        <td colSpan={8} className="px-4 py-3">
                          <SpendAttemptsPanel slug={stat.podcastSlug} episodeId={stat.episodeId} />
                        </td>
                      </tr>
                    )}
                    {isOpen && (
                      <tr className="bg-muted/20">
                        <td colSpan={8} className="px-4 py-3">
                          <EpisodeRunsDetail slug={stat.podcastSlug} episodeId={stat.episodeId} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="sm:hidden space-y-3">
        <div className="flex items-center gap-2">
          <select
            aria-label="Sort episode costs by"
            value={sortField}
            onChange={(e) => onSort(e.target.value as EpisodeCostSortField)}
            className={`flex-1 min-h-11 ${selectBase}`}
          >
            {EPISODE_COST_SORT_OPTIONS.map((option) => (
              <option key={option.field} value={option.field}>{option.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => onSort(sortField)}
            aria-label={sortDir === 'asc' ? 'Sort descending' : 'Sort ascending'}
            className={`min-h-11 min-w-11 inline-flex items-center justify-center rounded border border-border bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors ${focusRing}`}
          >
            <span aria-hidden="true">{sortDir === 'asc' ? '\u2191' : '\u2193'}</span>
          </button>
        </div>
        {items.length === 0 ? (
          <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">
            No episode costs recorded for this filter
          </div>
        ) : items.map((stat) => {
          const key = episodeCostKey(stat);
          const isOpen = expanded.has(key);
          const label = isOpen ? 'Hide run breakdown' : 'Show run breakdown';
          return (
          <div key={key} className="bg-card rounded-lg border border-border p-4">
            <Link to={`/feeds/${stat.podcastSlug}`} className={`text-primary hover:underline text-sm font-medium truncate max-w-[200px] block ${focusRing}`} title={stat.podcastTitle}>
              {stat.podcastTitle}
            </Link>
            <Link to={`/feeds/${stat.podcastSlug}/episodes/${stat.episodeId}`} className={`text-primary hover:underline text-sm block truncate mb-2 ${focusRing}`} title={stat.episodeTitle}>
              {stat.episodeTitle}
            </Link>
            <p className="text-xs text-muted-foreground truncate mb-1">
              Models: <ModelsSummary stat={stat} />
            </p>
            <div className="flex items-center gap-4 text-xs text-muted-foreground flex-wrap">
              <span>{stat.runCount} runs</span>
              <span>Latest: <CostAmount amount={parseFloat(stat.latestRunCostUsd)} unpriced={stat.latestRunUnknownCount > 0} unpricedCount={stat.latestRunUnknownCount} /></span>
              <span>Cumulative: <CostAmount
                amount={parseFloat(stat.cumulativeCostUsd)}
                unpriced={stat.hasUnknownCost}
                unpricedCount={stat.unknownCostCount}
                onInspect={() => toggleAttemptRows(key)}
                inspectExpanded={attemptRows.has(key)}
              /></span>
              <span>{formatDateTime(stat.lastActivityAt)}</span>
            </div>
            {attemptRows.has(key) && (
              <div className="mt-2">
                <SpendAttemptsPanel slug={stat.podcastSlug} episodeId={stat.episodeId} />
              </div>
            )}
            <div className="mt-2">
              <DisclosureButton expanded={isOpen} onToggle={() => onToggle(key)} label={label} showLabel />
            </div>
            {isOpen && (
              <div className="mt-2">
                <EpisodeRunsDetail slug={stat.podcastSlug} episodeId={stat.episodeId} />
              </div>
            )}
          </div>
          );
        })}
      </div>
    </>
  );
}

// Expansion state for one table's rows, keyed by row id.
function useExpandedKeys(): [Set<string>, (key: string) => void] {
  const [keys, setKeys] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setKeys((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  return [keys, toggle];
}

export default function StatsPage() {
  const theme = useThemeColors();
  const [podcastFilter, setPodcastFilter] = useState('');

  // itemStyle matters: without it the value line keeps recharts' dark default
  // and is unreadable on the dark card. cursor replaces the default light grey
  // rect, which read as a second bar rather than a hover highlight (#592).
  const tooltipStyle = useMemo(() => ({
    contentStyle: {
      backgroundColor: theme.card,
      border: `1px solid ${theme.border}`,
      borderRadius: 8,
      color: theme.foreground,
    },
    labelStyle: { color: theme.foreground },
    itemStyle: { color: theme.foreground },
    cursor: { fill: theme.border, opacity: 0.3 },
  }), [theme.card, theme.border, theme.foreground]);

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });

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

  // Same defer-until-dashboard pattern as the reviewer query above.
  const { data: addressing } = useQuery({
    queryKey: ['stats', 'addressing', podcastFilter],
    queryFn: () => getAddressingStats(podcastFilter || undefined),
    enabled: !!dashboard,
  });

  // Global cue telemetry (thresholds tuning). Deferred behind the dashboard so
  // the page does not fetch it before there is anything to show.
  const { data: cueStats } = useQuery({
    queryKey: ['stats', 'cue-aggregate'],
    queryFn: getCueAggregateStats,
    enabled: !!dashboard,
  });

  // Merge the match-score and near-miss histograms onto one score axis so both
  // series render as grouped bars per bucket.
  const cueHistogram = useMemo(() => {
    if (!cueStats) return [];
    const byScore = new Map<number, { scoreFrom: number; matches: number; nearMisses: number }>();
    for (const b of cueStats.scoreHistogram) {
      byScore.set(b.scoreFrom, { scoreFrom: b.scoreFrom, matches: b.count, nearMisses: 0 });
    }
    for (const b of cueStats.nearMissHistogram) {
      const row = byScore.get(b.scoreFrom) ?? { scoreFrom: b.scoreFrom, matches: 0, nearMisses: 0 };
      row.nearMisses = b.count;
      byScore.set(b.scoreFrom, row);
    }
    return [...byScore.values()].sort((a, b) => a.scoreFrom - b.scoreFrom);
  }, [cueStats]);

  const cueUnusedReasons = useMemo(() => {
    if (!cueStats) return [];
    return Object.entries(cueStats.unusedReasons)
      .map(([reason, count]) => ({ reason, count }))
      .sort((a, b) => b.count - a.count);
  }, [cueStats]);

  const { sortField, sortDirection: sortDir, handleSort } =
    useSortState<PodcastSortField>('totalAds', 'desc');

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

  // Shared ledger filters (date interval, podcast, provider, model) drive both
  // the model-usage and episode-cost lists below, and all of it round-trips
  // through the URL.
  const { read, readPage, write } = useStatsParams();
  const ledgerFrom = read('from');
  const ledgerTo = read('to');
  const ledgerPodcast = read('podcast');
  const ledgerProvider = read('provider');
  const ledgerModel = read('model');

  const modelUsagePage = readPage('muPage');
  const modelUsageSort = read('muSort') as ModelUsageSortField;
  const modelUsageDir = read('muDir') as 'asc' | 'desc';
  const episodeCostPage = readPage('ecPage');
  const episodeCostSort = read('ecSort') as EpisodeCostSortField;
  const episodeCostDir = read('ecDir') as 'asc' | 'desc';

  // Same rule as useSortState: the active column flips direction, a new one
  // resets to desc. Both reset the page, since the rows behind it change.
  const handleModelUsageSort = (field: ModelUsageSortField) => write(
    field === modelUsageSort
      ? { muDir: modelUsageDir === 'asc' ? 'desc' : 'asc', muPage: '1' }
      : { muSort: field, muDir: 'desc', muPage: '1' });
  const handleEpisodeCostSort = (field: EpisodeCostSortField) => write(
    field === episodeCostSort
      ? { ecDir: episodeCostDir === 'asc' ? 'desc' : 'asc', ecPage: '1' }
      : { ecSort: field, ecDir: 'desc', ecPage: '1' });

  const [expandedModels, toggleExpandedModel] = useExpandedKeys();
  const [expandedEpisodes, toggleExpandedEpisode] = useExpandedKeys();

  // The bare YYYY-MM-DD from <input type="date"> goes through as-is: the
  // backend reads from/to as whole UTC days, both ends included.
  const isIntervalSpend = !!ledgerFrom || !!ledgerTo;
  const spendLabel = !isIntervalSpend
    ? 'Lifetime spend (all recorded runs)'
    : ledgerFrom && ledgerTo
      ? `Interval spend from ${ledgerFrom} to ${ledgerTo} (UTC days, both included)`
      : ledgerFrom
        ? `Interval spend from ${ledgerFrom} onward (UTC days)`
        : `Interval spend through ${ledgerTo} (UTC days)`;

  const ledgerScope = useMemo(() => ({
    from: ledgerFrom || undefined,
    to: ledgerTo || undefined,
    podcastSlug: ledgerPodcast || undefined,
  }), [ledgerFrom, ledgerTo, ledgerPodcast]);

  const modelUsageParams: ModelUsageQueryParams = {
    ...ledgerScope,
    page: modelUsagePage,
    limit: LEDGER_LIMIT,
    sortBy: modelUsageSort,
    sortDir: modelUsageDir,
    provider: ledgerProvider || undefined,
    model: ledgerModel || undefined,
  };
  const {
    data: modelUsageData, isLoading: modelUsageLoading,
    error: modelUsageError, refetch: refetchModelUsage,
  } = useQuery({
    queryKey: ['stats-model-usage', modelUsageParams],
    queryFn: () => getModelUsageStats(modelUsageParams),
    // A page or sort change keeps the current rows until the next ones land.
    placeholderData: keepPreviousData,
  });

  const episodeCostParams: EpisodeCostQueryParams = {
    ...ledgerScope,
    page: episodeCostPage,
    limit: LEDGER_LIMIT,
    sortBy: episodeCostSort,
    sortDir: episodeCostDir,
    provider: ledgerProvider || undefined,
    model: ledgerModel || undefined,
  };
  const {
    data: episodeCostData, isLoading: episodeCostLoading,
    error: episodeCostError, refetch: refetchEpisodeCosts,
  } = useQuery({
    queryKey: ['stats-episode-costs', episodeCostParams],
    queryFn: () => getEpisodeCostStats(episodeCostParams),
    placeholderData: keepPreviousData,
  });

  // Complete, unpaginated option lists for the provider/model selects, so a
  // value past the first list page is still selectable.
  const {
    data: filterOptionsData, error: filterOptionsError, refetch: refetchFilterOptions,
  } = useQuery({
    queryKey: ['stats-ledger-filter-options', ledgerScope],
    queryFn: () => getLedgerFilterOptions(ledgerScope),
  });

  // A selection the current scope no longer offers is kept and marked rather
  // than dropped silently: the lists below still answer for that filter.
  const providerOptions = useMemo(
    () => withSelection(filterOptionsData?.providers ?? [], ledgerProvider),
    [filterOptionsData, ledgerProvider]
  );
  const modelOptions = useMemo(() => {
    const pairs = filterOptionsData?.pairs ?? [];
    const scoped = ledgerProvider ? pairs.filter((p) => p.provider === ledgerProvider) : pairs;
    return withSelection([...new Set(scoped.map((p) => p.model))].sort(), ledgerModel);
  }, [filterOptionsData, ledgerProvider, ledgerModel]);

  // Built from the same conditions the sections render under, so a link never
  // points at an anchor that is not on the page.
  const sectionLinks = [
    { id: 'stats-overview', label: 'Overview', show: !!dashboard },
    { id: 'stats-charts', label: 'Charts', show: topPodcasts.length > 0 || !!byDay?.days },
    { id: 'stats-reviewer', label: 'Reviewer', show: !!reviewer },
    { id: 'stats-addressing', label: 'Addressing', show: !!addressing },
    { id: 'stats-cues', label: 'Audio cues',
      show: !!cueStats && (cueStats.total > 0 || cueStats.nearMissTotal > 0) },
    { id: 'stats-spend', label: 'Spend', show: true },
    { id: 'stats-podcasts', label: 'Podcasts', show: sortedPodcasts.length > 0 },
  ].filter((entry) => entry.show);

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Stats</h1>
        <select
          aria-label="Filter summary cards, charts, reviewer and addressing sections by podcast"
          title="Applies to the summary cards, charts, reviewer and addressing sections. The LLM spend section has its own podcast filter."
          value={podcastFilter}
          onChange={(e) => setPodcastFilter(e.target.value)}
          className={`w-full sm:w-auto ${selectBase}`}
        >
          <option value="">All Podcasts</option>
          {feeds?.map((feed) => (
            <option key={feed.slug} value={feed.slug}>
              {feedDisplayTitle(feed)}
            </option>
          ))}
        </select>
      </div>

      <nav aria-label="Stats sections" className="mb-6 flex gap-2 overflow-x-auto no-scrollbar">
        {sectionLinks.map(({ id, label }) => (
          <a
            key={id}
            href={`#${id}`}
            className={`inline-flex items-center min-h-11 shrink-0 whitespace-nowrap rounded px-3 text-sm ${btnSecondary} transition-colors ${focusRing}`}
          >
            {label}
          </a>
        ))}
      </nav>

      <section id="stats-overview" className="scroll-mt-28">
      {/* Summary Cards */}
      {dashLoading && (
        <SkeletonStatCards count={7} lines={3} className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-8" />
      )}
      {dashboard && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
          <StatCard
            label="Avg Time Saved"
            value={formatDuration(dashboard.avgTimeSavedSeconds)}
            details={<><span>Min: {formatDuration(dashboard.minTimeSavedSeconds)}</span>{' '}<span>Max: {formatDuration(dashboard.maxTimeSavedSeconds)}</span></>}
          />
          <StatCard
            label="Avg Ads Removed"
            value={dashboard.avgAdsRemoved.toFixed(1)}
            details={<><span>Min: {String(dashboard.minAdsRemoved)}</span>{' '}<span>Max: {String(dashboard.maxAdsRemoved)}</span></>}
          />
          <StatCard
            label="Avg Cost"
            value={formatCost(dashboard.avgCostPerEpisode)}
            details={<><span>Min: {formatCost(dashboard.minCostPerEpisode)}</span>{' '}<span>Max: {formatCost(dashboard.maxCostPerEpisode)}</span></>}
          />
          <StatCard
            label="Avg Processing Time"
            value={formatDuration(dashboard.avgProcessingTimeSeconds)}
            details={<><span>Min: {formatDuration(dashboard.minProcessingTimeSeconds)}</span>{' '}<span>Max: {formatDuration(dashboard.maxProcessingTimeSeconds)}</span></>}
          />
          <StatCard
            label="Avg Episode Length"
            value={formatDuration(dashboard.avgEpisodeLengthSeconds)}
            details={<><span>Min: {formatDuration(dashboard.minEpisodeLengthSeconds)}</span>{' '}<span>Max: {formatDuration(dashboard.maxEpisodeLengthSeconds)}</span></>}
          />
          <StatCard
            label="Avg Tokens/Run"
            value={formatTokenCount(dashboard.avgInputTokens + dashboard.avgOutputTokens)}
            details={<><span>In: {formatTokenCount(dashboard.avgInputTokens)}</span>{' '}<span>Out: {formatTokenCount(dashboard.avgOutputTokens)}</span></>}
          />
          <StatCard
            label="Avg Audio Cues"
            value={dashboard.avgAudioCuesDetected.toFixed(1)}
            details={<><span>Min: {String(dashboard.minAudioCuesDetected)}</span>{' '}<span>Max: {String(dashboard.maxAudioCuesDetected)}</span></>}
          />
        </div>
      )}

      {/* Totals Row */}
      {dashLoading && (
        <SkeletonStatCards count={6} className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8" />
      )}
      {dashboard && (
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Episodes</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{dashboard.totalEpisodesProcessed}</p>
            {dashboard.totalRuns !== dashboard.totalEpisodesProcessed && (
              <p className="text-xs text-muted-foreground mt-1">{dashboard.totalRuns} processing runs</p>
            )}
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Ads Removed</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{dashboard.totalAdsRemoved}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Audio Cues</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{dashboard.totalAudioCuesDetected}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Time Saved</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{formatDuration(dashboard.totalTimeSavedSeconds)}</p>
            {dashboard.episodesWithTimeSaved > 0 &&
              dashboard.episodesWithTimeSaved !== dashboard.totalEpisodesProcessed && (
                <p className="text-xs text-muted-foreground mt-1">from {dashboard.episodesWithTimeSaved} episodes</p>
              )}
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total LLM Cost</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{formatCost(dashboard.totalLlmCost)}</p>
          </div>
          <div className="bg-card rounded-lg border border-border p-4">
            <p className="text-sm text-muted-foreground">Total Tokens</p>
            <p className="text-xl font-bold tabular-nums text-foreground">{formatTokenCount(dashboard.totalInputTokens + dashboard.totalOutputTokens)}</p>
            <p className="text-xs text-muted-foreground mt-1">In: {formatTokenCount(dashboard.totalInputTokens)} / Out: {formatTokenCount(dashboard.totalOutputTokens)}</p>
          </div>
        </div>
      )}

      </section>

      {/* Charts */}
      <div id="stats-charts" className="scroll-mt-28 grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Top Podcasts by Ads */}
        {podLoading && <SkeletonChart />}
        {topPodcasts.length > 0 && (
          <div className="bg-card rounded-lg border border-border p-4">
            <h2 className="text-lg font-semibold text-foreground mb-4">Top Podcasts by Ads Removed</h2>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={topPodcasts} layout="vertical" margin={{ left: 20, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                <XAxis type="number" tick={{ fill: theme.foreground, fontSize: 12 }} />
                <YAxis
                  dataKey="podcastTitle"
                  type="category"
                  width={130}
                  tick={{ fill: theme.foreground, fontSize: 12 }}
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
        {dayLoading && <SkeletonChart />}
        {byDay?.days && (
          <div className="bg-card rounded-lg border border-border p-4">
            <h2 className="text-lg font-semibold text-foreground mb-4">Episodes Processed by Day</h2>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={byDay.days} margin={{ left: 0, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: theme.foreground, fontSize: 12 }}
                  tickFormatter={(v: string) => v.slice(0, 3)}
                />
                <YAxis tick={{ fill: theme.foreground, fontSize: 12 }} />
                <Tooltip {...tooltipStyle} />
                <Bar dataKey="count" name="Episodes" fill={theme.primary} fillOpacity={0.85} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Ad Reviewer Stats. Renders whenever the query has loaded; all-zero
          counts are the visible signal that the reviewer is configured but
          has not yet run on any episode. */}
      {reviewer && (
        <div id="stats-reviewer" className="scroll-mt-28 bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
          <h2 className="text-lg font-semibold text-foreground mb-4">Ad Reviewer Stats</h2>
          {reviewer.totalReviews === 0 && (
            <p className="text-sm text-muted-foreground mb-4">
              No reviews yet. Enable Ad Reviewer in Settings, AI & Processing section, then reprocess an episode.
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

      {/* Addressing modes. Renders whenever the query has loaded; all-zero
          counts are the visible signal that neither mode has run yet. */}
      {addressing && (
        <div id="stats-addressing" className="scroll-mt-28 bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
          <h2 className="text-lg font-semibold text-foreground mb-1">Addressing modes</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Contract compliance and ad yield per addressing mode. Random-mode runs count toward whichever mode was drawn. Yield is recorded from 2.92.0 on, so its sample can lag the compliance sample.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {(['timestamps', 'segment_ids'] as const).map((mode) => {
              const stats = addressing.modes[mode];
              return (
                <div key={mode} className="bg-secondary/50 rounded-md p-4">
                  <p className="text-sm font-medium text-foreground mb-3">
                    {mode === 'timestamps' ? 'Timestamps' : 'Segment IDs'}
                  </p>
                  <div className="grid grid-cols-3 gap-3">
                    <Metric label="Runs" value={stats.runs} />
                    <Metric label="Windows judged" value={stats.windowsJudged} />
                    <Metric label="Compliance" value={`${stats.compliancePct.toFixed(1)}%`} />
                  </div>
                  <div className="mt-3 pt-3 border-t border-border">
                    {stats.yieldRuns > 0 ? (
                      <>
                        <div className="grid grid-cols-3 gap-3">
                          <Metric label="Ads kept" value={`${stats.adsKept} / ${stats.adsProposed}`} />
                          <Metric label="Kept rate" value={`${stats.keptPct.toFixed(1)}%`} />
                          <Metric label="Yield runs" value={stats.yieldRuns} />
                        </div>
                        {(stats.adsDroppedInvalidRef > 0 || stats.adsDroppedOutOfWindow > 0 || stats.adsDroppedTooLong > 0) && (
                          <p className="text-xs text-muted-foreground mt-2">
                            Dropped: {stats.adsDroppedInvalidRef} invalid ref, {stats.adsDroppedOutOfWindow} out of window, {stats.adsDroppedTooLong} too long
                          </p>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-muted-foreground">No yield data yet</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Audio Cue Telemetry. Renders once the aggregate query loads and there
          is any recorded cue (matches or near-misses). Below-threshold
          near-misses show as a distinct series -- they never affected cuts. */}
      {cueStats && (cueStats.total > 0 || cueStats.nearMissTotal > 0) && (
        <div id="stats-cues" className="scroll-mt-28 bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
          <h2 className="text-lg font-semibold text-foreground mb-4">Audio Cue Telemetry</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
            <ReviewerStatCard label="Matches" value={cueStats.total} />
            <ReviewerStatCard label="Snapped" value={cueStats.snapped} />
            <ReviewerStatCard label="Paired" value={cueStats.paired} />
            <ReviewerStatCard label="Near-misses" value={cueStats.nearMissTotal} />
          </div>
          {cueHistogram.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={cueHistogram} margin={{ left: 0, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                <XAxis
                  dataKey="scoreFrom"
                  tick={{ fill: theme.foreground, fontSize: 12 }}
                />
                <YAxis tick={{ fill: theme.foreground, fontSize: 12 }} />
                <Tooltip {...tooltipStyle} />
                <Bar dataKey="matches" name="Matches (affected cuts)"
                     fill={theme.primary} fillOpacity={0.85} radius={[4, 4, 0, 0]} />
                <Bar dataKey="nearMisses" name="Near-misses (never affected cuts)"
                     fill="#f59e0b" fillOpacity={0.7} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
          {cueUnusedReasons.length > 0 && (
            <div className="mt-6">
              <h3 className="text-sm font-medium text-muted-foreground mb-2">
                Unused cue reasons
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {cueUnusedReasons.map(({ reason, count }) => (
                  <ReviewerStatCard key={reason} label={reason.replace(/_/g, ' ')} value={count} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* LLM cost ledger: provider/model usage and per-episode spend, both
          paginated and sorted server-side over the llm_call_usage ledger.
          Includes failed and cancelled runs that incurred cost. */}
      <div id="stats-spend" className="scroll-mt-28 bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
        <h2 className="text-lg font-semibold text-foreground mb-1">LLM spend</h2>
        <p className="text-sm text-muted-foreground mb-4">
          Spend by provider, model, and episode, including failed or cancelled runs that incurred cost.
          The filters below apply to this section only, and dates select whole UTC days.
        </p>

        <div className="flex flex-wrap items-end gap-3 mb-3">
          <div className="min-w-0">
            <label htmlFor="spendFrom" className="block text-xs font-medium text-muted-foreground mb-1">From</label>
            <input
              type="date"
              id="spendFrom"
              value={ledgerFrom}
              onChange={(e) => write({ from: e.target.value, muPage: '1', ecPage: '1' })}
              className={`min-w-0 ${inputBase}`}
            />
          </div>
          <div className="min-w-0">
            <label htmlFor="spendTo" className="block text-xs font-medium text-muted-foreground mb-1">To</label>
            <input
              type="date"
              id="spendTo"
              value={ledgerTo}
              onChange={(e) => write({ to: e.target.value, muPage: '1', ecPage: '1' })}
              className={`min-w-0 ${inputBase}`}
            />
          </div>
          <select
            aria-label="Filter spend by podcast"
            value={ledgerPodcast}
            onChange={(e) => write({ podcast: e.target.value, muPage: '1', ecPage: '1' })}
            className={`w-full sm:w-auto ${selectBase}`}
          >
            <option value="">All Podcasts</option>
            {feeds?.map((feed) => (
              <option key={feed.slug} value={feed.slug}>
                {feedDisplayTitle(feed)}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter spend by provider"
            value={ledgerProvider}
            onChange={(e) => write({ provider: e.target.value, model: '', muPage: '1', ecPage: '1' })}
            className={`w-full sm:w-auto ${selectBase}`}
          >
            <option value="">All Providers</option>
            {providerOptions.map(({ value, inScope }) => (
              <option key={value} value={value}>
                {inScope ? value : `${value} (no data in range)`}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter spend by model"
            value={ledgerModel}
            onChange={(e) => write({ model: e.target.value, muPage: '1', ecPage: '1' })}
            className={`w-full sm:w-auto ${selectBase}`}
          >
            <option value="">All Models</option>
            {modelOptions.map(({ value, inScope }) => (
              <option key={value} value={value}>
                {inScope ? value : `${value} (no data in range)`}
              </option>
            ))}
          </select>
        </div>
        <p className="text-sm text-muted-foreground mb-6">{spendLabel}</p>

        {filterOptionsError && (
          <QueryErrorPanel
            message={`Could not load the provider and model filter options: ${getErrorMessage(filterOptionsError)}. The lists below may not offer every value.`}
            onRetry={() => { void refetchFilterOptions(); }}
          />
        )}

        <h3 className="text-base font-medium text-foreground mb-3">Provider &amp; model usage</h3>
        {modelUsageLoading && <SkeletonRows count={5} />}
        {modelUsageError && (
          <QueryErrorPanel
            message={`Could not load provider and model usage: ${getErrorMessage(modelUsageError)}. This is a failed request, not an absence of spend.`}
            onRetry={() => { void refetchModelUsage(); }}
          />
        )}
        {modelUsageData && (
          <>
            <ModelUsageTable
              items={modelUsageData.items}
              sortField={modelUsageSort}
              sortDir={modelUsageDir}
              onSort={handleModelUsageSort}
              expanded={expandedModels}
              onToggle={toggleExpandedModel}
            />
            <Pagination
              page={modelUsagePage}
              totalPages={modelUsageData.totalPages}
              total={modelUsageData.total}
              onPage={(p) => write({ muPage: String(p) })}
            />
          </>
        )}

        <h3 className="text-base font-medium text-foreground mb-3 mt-8">Episode costs</h3>
        {episodeCostLoading && <SkeletonRows count={5} />}
        {episodeCostError && (
          <QueryErrorPanel
            message={`Could not load episode costs: ${getErrorMessage(episodeCostError)}. This is a failed request, not an absence of spend.`}
            onRetry={() => { void refetchEpisodeCosts(); }}
          />
        )}
        {episodeCostData && (
          <>
            <EpisodeCostTable
              items={episodeCostData.items}
              sortField={episodeCostSort}
              sortDir={episodeCostDir}
              onSort={handleEpisodeCostSort}
              expanded={expandedEpisodes}
              onToggle={toggleExpandedEpisode}
            />
            <Pagination
              page={episodeCostPage}
              totalPages={episodeCostData.totalPages}
              total={episodeCostData.total}
              onPage={(p) => write({ ecPage: String(p) })}
            />
          </>
        )}
      </div>

      {/* Podcast Stats Table */}
      <div id="stats-podcasts" className="scroll-mt-28" />
      {/* Mobile Card Layout */}
      {sortedPodcasts.length > 0 && (
        <div className="sm:hidden space-y-3">
          <h2 className="text-lg font-semibold text-foreground">All Podcasts</h2>
          {sortedPodcasts.map((p) => (
            <div key={p.podcastSlug} className="bg-card rounded-lg border border-border p-4">
              <p className="text-sm font-medium text-foreground mb-2">{p.podcastTitle}</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <span className="text-muted-foreground">Episodes</span>
                <span className="text-foreground text-right tabular-nums">{p.episodeCount}</span>
                <span className="text-muted-foreground">Runs</span>
                <span className="text-foreground text-right tabular-nums">{p.runCount}</span>
                <span className="text-muted-foreground">Total Ads</span>
                <span className="text-foreground text-right tabular-nums">{p.totalAds}</span>
                <span className="text-muted-foreground">Avg Ads</span>
                <span className="text-foreground text-right tabular-nums">{p.avgAds}</span>
                <span className="text-muted-foreground">Avg Time Saved</span>
                <span className="text-foreground text-right tabular-nums">{formatDuration(p.avgTimeSavedSeconds)}</span>
                <span className="text-muted-foreground">Avg Length</span>
                <span className="text-foreground text-right tabular-nums">{formatDuration(p.avgEpisodeLengthSeconds)}</span>
                <span className="text-muted-foreground">Total Cost</span>
                <span className="text-foreground text-right tabular-nums">{formatCost(p.totalCost)}</span>
                <span className="text-muted-foreground">Tokens (In/Out)</span>
                <span className="text-foreground text-right tabular-nums">{formatTokenCount(p.totalInputTokens)} / {formatTokenCount(p.totalOutputTokens)}</span>
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
            <table aria-label="Podcast totals" className="w-full">
              <thead className="bg-muted/50">
                <tr>
                  <SortHeader field="podcastTitle" label="Podcast" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="episodeCount" label="Episodes" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="runCount" label="Runs" className="px-4 hidden lg:table-cell" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="totalAds" label="Total Ads" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="avgAds" label="Avg Ads" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="avgTimeSavedSeconds" label="Avg Time Saved" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="avgEpisodeLengthSeconds" label="Avg Length" className="px-4 hidden lg:table-cell" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="totalCost" label="Total Cost" className="px-4 hidden lg:table-cell" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                  <SortHeader field="avgTokensPerEpisode" label="Avg Tokens/Run" className="px-4 hidden lg:table-cell" align="right" sortField={sortField} sortDirection={sortDir} onSort={handleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sortedPodcasts.map((p) => (
                  <tr key={p.podcastSlug} className="hover:bg-muted/50">
                    <td className="px-4 py-3 text-sm text-foreground font-medium truncate max-w-[200px]">{p.podcastTitle}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{p.episodeCount}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums hidden lg:table-cell">{p.runCount}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{p.totalAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{p.avgAds}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums">{formatDuration(p.avgTimeSavedSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums hidden lg:table-cell">{formatDuration(p.avgEpisodeLengthSeconds)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums hidden lg:table-cell">{formatCost(p.totalCost)}</td>
                    <td className="px-4 py-3 text-sm text-muted-foreground text-right tabular-nums hidden lg:table-cell">
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

function StatCard({ label, value, details }: { label: string; value: string; details: ReactNode }) {
  return (
    <div className="row-span-3 grid grid-rows-subgrid gap-y-0 bg-card rounded-lg border border-border p-3 sm:p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="text-xl font-bold tabular-nums text-foreground">{value}</p>
      <p className="flex flex-wrap content-start gap-x-2 text-xs text-muted-foreground mt-1 [&>span]:whitespace-nowrap">{details}</p>
    </div>
  );
}

import type { EpisodeCostStat, ModelUsageStat } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, pageSlice, sortBy } from './paging';
import { loadAllPages } from './pagedDump';

type Dump<R> = { items: R[] };

const num = (s: string) => parseFloat(s);
const USAGE_SORT: Record<string, (r: ModelUsageStat) => unknown> = {
  provider: (r) => r.provider, model: (r) => r.model, calls: (r) => r.calls,
  distinctEpisodes: (r) => r.distinctEpisodes, inputTokens: (r) => r.inputTokens,
  outputTokens: (r) => r.outputTokens, knownCostUsd: (r) => num(r.knownCostUsd),
  unknownCostCount: (r) => r.unknownCostCount,
  cacheHitRate: (r) => (r.inputTokens ? r.cacheReadTokens / r.inputTokens : null),
};
const COST_SORT: Record<string, (r: EpisodeCostStat) => unknown> = {
  podcastSlug: (r) => r.podcastSlug, podcastTitle: (r) => r.podcastTitle, episodeTitle: (r) => r.episodeTitle,
  runCount: (r) => r.runCount, latestRunCostUsd: (r) => num(r.latestRunCostUsd),
  cumulativeCostUsd: (r) => num(r.cumulativeCostUsd), lastActivityAt: (r) => r.lastActivityAt,
};

// Shared tail of get_model_usage_stats / get_episode_cost_stats (database/stats.py).
function build<R>(
  dumpKey: string, sorts: Record<string, (r: R) => unknown>, defaultSort: string,
  keep: (r: R, p: URLSearchParams) => boolean,
): ListHandler {
  return async (url, load) => {
    const all = await loadAllPages<Dump<R>, R>(dumpKey, load, (b) => b.items);
    if (!all) return null;
    const p = url.searchParams;
    const limit = clampInt(p.get('limit'), 50, 1, 100);
    const page = clampInt(p.get('page'), 1, 1, Infinity);
    const dir = (p.get('sortDir') ?? 'desc').toLowerCase() === 'asc' ? 'asc' : 'desc';
    const rows = sortBy(all.rows.filter((r) => keep(r, p)), sorts[p.get('sortBy') ?? ''] ?? sorts[defaultSort], dir);
    const total = rows.length;
    return {
      status: 200,
      body: { ...all.body, items: pageSlice(rows, page, limit), total, totalPages: total > 0 ? Math.ceil(total / limit) : 1, page, limit },
    };
  };
}

// Aggregate rows carry no podcast slug or dates, so podcastSlug, from and to cannot apply here.
export const modelUsageHandler = build<ModelUsageStat>(
  '/stats/model-usage?limit=100&page=1&sortBy=knownCostUsd&sortDir=desc', USAGE_SORT, 'knownCostUsd',
  (r, p) => (!p.get('provider') || r.provider === p.get('provider')) && (!p.get('model') || r.model === p.get('model')),
);

// Rows carry modelsUsed but no provider, so the provider filter cannot apply; from and to are ignored.
export const episodeCostsHandler = build<EpisodeCostStat>(
  '/stats/episode-costs?limit=100&page=1&sortBy=lastActivityAt&sortDir=desc', COST_SORT, 'lastActivityAt',
  (r, p) => (!p.get('podcastSlug') || r.podcastSlug === p.get('podcastSlug')) && (!p.get('model') || r.modelsUsed.includes(p.get('model')!)),
);

import type { EpisodeCostStat, ModelUsageStat } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt, pageSlice, parseDir, sortBy, totalPages } from './paging';
import { loadAllPages } from './pagedDump';

type Dump<R> = { items: R[] };

const num = (s: string) => parseFloat(s);
type SortMap<R> = Record<string, keyof R | ((r: R) => unknown)>;
const USAGE_SORT: SortMap<ModelUsageStat> = {
  provider: 'provider', model: 'model', calls: 'calls',
  distinctEpisodes: 'distinctEpisodes', inputTokens: 'inputTokens',
  outputTokens: 'outputTokens', knownCostUsd: (r) => num(r.knownCostUsd),
  unknownCostCount: 'unknownCostCount',
  cacheHitRate: (r) => (r.inputTokens ? r.cacheReadTokens / r.inputTokens : null),
};
const COST_SORT: SortMap<EpisodeCostStat> = {
  podcastSlug: 'podcastSlug', podcastTitle: 'podcastTitle', episodeTitle: 'episodeTitle',
  runCount: 'runCount', latestRunCostUsd: (r) => num(r.latestRunCostUsd),
  cumulativeCostUsd: (r) => num(r.cumulativeCostUsd), lastActivityAt: 'lastActivityAt',
};

// Shared tail of get_model_usage_stats / get_episode_cost_stats (database/stats.py).
function build<R>(
  dumpKey: string, sorts: SortMap<R>, defaultSort: string,
  keep: (r: R, p: URLSearchParams) => boolean,
  unsupported: string[],
): ListHandler {
  return async (url, load) => {
    const p = url.searchParams;
    if (unsupported.some((key) => p.get(key))) {
      return { status: 422, body: { error: 'These filters are not available in this demo snapshot.' } };
    }
    const all = await loadAllPages<Dump<R>, R>(dumpKey, load, (b) => b.items);
    if (!all) return null;
    const limit = clampInt(p.get('limit'), 50, 1, 100);
    const page = clampInt(p.get('page'), 1, 1, Infinity);
    const dir = parseDir(p.get('sortDir'), 'desc');
    const rows = sortBy(all.rows.filter((r) => keep(r, p)), sorts[p.get('sortBy') ?? ''] ?? sorts[defaultSort], dir);
    const total = rows.length;
    return {
      status: 200,
      body: { ...all.body, items: pageSlice(rows, page, limit), total, totalPages: totalPages(total, limit), page, limit },
    };
  };
}

// Aggregated rows cannot reconstruct date or podcast scopes.
export const modelUsageHandler = build<ModelUsageStat>(
  '/stats/model-usage?limit=100&page=1&sortBy=knownCostUsd&sortDir=desc', USAGE_SORT, 'knownCostUsd',
  (r, p) => (!p.get('provider') || r.provider === p.get('provider')) && (!p.get('model') || r.model === p.get('model')),
  ['podcastSlug', 'from', 'to'],
);

// Model and provider scopes require recomputing costs from the spend ledger.
export const episodeCostsHandler = build<EpisodeCostStat>(
  '/stats/episode-costs?limit=100&page=1&sortBy=lastActivityAt&sortDir=desc', COST_SORT, 'lastActivityAt',
  (r, p) => !p.get('podcastSlug') || r.podcastSlug === p.get('podcastSlug'),
  ['provider', 'model', 'from', 'to'],
);

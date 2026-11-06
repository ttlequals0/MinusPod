import type { ProcessingEpisode } from '../../api/settings';
import type { ListHandler } from './index';
import { clampInt, offsetSlice } from './paging';

// Ports get_processing_episodes (api/episodes/runs.py): active rows lead, the queue is paged.
export const processingHandler: ListHandler = async (url, load) => {
  const dump = await load('/episodes/processing?limit=1000&offset=0');
  if (!dump) return null;
  const p = url.searchParams;
  const limit = clampInt(p.get('limit'), 200, 1, 1000);
  const offset = clampInt(p.get('offset'), 0, 0, Infinity);
  const rows = dump.body as ProcessingEpisode[];
  const queued = offsetSlice(rows.filter((r) => r.stage === 'queued'), offset, limit)
    .map((r, i) => ({ ...r, queuePosition: offset + i + 1 }));
  return { status: 200, body: [...rows.filter((r) => r.stage !== 'queued'), ...queued] };
};

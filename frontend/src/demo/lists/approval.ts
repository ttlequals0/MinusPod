import type { ApprovalEpisodeRow } from '../../api/settings';
import type { ListHandler } from './index';
import { clampInt, offsetSlice } from './paging';
import { loadAllPages } from './pagedDump';

type Body = { count: number; episodes: ApprovalEpisodeRow[] };

// Ports list_awaiting_approval / list_held_episodes (api/episodes/approval.py).
function approval(key: string, bySlug: boolean): ListHandler {
  return async (url, load) => {
    const dump = await loadAllPages<Body, ApprovalEpisodeRow>(key, load, (body) => body.episodes, 500, 'offset');
    if (!dump) return null;
    const p = url.searchParams;
    const limit = clampInt(p.get('limit'), 100, 1, 500);
    const offset = clampInt(p.get('offset'), 0, 0, Infinity);
    const slug = bySlug ? p.get('slug') : null;
    const body = dump.body;
    const rows = slug ? dump.rows.filter((e) => e.slug === slug) : dump.rows;
    return { status: 200, body: { ...body, count: slug ? rows.length : body.count, episodes: offsetSlice(rows, offset, limit) } };
  };
}

export const awaitingApprovalHandler = approval('/episodes/awaiting-approval?limit=500&offset=0', true);
export const heldHandler = approval('/episodes/held?limit=500&offset=0', false);

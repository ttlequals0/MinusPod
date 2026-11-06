import type { PatternCleanupSuggestion } from '../../api/patternCleanup';
import type { ListHandler } from './index';
import { clampInt, offsetSlice, sortBy } from './paging';

// Ports list_cleanup_suggestions and get_cleanup_suggestions (api/pattern_cleanup.py).
export const suggestionsHandler: ListHandler = async (url, load) => {
  const dump = await load('/patterns/cleanup/suggestions?limit=200&offset=0');
  if (!dump) return null;
  const p = url.searchParams;
  const status = p.get('status');
  const kind = p.get('kind');
  const limit = clampInt(p.get('limit') || 50, 50, 1, 200);
  const offset = clampInt(p.get('offset'), 0, 0, Infinity);
  const beforeId = p.has('before_id') ? clampInt(p.get('before_id'), NaN, -Infinity, Infinity) : NaN;
  let rows = sortBy((dump.body as { suggestions: PatternCleanupSuggestion[] }).suggestions, 'id', 'desc');
  if (status) rows = rows.filter((r) => r.status === status);
  if (kind) rows = rows.filter((r) => r.kind === kind);
  const keyset = !Number.isNaN(beforeId);
  if (keyset) rows = rows.filter((r) => r.id < beforeId);
  return { status: 200, body: { ...dump.body as object, suggestions: offsetSlice(rows, keyset ? 0 : offset, limit) } };
};

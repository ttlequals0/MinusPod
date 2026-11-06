import type { AdPattern } from '../../api/patterns';
import type { ListHandler } from './index';

const SOURCES = ['local', 'community', 'imported'];

// Ports list_patterns (api/patterns.py).
export const patternsHandler: ListHandler = async (url, load) => {
  const dump = await load('/patterns');
  if (!dump) return null;
  const p = url.searchParams;
  const scope = p.get('scope');
  const podcast = p.get('podcast_id');
  const network = p.get('network_id');
  const source = p.get('source');
  const activeOnly = (p.get('active_only') ?? p.get('active') ?? '').toLowerCase() === 'true';
  let rows = (dump.body as { patterns: AdPattern[] }).patterns;
  if (activeOnly) rows = rows.filter((r) => r.is_active);
  if (scope && scope !== 'all') rows = rows.filter((r) => r.scope === scope);
  if (podcast) rows = rows.filter((r) => r.podcast_id === podcast);
  if (network) rows = rows.filter((r) => r.network_id === network);
  if (source && SOURCES.includes(source)) rows = rows.filter((r) => r.source === source);
  return { status: 200, body: { ...dump.body as object, patterns: rows } };
};

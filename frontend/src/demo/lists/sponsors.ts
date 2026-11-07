import type { Sponsor } from '../../api/types';
import type { ListHandler } from './index';

// Ports list_sponsors (api/sponsors.py).
export const sponsorsHandler: ListHandler = async (url, load) => {
  const dump = await load('/sponsors?include_inactive=true');
  if (!dump) return null;
  const p = url.searchParams;
  const includeInactive = (p.get('include_inactive') ?? '').toLowerCase() === 'true';
  const tag = p.get('tag');
  let rows = (dump.body as { sponsors: Sponsor[] }).sponsors;
  if (!includeInactive) rows = rows.filter((r) => r.is_active !== false);
  if (tag) rows = rows.filter((r) => r.tags?.includes(tag));
  return { status: 200, body: { ...dump.body as object, sponsors: rows } };
};

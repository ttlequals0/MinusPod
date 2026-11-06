import type { ChangelogResponse } from '../../api/types';
import type { ListHandler } from './index';
import { clampInt } from './paging';

const parts = (v: string) => v.split('.').map(Number);

function newer(a: string, b: string): boolean {
  const [x, y] = [parts(a), parts(b)];
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d !== 0) return d > 0;
  }
  return false;
}

// Ports get_changelog (api/system.py).
export const changelogHandler: ListHandler = async (url, load) => {
  const dump = await load('/changelog?limit=50');
  if (!dump) return null;
  const p = url.searchParams;
  const since = (p.get('since') ?? '').trim();
  if (since && !/^\d+\.\d+\.\d+$/.test(since)) return { status: 400, body: { error: 'bad_since' } };
  const limit = p.has('limit') ? clampInt(p.get('limit'), 10, -Infinity, Infinity) : 10;
  if (limit < 1) return { status: 400, body: { error: 'bad_limit' } };
  const body = dump.body as ChangelogResponse;
  const sections = since ? body.sections.filter((s) => newer(s.version, since)) : body.sections;
  return { status: 200, body: { ...body, sections: sections.slice(0, Math.min(limit, 50)) } };
};

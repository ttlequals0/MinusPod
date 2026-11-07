import type { Fixture } from '../fixtures';

// Follow recorded pages without presenting a partial dump as a complete list.
export async function loadAllPages<B, R>(
  key: string,
  load: (key: string) => Promise<Fixture | null>,
  rowsOf: (body: B) => R[],
  pageSize = 100,
  pagination: 'page' | 'offset' = 'page',
): Promise<{ body: B; rows: R[] } | null> {
  const first = await load(key);
  if (!first) return null;
  const body = first.body as B;
  const rows = [...rowsOf(body)];
  const counts = body as { total?: number; count?: number };
  const total = counts.total ?? counts.count;
  if (total === undefined) throw new Error(`[demo] dump has no total: ${key}`);
  for (let page = 2; rows.length < total; page++) {
    const nextKey = pagination === 'offset'
      ? key.replace(/offset=\d+/, `offset=${(page - 1) * pageSize}`)
      : key.replace(/page=\d+/, `page=${page}`);
    const next = await load(nextKey);
    const chunk = next ? rowsOf(next.body as B) : [];
    if (!chunk.length) throw new Error(`[demo] incomplete fixture dump: ${nextKey}`);
    rows.push(...chunk);
  }
  return { body, rows };
}

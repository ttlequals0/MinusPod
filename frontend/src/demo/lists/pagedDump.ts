import type { Fixture } from '../fixtures';

const PAGE_SIZE = 100;

// Loads page 1 of a paged dump and follows full pages; null when page 1 is missing.
export async function loadAllPages<B, R>(
  key: string,
  load: (key: string) => Promise<Fixture | null>,
  rowsOf: (body: B) => R[],
): Promise<{ body: B; rows: R[] } | null> {
  const first = await load(key);
  if (!first) return null;
  const body = first.body as B;
  const rows = [...rowsOf(body)];
  let last = rows.length;
  for (let page = 2; last === PAGE_SIZE; page++) {
    const next = await load(key.replace(/page=\d+/, `page=${page}`));
    if (!next) break;
    const chunk = rowsOf(next.body as B);
    rows.push(...chunk);
    last = chunk.length;
  }
  return { body, rows };
}

type Dir = 'asc' | 'desc';
type Key<T> = keyof T | ((row: T) => unknown);

const isNil = (v: unknown) => v === null || v === undefined;

function compare(a: unknown, b: unknown): number {
  if (Array.isArray(a) && Array.isArray(b)) {
    for (let i = 0; i < a.length; i++) {
      const c = compare(a[i], b[i]);
      if (c !== 0) return c;
    }
    return 0;
  }
  if (isNil(a) || isNil(b)) return isNil(a) === isNil(b) ? 0 : isNil(a) ? -1 : 1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b);
  return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
}

// Stable. Nulls sort smallest (SQLite) unless nullsLast pins them last in both directions.
export function sortBy<T>(rows: T[], key: Key<T>, dir: Dir, opts: { nullsLast?: boolean } = {}): T[] {
  const get = (r: T) => (typeof key === 'function' ? key(r) : r[key]);
  const sign = dir === 'desc' ? -1 : 1;
  return rows
    .map((row, i) => ({ row, i, k: get(row) }))
    .sort((x, y) => {
      if (opts.nullsLast && isNil(x.k) !== isNil(y.k)) return isNil(x.k) ? 1 : -1;
      return sign * compare(x.k, y.k) || x.i - y.i;
    })
    .map((e) => e.row);
}

export function pageSlice<T>(rows: T[], page: number, limit: number): T[] {
  return rows.slice((page - 1) * limit, page * limit);
}

export function offsetSlice<T>(rows: T[], offset: number, limit: number): T[] {
  return rows.slice(offset, offset + limit);
}

const INT_RE = /^\s*[+-]?\d+\s*$/;

export const isIntParam = (v: string | null): boolean => INT_RE.test(v ?? '');

// Mirrors Flask's request.args.get(type=int): unparseable falls back to def.
export function clampInt(v: string | null, def: number, min: number, max: number): number {
  const n = isIntParam(v) ? parseInt(v as string, 10) : def;
  return Math.min(Math.max(n, min), max);
}

export function parseDir(v: string | null, def: Dir): Dir {
  const d = v?.toLowerCase();
  return d === 'asc' || d === 'desc' ? d : def;
}

export const totalPages = (total: number, limit: number): number => (total > 0 && limit > 0 ? Math.ceil(total / limit) : 1);

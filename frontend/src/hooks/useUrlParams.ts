import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { SEARCH_DEBOUNCE_MS } from './useDebouncedValue';
import type { SortDirection } from '../components/SortHeader';

type SetParams = ReturnType<typeof useSearchParams>[1];

// List state kept in the query string: values equal to the default are left out.
// Pass a module-level defaults object so the returned functions stay stable.
// `resetKey` (usually 'page') is dropped on every write that does not name it.
export function useUrlParams<K extends string>(defaults: Record<K, string>, options: { resetKey?: NoInfer<K> } = {}) {
  const [params, setParams] = useSearchParams();
  const { resetKey } = options;
  return useMemo(() => {
    const read = (key: K) => params.get(key) ?? defaults[key];
    const readOneOf = <T extends string>(key: K, allowed: readonly T[], fallback: T): T => {
      const value = read(key);
      return (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
    };
    // Clamps to totalPages when given, so a shrunken list needs no URL write.
    const readPage = (key: K, totalPages?: number) => {
      const page = Math.max(1, Number(read(key)) || 1);
      return totalPages === undefined ? page : Math.min(page, Math.max(1, totalPages));
    };
    // One write per interaction: a second call would build from the stale URL.
    const write = (changes: Partial<Record<K, string>>) => {
      const next = new URLSearchParams(params);
      if (resetKey && !(resetKey in changes)) next.delete(resetKey);
      for (const key of Object.keys(changes) as K[]) {
        const value = changes[key];
        if (value && value !== defaults[key]) next.set(key, value);
        else next.delete(key);
      }
      setParams(next, { replace: true });
    };
    return { read, readOneOf, readPage, write };
  }, [params, setParams, defaults, resetKey]);
}

// Functional update so a debounce timer never builds from a stale URL.
function patchParams(setParams: SetParams, changes: Record<string, string>, resetKey = 'page') {
  setParams((prev) => {
    const next = new URLSearchParams(prev);
    next.delete(resetKey);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return next;
  }, { replace: true });
}

// Sort field and direction in ?sort= and ?dir=; a sort change drops ?page=.
export function useUrlSort<T extends string>(fields: readonly T[], defaultField: T, defaultDir: SortDirection) {
  const [params, setParams] = useSearchParams();
  const rawField = params.get('sort') ?? defaultField;
  const rawDir = params.get('dir') ?? defaultDir;
  const sortBy = (fields as readonly string[]).includes(rawField) ? (rawField as T) : defaultField;
  const sortDirection: SortDirection = rawDir === 'asc' || rawDir === 'desc' ? rawDir : defaultDir;
  const write = (field: T, dir: SortDirection) =>
    patchParams(setParams, { sort: field === defaultField ? '' : field, dir: dir === defaultDir ? '' : dir });
  // Header click: the active column flips, a new one starts at the default direction.
  const onSort = (field: T) =>
    write(field, field === sortBy ? (sortDirection === 'asc' ? 'desc' : 'asc') : defaultDir);
  return {
    sortBy, sortDirection, onSort,
    onField: (field: T) => write(field, defaultDir),
    onDirection: (dir: SortDirection) => write(sortBy, dir),
  };
}

// Search box backed by ?<key>=: input state updates at once, the URL after typing pauses.
// An outside URL change (Back, a link, a tab switch) replaces the input.
export function useUrlSearch(
  key = 'q',
  { enabled = true, trim = false }: { enabled?: boolean; trim?: boolean } = {},
) {
  const [params, setParams] = useSearchParams();
  const urlValue = params.get(key) ?? '';
  const [input, setInput] = useState(urlValue);
  const typed = trim ? input.trim() : input;
  const [seen, setSeen] = useState({ urlValue, enabled });

  // Our own write lands as urlValue === typed; anything else came from outside.
  if (seen.urlValue !== urlValue || seen.enabled !== enabled) {
    setSeen({ urlValue, enabled });
    if (input !== urlValue && (urlValue !== typed || !enabled)) setInput(urlValue);
  }

  useEffect(() => {
    if (!enabled || typed === urlValue) return;
    const timer = setTimeout(() => patchParams(setParams, { [key]: typed }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [typed, urlValue, enabled, key, setParams]);

  // Third value: the committed URL text, for queries.
  return [input, setInput, urlValue] as const;
}

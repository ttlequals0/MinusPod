import { useEffect, useMemo, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { search } from '../api/search';
import { buildSearchResultRows } from '../components/SearchResults';
import type { SearchResultRow, UnifiedSearchGroups } from '../components/SearchResults';

export const SEARCH_MIN_QUERY = 2;
const SEARCH_DEBOUNCE_MS = 300;

const EMPTY_GROUPS: UnifiedSearchGroups = { shows: [], episodes: [], transcripts: [] };

// Query + arrow/Enter keyboard-nav state shared by the header palette (QuickSearch)
// and the Dashboard field, so both stay behaviorally identical against /search.
export function useUnifiedSearch(seed: string, limit = 8) {
  const [query, setQueryValue] = useState(seed);
  const [debounced, setDebounced] = useState(seed);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  const ready = debounced.trim().length >= SEARCH_MIN_QUERY;
  const { data } = useQuery({
    queryKey: ['unifiedSearch', debounced, limit],
    queryFn: ({ signal }) => search(debounced, limit, signal),
    enabled: ready,
    placeholderData: keepPreviousData,
  });

  // Guard on ready: keepPreviousData would otherwise show stale rows under a too-short query.
  const results: UnifiedSearchGroups = ready && data ? data : EMPTY_GROUPS;
  const rows = useMemo(() => buildSearchResultRows(results), [results]);

  // Clamp: a shorter result set can swap in under a stale index.
  const current = rows.length ? Math.min(active, rows.length - 1) : 0;

  useEffect(() => {
    const id = rows[current]?.key;
    if (id) document.getElementById(id)?.scrollIntoView?.({ block: 'nearest' });
  }, [current, rows]);

  const setQuery = (value: string) => { setQueryValue(value); setActive(0); };

  const onKeyDown = (e: KeyboardEvent, onSelect: (row: SearchResultRow) => void) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (rows.length === 0) return;
      setActive(e.key === 'ArrowDown' ? Math.min(current + 1, rows.length - 1) : Math.max(current - 1, 0));
    } else if (e.key === 'Enter' && rows[current]) {
      e.preventDefault();
      onSelect(rows[current]);
    }
  };

  return { query, setQuery, debounced, ready, results, rows, current, setActive, onKeyDown };
}

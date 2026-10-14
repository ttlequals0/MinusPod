import { useEffect, useState } from 'react';

export const SEARCH_DEBOUNCE_MS = 300;

// The value once it has stopped changing for `ms`.
export function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

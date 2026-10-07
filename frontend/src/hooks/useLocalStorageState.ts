import { useEffect, useState } from 'react';

/**
 * State hook backed by localStorage. Values are JSON-serialized.
 *
 * Legacy sites that previously stored raw, non-JSON strings (e.g. "list",
 * "true") are read transparently: on JSON.parse failure the raw string is
 * returned as-is, then the next write upgrades the entry to JSON.
 */
// One-shot read of a stored value without subscribing to it. Same parse rules
// as the hook (JSON with legacy raw-string fallback). Used by components that
// need another component's persisted state (e.g. a CollapsibleSection's open
// flag) only to seed their own initial state.
export function readStoredValue<T>(key: string, defaultValue: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return defaultValue;
    try {
      return JSON.parse(raw) as T;
    } catch {
      // Legacy raw-string value (not JSON-encoded).
      return raw as unknown as T;
    }
  } catch {
    return defaultValue;
  }
}

export function useLocalStorageState<T>(
  key: string,
  defaultValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => readStoredValue(key, defaultValue));

  // The lazy initializer above only runs on mount. A component reused across
  // a changing key without remounting (e.g. CollapsibleSection across
  // /feeds/:slug) must re-seed from the new key's own persisted value
  // instead of carrying the previous key's state forward. Adjusted during
  // render (React's documented pattern for this) rather than in an effect,
  // so the corrected value is already in place for any hook called later in
  // this same render (e.g. a useQuery `enabled` flag) -- an effect-based
  // resync would still fire that hook once with the stale value first.
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setValue(readStoredValue(key, defaultValue));
  }

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage unavailable (private mode, quota); ignore.
    }
  }, [key, value]);

  return [value, setValue];
}

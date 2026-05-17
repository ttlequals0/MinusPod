import { useEffect, useState } from 'react';

/**
 * State hook backed by localStorage. Values are JSON-serialized.
 *
 * Legacy sites that previously stored raw, non-JSON strings (e.g. "list",
 * "true") are read transparently: on JSON.parse failure the raw string is
 * returned as-is, then the next write upgrades the entry to JSON.
 */
export function useLocalStorageState<T>(
  key: string,
  defaultValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
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
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage unavailable (private mode, quota); ignore.
    }
  }, [key, value]);

  return [value, setValue];
}

export default useLocalStorageState;

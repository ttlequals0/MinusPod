import { useState } from 'react';

// Render-phase form seeder. Calls onSync whenever the upstream query data
// identity changes (typically: undefined -> first payload, or a refetch
// produces a new object reference). Mirrors the React 19 conditional-
// setState pattern: a snapshot of the last-seen data is held in useState,
// and the seed runs synchronously during render so form inputs reflect
// the new value on the same render cycle that data arrived.
//
// Use this instead of useEffect+setState for query->form sync; useEffect
// would seed AFTER commit (one frame of stale UI) and was the exact
// anti-pattern fixed in Settings.tsx on May 4 2026.
export function useSyncFromQuery<T>(data: T | undefined, onSync: (d: T) => void): void {
  const [snapshot, setSnapshot] = useState<T | undefined>(data);
  if (data !== snapshot) {
    setSnapshot(data);
    if (data !== undefined) {
      onSync(data);
    }
  }
}

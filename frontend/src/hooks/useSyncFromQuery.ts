import { useState } from 'react';

// Render-phase form seeder: calls onSync when the upstream query data identity changes (first payload or a
// refetch with a new reference). A snapshot of the last-seen data lives in useState and the seed runs during
// render, so inputs reflect new data in the same render; useEffect+setState would seed after commit.
// The snapshot starts as `undefined`, not `data`: otherwise a cached-first render (remount within gcTime)
// has `data === snapshot`, onSync never fires, and consumers gating render on it stick forever (#527).
export function useSyncFromQuery<T>(data: T | undefined, onSync: (d: T, previous: T | undefined) => void): void {
  const [snapshot, setSnapshot] = useState<T | undefined>(undefined);
  if (data !== snapshot) {
    setSnapshot(data);
    if (data !== undefined) {
      onSync(data, snapshot);
    }
  }
}

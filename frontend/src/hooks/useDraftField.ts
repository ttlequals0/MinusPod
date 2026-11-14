import { useUnsavedChanges } from './useUnsavedChanges';
import { useState } from 'react';
import { useSyncFromQuery } from './useSyncFromQuery';

export interface DraftField {
  value: string;
  setValue: (v: string) => void;
  dirty: boolean;
  // Re-baselines right after a blur commit (or a reset to the server value),
  // so dirty clears immediately instead of waiting on the next refetch.
  markClean: (v: string) => void;
}

// Pairs a draft with the last server-matching baseline so a background refetch cannot clobber an in-progress
// edit. Baseline is state, not a ref, so `dirty` tracks the render; `normalize` affects only the compare.
// Reseeds from `source` (via useSyncFromQuery) when its identity changes, unless the draft is dirty.
export function useDraftField<T>(
  source: T,
  selector: (source: T) => string,
  normalize: (v: string) => string = (v) => v,
  // False for a disabled field: its draft must not block navigation.
  active = true,
): DraftField {
  const initial = selector(source);
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const dirty = normalize(value) !== normalize(baseline);
  useUnsavedChanges(dirty && active);

  useSyncFromQuery(source, (s) => {
    if (dirty) return;
    const next = selector(s);
    setBaseline(next);
    setValue(next);
  });

  const markClean = (v: string) => {
    setBaseline(v);
    setValue(v);
  };

  return { value, setValue, dirty, markClean };
}

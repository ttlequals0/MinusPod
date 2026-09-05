import { useState } from 'react';

export interface DraftField {
  value: string;
  setValue: (v: string) => void;
  dirty: boolean;
  // Reseeds from the server unless the draft has an unsaved edit (dirty).
  sync: (serverValue: string) => void;
  // Re-baselines right after a blur commit (or a reset to the server value),
  // so dirty clears immediately instead of waiting on the next refetch.
  markClean: (v: string) => void;
}

// Pairs a locally-typed draft string with the last value known to match the
// server, so a background refetch (staleTime elapsing, or any other field's
// save invalidating the query) cannot clobber an in-progress edit. Baseline
// is state, not a ref, so `dirty` stays consistent with the render it's read in.
export function useDraftField(initial: string): DraftField {
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const dirty = value !== baseline;

  const sync = (serverValue: string) => {
    if (dirty) return;
    setBaseline(serverValue);
    setValue(serverValue);
  };

  const markClean = (v: string) => {
    setBaseline(v);
    setValue(v);
  };

  return { value, setValue, dirty, sync, markClean };
}

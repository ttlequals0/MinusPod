import { useState } from 'react';
import type { UIEvent } from 'react';

// Rows rendered before the reader scrolls, and how many more each time they
// reach the end. Shared by RunLogViewer and TranscriptViewer, both of which
// can render thousands of rows.
const PAGE = 300;

export function usePagedList(total: number) {
  const [shown, setShown] = useState(PAGE);

  const reset = () => setShown(PAGE);

  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 400) {
      setShown((n) => (n < total ? n + PAGE : n));
    }
  };

  return { shown, reset, onScroll };
}

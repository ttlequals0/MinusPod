import { useEffect, useState, type RefObject } from 'react';

// Filters the CollapsibleSections under `regionRef` by a substring scan of each
// `[data-search-key]` card. matchKeys is null when no search is active.
export function useSectionSearch(regionRef: RefObject<HTMLElement | null>) {
  const [query, setQuery] = useState('');
  // Computed in the event handler (the lint forbids ref reads in render and
  // setState in effects); hidden sections keep their textContent.
  const [matchKeys, setMatchKeys] = useState<Set<string> | null>(null);

  const run = (q: string) => {
    setQuery(q);
    const norm = q.trim().toLowerCase();
    if (!norm) {
      setMatchKeys(null);
      return;
    }
    const matches = new Set<string>();
    regionRef.current?.querySelectorAll<HTMLElement>('[data-search-key]').forEach((el) => {
      if ((el.textContent ?? '').toLowerCase().includes(norm)) {
        const key = el.getAttribute('data-search-key');
        if (key) matches.add(key);
      }
    });
    setMatchKeys(matches);
  };

  const clear = () => run('');

  // Paints matches with the CSS Custom Highlight API after the filter commit, so
  // ranges point at the expanded sections; offsetParent skips hidden cards.
  useEffect(() => {
    if (typeof CSS === 'undefined' || !('highlights' in CSS)) return;
    const norm = query.trim().toLowerCase();
    const region = regionRef.current;
    if (!norm || !region) {
      CSS.highlights.delete('settings-search');
      return;
    }
    const ranges: Range[] = [];
    const walker = document.createTreeWalker(region, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.nodeValue && n.parentElement?.offsetParent
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT,
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const hay = n.nodeValue!.toLowerCase();
      for (let i = hay.indexOf(norm); i !== -1; i = hay.indexOf(norm, i + norm.length)) {
        const r = document.createRange();
        r.setStart(n, i);
        r.setEnd(n, i + norm.length);
        ranges.push(r);
      }
    }
    CSS.highlights.set('settings-search', new Highlight(...ranges));
    return () => { CSS.highlights.delete('settings-search'); };
  }, [query, matchKeys, regionRef]);

  return { query, matchKeys, run, clear };
}

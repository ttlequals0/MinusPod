import { useEffect, useRef } from 'react';
import { useNavigate, Link } from 'react-router';
import { modalBackdrop, modalPanel, useEscape, useFocusTrap } from './Modal';
import SearchResults from './SearchResults';
import type { SearchResultRow } from './SearchResults';
import { useUnifiedSearch } from '../hooks/useUnifiedSearch';

interface Props {
  // Character that opened the palette, so the first keystroke is not lost.
  // null means the palette is closed.
  seed: string | null;
  onClose: () => void;
}

const EDITABLE = 'input,textarea,select,[contenteditable="true"]';

// Global open trigger: a printable key, "/" or Ctrl/Cmd+K, outside editable fields and dialogs.
export function useQuickSearchHotkey(onOpen: (seed: string) => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented) return;
      const palette = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
      const printable = !e.metaKey && !e.ctrlKey && !e.altKey && e.key.length === 1 && e.key !== ' ';
      if (!palette && !printable) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest(EDITABLE) || document.querySelector('[role="dialog"]')) return;
      e.preventDefault();
      onOpen(palette || e.key === '/' ? '' : e.key);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onOpen]);
}

function Palette({ seed, onClose }: { seed: string; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { query, setQuery, debounced, ready, results, rows, current, setActive, onKeyDown } = useUnifiedSearch(seed);

  useEscape(onClose);
  useFocusTrap(panelRef);

  const go = (row: SearchResultRow) => { onClose(); navigate(row.to); };

  return (
    <div className={`${modalBackdrop} items-start pt-[12vh]`} onMouseDown={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Quick search"
        className={`${modalPanel} w-full max-w-xl overflow-hidden`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="relative border-b border-border">
          <svg className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            role="combobox"
            aria-expanded={rows.length > 0}
            aria-controls="quick-search-results"
            aria-activedescendant={rows[current]?.key}
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => onKeyDown(e, go)}
            placeholder="Search shows, episodes and transcripts"
            className="w-full bg-transparent py-3 pl-12 pr-4 text-base text-foreground placeholder:text-muted-foreground outline-hidden"
          />
        </div>
        <ul id="quick-search-results" role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto py-1">
          <SearchResults results={results} activeIndex={current} onHover={setActive} onSelect={go} />
          {!ready && (
            <li role="presentation" className="px-4 py-3 text-sm text-muted-foreground">Type two or more characters to search shows, episodes and transcripts.</li>
          )}
          {ready && rows.length === 0 && (
            <li role="presentation" className="px-4 py-3 text-sm text-muted-foreground">No shows, episodes or transcripts match.</li>
          )}
        </ul>
        <div className="border-t border-border bg-secondary/50 px-4 py-2 text-sm">
          <Link
            to={ready ? `/search?q=${encodeURIComponent(debounced)}` : '/search'}
            onClick={onClose}
            className="text-primary hover:underline"
          >
            {ready ? 'Advanced search' : 'Open full search'}
          </Link>
        </div>
      </div>
    </div>
  );
}

// Mounting the palette only while open scopes its state and focus trap to one session.
function QuickSearch({ seed, onClose }: Props) {
  if (seed === null) return null;
  return <Palette seed={seed} onClose={onClose} />;
}

export default QuickSearch;

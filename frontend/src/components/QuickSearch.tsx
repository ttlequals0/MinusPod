import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, Link } from 'react-router';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { quickSearch } from '../api/quickSearch';
import { modalBackdrop, modalPanel, useEscape, useFocusTrap } from './Modal';
import { EPISODE_STATUS_COLORS, EPISODE_STATUS_LABELS } from '../utils/episodeStatus';

interface Props {
  open: boolean;
  // Character that opened the palette, so the first keystroke is not lost.
  seed: string;
  onClose: () => void;
}

interface Row { key: string; group: 'Feeds' | 'Episodes'; label: string; sub?: string; status?: string; to: string }

const EDITABLE = 'input,textarea,select,[contenteditable="true"]';
const MIN_QUERY = 2;

// Global open trigger: a printable key, "/" or Ctrl/Cmd+K, outside editable fields and dialogs.
export function useQuickSearchHotkey(onOpen: (seed: string) => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest(EDITABLE) || document.querySelector('[role="dialog"]')) return;
      const palette = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
      const printable = !e.metaKey && !e.ctrlKey && !e.altKey && e.key.length === 1 && e.key !== ' ';
      if (!palette && !printable) return;
      e.preventDefault();
      onOpen(palette || e.key === '/' ? '' : e.key);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onOpen]);
}

function Palette({ seed, onClose }: Omit<Props, 'open'>) {
  const [q, setQ] = useState(seed);
  const [debounced, setDebounced] = useState(seed);
  const [active, setActive] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEscape(onClose);
  useFocusTrap(panelRef);

  const ready = debounced.trim().length >= MIN_QUERY;
  const { data } = useQuery({
    queryKey: ['quickSearch', debounced],
    queryFn: ({ signal }) => quickSearch(debounced, signal),
    enabled: ready,
    placeholderData: keepPreviousData,
  });

  // Guard on ready: keepPreviousData would otherwise show stale rows under a too-short query.
  const rows = useMemo<Row[]>(() => !ready || !data ? [] : [
    ...data.feeds.map((f) => ({
      key: `qs-f-${f.slug}`, group: 'Feeds' as const, label: f.title, to: `/feeds/${f.slug}`,
    })),
    ...data.episodes.map((e) => ({
      key: `qs-e-${e.feedSlug}-${e.episodeId}`, group: 'Episodes' as const, label: e.title,
      sub: e.feedTitle, status: e.status, to: `/feeds/${e.feedSlug}/episodes/${e.episodeId}`,
    })),
  ], [data, ready]);

  useEffect(() => {
    const id = rows[active]?.key;
    if (id) document.getElementById(id)?.scrollIntoView?.({ block: 'nearest' });
  }, [active, rows]);

  const go = (row: Row) => { onClose(); navigate(row.to); };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, rows.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && rows[active]) { e.preventDefault(); go(rows[active]); }
  };

  let lastGroup: Row['group'] | null = null;
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
            aria-activedescendant={rows[active]?.key}
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="Jump to a feed or episode"
            className="w-full bg-transparent py-3 pl-12 pr-4 text-base text-foreground placeholder:text-muted-foreground outline-hidden"
          />
        </div>
        <ul id="quick-search-results" role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto py-1">
          {rows.map((row, i) => {
            const header = row.group !== lastGroup;
            lastGroup = row.group;
            return [
              header && (
                <li key={row.group} role="presentation" className="px-4 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                  {row.group}
                </li>
              ),
              <li
                key={row.key}
                id={row.key}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(row)}
                className={`cursor-pointer px-4 py-2 ${i === active ? 'bg-accent text-accent-foreground' : ''}`}
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm">{row.label}</span>
                  {row.status && (
                    <span className={`whitespace-nowrap rounded px-1.5 py-0.5 text-xs ${EPISODE_STATUS_COLORS[row.status] || 'bg-muted text-muted-foreground'}`}>
                      {EPISODE_STATUS_LABELS[row.status] || row.status}
                    </span>
                  )}
                </div>
                {row.sub && <div className="truncate text-xs text-muted-foreground">{row.sub}</div>}
              </li>,
            ];
          })}
          {!ready && (
            <li className="px-4 py-3 text-sm text-muted-foreground">Type two or more characters to match feed and episode titles.</li>
          )}
          {ready && data && rows.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted-foreground">No feed or episode titles match.</li>
          )}
        </ul>
        {ready && (
          <div className="border-t border-border bg-secondary/50 px-4 py-2 text-sm">
            <Link to={`/search?q=${encodeURIComponent(debounced)}`} onClick={onClose} className="text-primary hover:underline">
              Search transcripts for "{debounced}"
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

// Mounting the palette only while open scopes its state and focus trap to one session.
function QuickSearch({ open, seed, onClose }: Props) {
  if (!open) return null;
  return <Palette seed={seed} onClose={onClose} />;
}

export default QuickSearch;

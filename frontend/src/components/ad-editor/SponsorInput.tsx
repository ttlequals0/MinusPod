import { useEffect, useMemo, useRef, useState } from 'react';

export interface SponsorOption {
  id: number;
  name: string;
}

interface Props {
  value: string;
  onChange: (next: string) => void;
  sponsors: SponsorOption[];
  placeholder?: string;
}

// Controlled sponsor combobox. The dropdown is rendered inside the React
// tree (not as a browser-native <datalist>), so clicking a suggestion
// inside a modal does not bubble past the modal's stopPropagation
// boundary and dismiss the parent dialog.
//
// Selection uses onMouseDown rather than onClick because the input's
// onBlur fires before onClick would land — onMouseDown commits the value
// before the focus-loss collapses the menu.
export function SponsorInput({ value, onChange, sponsors, placeholder }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Keep local input in sync if the parent updates `value` externally
  // (e.g. resolve to a canonical sponsor name post-save).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuery(value);
  }, [value]);

  // Close the menu on outside click (so a click on the modal chrome but
  // outside this input does not leave a stray dropdown).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sponsors.slice(0, 8);
    return sponsors.filter((s) => s.name.toLowerCase().includes(q)).slice(0, 8);
  }, [query, sponsors]);

  const exactMatch = filtered.some(
    (s) => s.name.toLowerCase() === query.trim().toLowerCase()
  );
  const trimmed = query.trim();

  const commit = (next: string) => {
    setQuery(next);
    onChange(next);
    setOpen(false);
  };

  return (
    <div ref={wrapperRef} className="relative">
      <input
        type="text"
        value={query}
        placeholder={placeholder ?? 'e.g. BetterHelp, Squarespace, Progressive'}
        onChange={(e) => {
          setQuery(e.target.value);
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            setOpen(false);
          }
        }}
        className="w-full px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm"
        aria-autocomplete="list"
        aria-expanded={open}
      />
      {open && (filtered.length > 0 || (!exactMatch && trimmed.length > 0)) && (
        <ul
          role="listbox"
          className="absolute z-20 mt-1 w-full max-h-64 overflow-auto rounded-md border border-border bg-card shadow-lg text-sm"
        >
          {filtered.map((s) => (
            <li
              key={s.id}
              role="option"
              aria-selected={s.name === query}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(s.name);
              }}
              className="px-3 py-1.5 cursor-pointer hover:bg-accent text-foreground"
            >
              {s.name}
            </li>
          ))}
          {!exactMatch && trimmed.length > 0 && (
            <li
              role="option"
              onMouseDown={(e) => {
                e.preventDefault();
                commit(trimmed);
              }}
              className="px-3 py-1.5 cursor-pointer hover:bg-accent text-primary border-t border-border"
            >
              + Add new: <strong className="font-semibold">{trimmed}</strong>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

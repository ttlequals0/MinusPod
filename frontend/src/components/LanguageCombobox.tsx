import { useEffect, useMemo, useRef, useState } from 'react';
import { WHISPER_LANGUAGES, labelForLanguage } from '../utils/whisperLanguages';

interface LanguageComboboxProps {
  id?: string;
  value: string;
  onChange: (code: string) => void;
  className?: string;
}

interface Option {
  code: string;
  label: string;
  sub?: string;
}

const PINNED: Option[] = [
  { code: 'en', label: 'English', sub: 'default' },
  { code: 'auto', label: 'Auto-detect', sub: 'multilingual' },
];

const CUSTOM_CODE = /^[a-z]{2,3}(-[a-z0-9]{2,4})?$/i;

function normalize(s: string) {
  return s.trim().toLowerCase();
}

function LanguageCombobox({ id, value, onChange, className = '' }: LanguageComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  const display = labelForLanguage(value);

  const options: Option[] = useMemo(() => {
    const q = normalize(query);
    const rest = WHISPER_LANGUAGES
      .filter((l) => l.code !== 'en')
      .map<Option>((l) => ({ code: l.code, label: l.name, sub: l.code }));
    const combined = [...PINNED, ...rest];
    if (!q) return combined;
    return combined.filter(
      (o) => o.code.toLowerCase().includes(q) || o.label.toLowerCase().includes(q),
    );
  }, [query]);

  const canUseCustom = options.length === 0 && CUSTOM_CODE.test(query.trim());

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  useEffect(() => {
    setHighlight(0);
  }, [query, open]);

  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.children[highlight] as HTMLElement | undefined;
    el?.scrollIntoView({ block: 'nearest' });
  }, [highlight, open]);

  const commit = (code: string) => {
    onChange(code.toLowerCase());
    setOpen(false);
    setQuery('');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      const max = options.length + (canUseCustom ? 1 : 0);
      if (max === 0) return;
      setHighlight((h) => (h + 1) % max);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
      const max = options.length + (canUseCustom ? 1 : 0);
      if (max === 0) return;
      setHighlight((h) => (h - 1 + max) % max);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (canUseCustom && highlight === options.length) {
        commit(query.trim());
      } else if (options[highlight]) {
        commit(options[highlight].code);
      }
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <input
        ref={inputRef}
        id={id}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        value={open ? query : display}
        placeholder="Type a language or code"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
        }}
        onKeyDown={onKeyDown}
        className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      />
      {open && (
        <ul
          ref={listRef}
          role="listbox"
          className="absolute z-20 mt-1 w-full max-h-72 overflow-y-auto rounded-lg border border-input bg-background shadow-lg"
        >
          {options.map((opt, i) => {
            const selected = opt.code === (value || 'en').toLowerCase();
            const active = i === highlight;
            return (
              <li
                key={opt.code}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(opt.code);
                }}
                className={`flex items-baseline justify-between px-3 py-1.5 cursor-pointer text-sm ${
                  active ? 'bg-secondary text-secondary-foreground' : 'text-foreground'
                }`}
              >
                <span className={selected ? 'font-medium' : ''}>{opt.label}</span>
                {opt.sub && <span className="ml-3 text-xs text-muted-foreground">{opt.sub}</span>}
              </li>
            );
          })}
          {canUseCustom && (
            <li
              role="option"
              aria-selected={false}
              onMouseEnter={() => setHighlight(options.length)}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(query.trim());
              }}
              className={`px-3 py-1.5 cursor-pointer text-sm ${
                highlight === options.length ? 'bg-secondary text-secondary-foreground' : 'text-foreground'
              }`}
            >
              Use "{query.trim()}" as a custom code
            </li>
          )}
          {options.length === 0 && !canUseCustom && (
            <li className="px-3 py-2 text-sm text-muted-foreground">No matches</li>
          )}
        </ul>
      )}
    </div>
  );
}

export default LanguageCombobox;

import { useEffect, useRef, useState } from 'react';

interface NumberInputProps {
  value: number;
  min: number;
  max: number;
  fallback: number;
  onCommit: (value: number) => void;
  id?: string;
  ariaLabel?: string;
  step?: number;
  parse?: (s: string) => number;
  className?: string;
  disabled?: boolean;
  /**
   * 'edit' (default) commits on every parseable keystroke, for drafts a Save
   * button later persists. 'blur' commits only on blur or Enter, for fields
   * wired straight to a write.
   */
  commitOn?: 'edit' | 'blur';
}

/**
 * Numeric settings input that can actually be cleared and retyped.
 *
 * A plain controlled `value={number}` input snaps back to the number on every
 * keystroke (so clearing a 0 is impossible) and `type=number` does not normalize
 * leading zeros, which produces "012" / "120". This keeps a local text buffer so
 * the field can be empty while editing, commits a clamped number as soon as the
 * text parses, and normalizes (empty/invalid -> fallback, else clamp) on blur.
 */
export default function NumberInput({
  value, min, max, fallback, onCommit, id, ariaLabel, step,
  disabled = false, commitOn = 'edit',
  parse = parseFloat,
  className = 'w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring',
}: NumberInputProps) {
  const [text, setText] = useState(String(value));
  const focused = useRef(false);

  // Re-sync from the prop when it changes externally (settings load / reset),
  // but never while the user is mid-edit.
  useEffect(() => {
    if (!focused.current) setText(String(value));
  }, [value]);

  const clamp = (v: number) => Math.max(min, Math.min(max, v));

  return (
    <input
      type="number"
      id={id}
      aria-label={ariaLabel}
      value={text}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      className={className}
      onFocus={(e) => { focused.current = true; e.target.select(); }}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        if (commitOn === 'edit' && raw !== '') {
          const v = parse(raw);
          if (Number.isFinite(v)) onCommit(clamp(v));
        }
      }}
      onBlur={(e) => {
        focused.current = false;
        const raw = e.target.value;
        const parsed = raw === '' ? fallback : parse(raw);
        const final = Number.isFinite(parsed) ? clamp(parsed) : fallback;
        onCommit(final);
        setText(String(final));
      }}
    />
  );
}

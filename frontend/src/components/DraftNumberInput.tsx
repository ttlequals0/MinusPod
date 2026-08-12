import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { focusRing } from './fieldStyles';

// Blank or unparseable input reads as "no value" rather than NaN.
export function parseOptionalNumber(raw: string): number | null {
  if (raw.trim() === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export const DRAFT_NUMBER_INPUT_CLASS =
  `w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm ${focusRing} disabled:opacity-60`;

interface DraftNumberInputProps {
  value: number | null;
  fallback: number | null;
  min: number;
  max: number;
  step: number;
  placeholder?: string;
  parse: (raw: string) => number | null;
  onChange: (parsed: number | null) => void;
  className?: string;
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
  ariaInvalid?: boolean;
  onBlur?: () => void;
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void;
  autoFocus?: boolean;
}

// Numeric field whose value may be empty, meaning "inherit the default"; use
// NumberInput when the field always holds a number. Commits on every keystroke
// rather than on blur, so a typed value survives when the user never blurs.
export default function DraftNumberInput({
  value, fallback, min, max, step, placeholder, parse, onChange,
  className = DRAFT_NUMBER_INPUT_CLASS, disabled, id, ariaLabel, ariaInvalid, onBlur, onKeyDown, autoFocus,
}: DraftNumberInputProps) {
  const display = (v: number | null) => {
    if (v !== null && v !== undefined) return String(v);
    if (fallback !== null && fallback !== undefined) return String(fallback);
    return '';
  };
  const [text, setText] = useState(() => display(value));
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (inputRef.current && document.activeElement === inputRef.current) return;
    setText(display(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, fallback]);

  return (
    <input
      ref={inputRef}
      id={id}
      aria-label={ariaLabel}
      aria-invalid={ariaInvalid}
      type="number"
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(parse(e.target.value));
      }}
      onBlur={onBlur}
      onKeyDown={onKeyDown}
      autoFocus={autoFocus}
      className={className}
      disabled={disabled}
    />
  );
}

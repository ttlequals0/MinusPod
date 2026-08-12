import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { focusRing } from './fieldStyles';

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

/**
 * Numeric field whose value may be empty, meaning "inherit the default".
 *
 * Use this wherever a blank input is meaningful; use NumberInput when the field
 * always holds a number. Reports every change immediately rather than on blur,
 * so a typed value is captured even if the user never blurs, which matters on
 * mobile. Keeps a local text buffer for typing fluidity and re-syncs from the
 * source only while unfocused.
 */
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

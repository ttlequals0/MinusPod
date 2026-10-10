import { useState } from 'react';
import { getErrorMessage } from '../../api/client';
import { RemovableChip } from '../../components/RemovableChip';
import { btnOutline } from '../../components/buttonStyles';
import { inputBase, focusRing } from '../../components/fieldStyles';

interface SkipPatternsFieldProps {
  label: string;
  patterns: string[] | undefined;
  addButtonAriaLabel?: string;
  inputAriaLabel: string;
  placeholder: string;
  hint: string;
  disabled: boolean;
  onAdd: (next: string[], callbacks: { onSuccess: () => void; onError: (e: unknown) => void }) => void;
  onRemove: (next: string[], callbacks: { onError: (e: unknown) => void }) => void;
}

export function SkipPatternsField({
  label, patterns, addButtonAriaLabel, inputAriaLabel, placeholder, hint, disabled, onAdd, onRemove,
}: SkipPatternsFieldProps) {
  const [adding, setAdding] = useState(false);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);

  const addPattern = () => {
    if (disabled) return;
    const pattern = input.trim();
    if (!pattern) return;
    const current = patterns ?? [];
    if (current.includes(pattern)) {
      setInput('');
      setAdding(false);
      return;
    }
    setError(null);
    onAdd([...current, pattern], {
      onSuccess: () => {
        setInput('');
        setAdding(false);
      },
      onError: (e) => setError(getErrorMessage(e, 'Failed to add pattern')),
    });
  };

  const removePattern = (pattern: string) => {
    setError(null);
    onRemove((patterns ?? []).filter((p) => p !== pattern), {
      onError: (e) => setError(getErrorMessage(e, 'Failed to remove pattern')),
    });
  };

  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
      <span className="text-muted-foreground sm:w-32 shrink-0 sm:pt-0.5">
        {label}
      </span>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        {(patterns ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1 mb-1">
            {patterns!.map((p) => (
              <RemovableChip key={p} label={p} onRemove={() => removePattern(p)} disabled={disabled} />
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {!adding ? (
            <button
              type="button"
              aria-label={addButtonAriaLabel}
              onClick={() => setAdding(true)}
              disabled={disabled}
              className={`min-h-11 px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
            >
              + Add pattern
            </button>
          ) : (
            <>
              <input
                type="text"
                autoFocus
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addPattern();
                  }
                }}
                placeholder={placeholder}
                aria-label={inputAriaLabel}
                maxLength={200}
                disabled={disabled}
                className={`w-full min-h-11 min-w-0 sm:flex-1 sm:w-auto ${inputBase}`}
              />
              <button
                type="button"
                onClick={addPattern}
                disabled={disabled || !input.trim()}
                className={`min-h-11 px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
              >
                Add
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  setAdding(false);
                  setInput('');
                  setError(null);
                }}
                className={`min-h-11 px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {error && (
          <p role="alert" className="text-xs text-destructive">{error}</p>
        )}
        <p className="text-xs text-muted-foreground">
          {hint}
        </p>
      </div>
    </div>
  );
}

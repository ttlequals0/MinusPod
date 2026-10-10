import { useState } from 'react';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import { useDraftField } from '../../hooks/useDraftField';
import { btnPrimary, btnOutline } from '../../components/buttonStyles';
import { inputBase, focusRing } from '../../components/fieldStyles';

interface UserAgentOverrideFieldProps {
  id: string;
  label: string;
  feed: Feed;
  field: 'downloadUserAgentOverride' | 'feedUserAgentOverride';
  globalValue: string | undefined;
  hint: string;
  saveAriaLabel: string;
  useGlobalAriaLabel: string;
  errorFallback: string;
  disabled: boolean;
  onSave: (next: string | null, callbacks: { onSuccess: () => void; onError: (e: unknown) => void }) => void;
}

export function UserAgentOverrideField({
  id, label, feed, field, globalValue, hint, saveAriaLabel, useGlobalAriaLabel, errorFallback, disabled, onSave,
}: UserAgentOverrideFieldProps) {
  const draft = useDraftField(feed, (f) => f[field] ?? '', (v) => v.trim());
  const [error, setError] = useState<string | null>(null);

  const save = (value: string) => {
    const next = value.trim() || null;
    setError(null);
    onSave(next, {
      onSuccess: () => draft.markClean(next ?? ''),
      onError: (e) => setError(getErrorMessage(e, errorFallback)),
    });
  };

  return (
    <div className="flex flex-col gap-2 text-sm min-w-0">
      <label htmlFor={id} className="text-muted-foreground">{label}</label>
      <input
        id={id}
        value={draft.value}
        onChange={(e) => {
          draft.setValue(e.target.value);
          setError(null);
        }}
        placeholder={String(globalValue ?? 'Use global')}
        maxLength={512}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
        className={`w-full min-w-0 min-h-11 font-mono ${inputBase}`}
      />
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => save(draft.value)}
          disabled={!draft.dirty || disabled}
          aria-label={saveAriaLabel}
          className={`${btnPrimary} min-h-11 px-3 py-2 text-xs rounded ${focusRing}`}
        >Save</button>
        <button
          onClick={() => save('')}
          disabled={(feed[field] == null && draft.value === '') || disabled}
          aria-label={useGlobalAriaLabel}
          className={`${btnOutline} min-h-11 px-3 py-2 text-xs rounded ${focusRing}`}
        >Use global</button>
      </div>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

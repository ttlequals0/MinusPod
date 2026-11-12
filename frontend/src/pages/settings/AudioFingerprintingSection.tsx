import { useCallback, useEffect, useId, useState } from 'react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { inputBase, invalidField, labelBase } from '../../components/fieldStyles';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { formatPercent } from '../../utils/format';

export interface FingerprintState {
  enabled: boolean;
  matchStrictness: number;
  minCoverage: number;
  gapToleranceS: number;
  minAdLengthS: number;
}

type NumericKey = Exclude<keyof FingerprintState, 'enabled'>;

// Ranges and defaults mirror the server (openapi.yaml, /settings/ad-detection); the one place they live here.
export const FINGERPRINT_LIMITS: Record<NumericKey, { min: number; max: number; default: number }> = {
  matchStrictness: { min: 0.4, max: 0.95, default: 0.65 },
  minCoverage: { min: 0.1, max: 1, default: 0.5 },
  gapToleranceS: { min: 0, max: 15, default: 3.5 },
  minAdLengthS: { min: 3, max: 30, default: 5 },
};

interface FieldDef {
  key: NumericKey;
  label: string;
  step: number;
  fraction: boolean;
  help: string;
}

const FIELDS: FieldDef[] = [
  {
    key: 'matchStrictness', label: 'Match strictness', step: 0.05, fraction: true,
    help: 'Raising it requires a closer match, which means fewer false matches and more missed repeats.',
  },
  {
    key: 'minCoverage', label: 'Minimum coverage', step: 0.05, fraction: true,
    help: 'Raising it requires more of the stored ad to be found before a match counts.',
  },
  {
    key: 'gapToleranceS', label: 'Gap tolerance (s)', step: 0.5, fraction: false,
    help: 'Raising it bridges longer dropouts inside one match, such as a swapped-in sound.',
  },
  {
    key: 'minAdLengthS', label: 'Minimum ad length (s)', step: 1, fraction: false,
    help: 'Raising it skips shorter spans when fingerprints are made. Stored ads are not redone.',
  },
];

function FingerprintNumber({ def, value, onCommit, onInvalidChange }: {
  def: FieldDef;
  value: number;
  onCommit: (v: number) => void;
  onInvalidChange: (key: NumericKey, invalid: boolean) => void;
}) {
  const id = useId();
  const { min, max } = FINGERPRINT_LIMITS[def.key];
  // null follows the saved value; a string is what the person typed and has not been accepted.
  const [draft, setDraft] = useState<string | null>(null);
  const entered = draft === null ? value : draft.trim() === '' ? NaN : Number(draft);
  const invalid = !(entered >= min && entered <= max);
  const blocked = draft !== null && invalid;
  useUnsavedChanges(blocked);
  useEffect(() => {
    onInvalidChange(def.key, blocked);
    return () => onInvalidChange(def.key, false);
  }, [def.key, blocked, onInvalidChange]);

  return (
    <div>
      <label htmlFor={id} className={labelBase}>{def.label}</label>
      <div className="flex flex-wrap items-center gap-3">
        <input
          id={id}
          type="number"
          min={min}
          max={max}
          step={def.step}
          value={draft ?? String(value)}
          aria-invalid={invalid ? true : undefined}
          data-save-blocker={blocked ? '' : undefined}
          aria-describedby={invalid ? `${id}-help ${id}-error` : `${id}-help`}
          className={`${inputBase} ${invalidField} w-28`}
          onChange={(e) => {
            const raw = e.target.value;
            setDraft(raw);
            const next = raw.trim() === '' ? NaN : Number(raw);
            if (next >= min && next <= max) onCommit(next);
          }}
          onBlur={() => { if (!invalid) setDraft(null); }}
        />
        <span className="text-sm text-muted-foreground">
          {def.fraction
            ? `${formatPercent(value)} (${formatPercent(min)} to ${formatPercent(max)})`
            : `${min} to ${max}`}
        </span>
      </div>
      {invalid && (
        <p id={`${id}-error`} role="alert" className="mt-2 text-sm text-destructive">
          {`Enter a number from ${min} to ${max}.`}
        </p>
      )}
      <p id={`${id}-help`} className="mt-2 text-sm text-muted-foreground">{def.help}</p>
    </div>
  );
}

interface AudioFingerprintingSectionProps {
  fingerprint: FingerprintState;
  onChange: (next: FingerprintState) => void;
  // True while any field holds an entry that cannot be saved.
  onInvalidChange?: (invalid: boolean) => void;
}

// Fingerprint matching needs no model, so this section is never gated on the LLM provider.
function AudioFingerprintingSection({ fingerprint, onChange, onInvalidChange }: AudioFingerprintingSectionProps) {
  const [invalidKeys, setInvalidKeys] = useState<ReadonlySet<NumericKey>>(new Set());
  const trackInvalid = useCallback((key: NumericKey, invalid: boolean) => {
    setInvalidKeys((prev) => {
      if (prev.has(key) === invalid) return prev;
      const next = new Set(prev);
      if (invalid) next.add(key); else next.delete(key);
      return next;
    });
  }, []);
  useEffect(() => { onInvalidChange?.(invalidKeys.size > 0); }, [invalidKeys, onInvalidChange]);

  return (
    <CollapsibleSection
      title="Audio fingerprinting"
      subtitle="Recognizes ads you have already marked by their sound, with no model call."
    >
      <div className="space-y-6">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={fingerprint.enabled}
              onChange={(v) => onChange({ ...fingerprint, enabled: v })}
              ariaLabel="Enable audio fingerprinting"
            />
            <span className="text-sm font-medium text-foreground">Enable audio fingerprinting</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Matches new episodes against the audio of ads stored with a pattern. It works with any LLM provider, including None.
          </p>
        </div>
        {FIELDS.map((def) => (
          <FingerprintNumber
            key={def.key}
            def={def}
            value={fingerprint[def.key]}
            onCommit={(v) => onChange({ ...fingerprint, [def.key]: v })}
            onInvalidChange={trackInvalid}
          />
        ))}
      </div>
    </CollapsibleSection>
  );
}

export default AudioFingerprintingSection;

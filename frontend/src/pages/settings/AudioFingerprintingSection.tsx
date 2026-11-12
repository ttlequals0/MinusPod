import { useEffect, useId, useRef, useState } from 'react';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { inputBase, invalidField, labelBase } from '../../components/fieldStyles';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { formatPercent } from '../../utils/format';
import { limitsOf } from '../../utils/settingLimits';
import type { Settings as SettingsShape } from '../../api/types';

export interface FingerprintState {
  enabled: boolean;
  matchStrictness: number;
  minCoverage: number;
  gapToleranceS: number;
  minAdLengthS: number;
}

type NumericKey = Exclude<keyof FingerprintState, 'enabled'>;

export interface FingerprintLimits { min: number; max: number; default: number; step?: number }

// Used only when the settings response lacks the limits (an older server or demo fixture);
// the served min, max, step and default win. The openapi test pins these to openapi.yaml.
export const FINGERPRINT_FALLBACK_LIMITS: Record<NumericKey, FingerprintLimits> = {
  matchStrictness: { min: 0.65, max: 0.95, default: 0.65, step: 0.05 },
  minCoverage: { min: 0.1, max: 1, default: 0.5, step: 0.05 },
  gapToleranceS: { min: 0, max: 15, default: 3.5, step: 0.5 },
  minAdLengthS: { min: 3, max: 30, default: 5, step: 1 },
};

export const FINGERPRINT_SETTING_KEYS = {
  matchStrictness: 'fingerprintMatchStrictness',
  minCoverage: 'fingerprintMinCoverage',
  gapToleranceS: 'fingerprintGapToleranceS',
  minAdLengthS: 'fingerprintMinAdLengthS',
} as const satisfies Record<NumericKey, string>;

export function fingerprintLimits(settings: SettingsShape | undefined): Record<NumericKey, FingerprintLimits> {
  const out = {} as Record<NumericKey, FingerprintLimits>;
  for (const key of Object.keys(FINGERPRINT_SETTING_KEYS) as NumericKey[]) {
    const fallback = FINGERPRINT_FALLBACK_LIMITS[key];
    const settingKey = FINGERPRINT_SETTING_KEYS[key];
    const served = settings?.defaults?.[settingKey];
    out[key] = {
      ...limitsOf(settings?.[settingKey], fallback as { min: number; max: number; step?: number }),
      default: typeof served === 'number' ? served : fallback.default,
    };
  }
  return out;
}

// Reports (or clears, with null) a field whose entry cannot be saved, with a way to focus it.
export type FieldIssueReporter = (settingKey: string, focus: (() => void) | null) => void;

interface FieldDef {
  key: NumericKey;
  label: string;
  fraction: boolean;
  help: string;
}

const FIELDS: FieldDef[] = [
  {
    key: 'matchStrictness', label: 'Match strictness', fraction: true,
    help: 'Raising it requires a closer match, which means fewer false matches and more missed repeats.',
  },
  {
    key: 'minCoverage', label: 'Minimum coverage', fraction: true,
    help: 'Raising it requires more of the stored ad to be found before a match counts.',
  },
  {
    key: 'gapToleranceS', label: 'Gap tolerance (s)', fraction: false,
    help: 'Raising it bridges longer dropouts inside one match, such as a swapped-in sound.',
  },
  {
    key: 'minAdLengthS', label: 'Minimum ad length (s)', fraction: false,
    help: 'Raising it skips shorter spans when fingerprints are made. Stored ads are not redone.',
  },
];

function FingerprintNumber({ def, value, limits, onCommit, onFieldIssue }: {
  def: FieldDef;
  value: number;
  limits: FingerprintLimits;
  onCommit: (v: number) => void;
  onFieldIssue: FieldIssueReporter;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const { min, max, step } = limits;
  // null follows the saved value; a string is what the person typed and has not been accepted.
  const [draft, setDraft] = useState<string | null>(null);
  const entered = draft === null ? value : draft.trim() === '' ? NaN : Number(draft);
  const invalid = !(entered >= min && entered <= max);
  const blocked = draft !== null && invalid;
  useUnsavedChanges(blocked);
  const settingKey = FINGERPRINT_SETTING_KEYS[def.key];
  useEffect(() => {
    if (!blocked) return;
    onFieldIssue(settingKey, () => inputRef.current?.focus());
    return () => onFieldIssue(settingKey, null);
  }, [settingKey, blocked, onFieldIssue]);

  return (
    <div>
      <label htmlFor={id} className={labelBase}>{def.label}</label>
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          id={id}
          type="number"
          min={min}
          max={max}
          step={step}
          value={draft ?? String(value)}
          aria-invalid={invalid ? true : undefined}
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
  limits: Record<NumericKey, FingerprintLimits>;
  onChange: (next: FingerprintState) => void;
  onFieldIssue: FieldIssueReporter;
}

// Fingerprint matching needs no model, so this section is never gated on the LLM provider.
function AudioFingerprintingSection({ fingerprint, limits, onChange, onFieldIssue }: AudioFingerprintingSectionProps) {
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
            limits={limits[def.key]}
            onCommit={(v) => onChange({ ...fingerprint, [def.key]: v })}
            onFieldIssue={onFieldIssue}
          />
        ))}
      </div>
    </CollapsibleSection>
  );
}

export default AudioFingerprintingSection;

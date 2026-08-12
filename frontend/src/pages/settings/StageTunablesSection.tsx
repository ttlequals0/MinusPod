import { useMemo, useState } from 'react';
import type {
  LlmProvider,
  ReasoningLevel,
  StageTunables,
  UpdateSettingsPayload,
} from '../../api/types';
import { LLM_PROVIDERS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { btnPrimary } from '../../components/buttonStyles';
import SavedBadge from './SavedBadge';
import DraftNumberInput, { DRAFT_NUMBER_INPUT_CLASS } from '../../components/DraftNumberInput';
import { selectBase } from '../../components/fieldStyles';

interface StageTunablesSectionProps {
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  llmProvider: LlmProvider;
  onSave: (payload: UpdateSettingsPayload) => void;
  saveIsPending: boolean;
  saveIsSuccess: boolean;
  saveError: string | null;
  parallelWindows: number;
  parallelWindowsDefault: number;
  omitTemperature: boolean;
}

interface StageBlock {
  label: string;
  temperatureKey: keyof StageTunables;
  maxTokensKey: keyof StageTunables;
  budgetKey: keyof StageTunables;
  levelKey: keyof StageTunables;
  description: string;
}

const STAGES: StageBlock[] = [
  {
    label: 'Ad Detection (Pass 1)',
    temperatureKey: 'detectionTemperature',
    maxTokensKey: 'detectionMaxTokens',
    budgetKey: 'detectionReasoningBudget',
    levelKey: 'detectionReasoningLevel',
    description: 'First scan of the full transcript.',
  },
  {
    label: 'Verification (Ad Detection Pass 2)',
    temperatureKey: 'verificationTemperature',
    maxTokensKey: 'verificationMaxTokens',
    budgetKey: 'verificationReasoningBudget',
    levelKey: 'verificationReasoningLevel',
    description: 'Second scan against processed audio.',
  },
  {
    label: 'Reviewer (Pass 1 and Pass 2)',
    temperatureKey: 'reviewerTemperature',
    maxTokensKey: 'reviewerMaxTokens',
    budgetKey: 'reviewerReasoningBudget',
    levelKey: 'reviewerReasoningLevel',
    description: 'Optional confirm/reject pass on detected ads.',
  },
  {
    label: 'Chapter Boundary Detection',
    temperatureKey: 'chapterBoundaryTemperature',
    maxTokensKey: 'chapterBoundaryMaxTokens',
    budgetKey: 'chapterBoundaryReasoningBudget',
    levelKey: 'chapterBoundaryReasoningLevel',
    description: 'Finds topic transitions.',
  },
  {
    label: 'Chapter Title Generation',
    temperatureKey: 'chapterTitleTemperature',
    maxTokensKey: 'chapterTitleMaxTokens',
    budgetKey: 'chapterTitleReasoningBudget',
    levelKey: 'chapterTitleReasoningLevel',
    description: 'Writes titles for each chapter.',
  },
];

const REASONING_LEVEL_OPTIONS: { value: ReasoningLevel; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

// The 'adDetectionParallelWindows' key is not part of StageTunables (it lives on
// its own settings field) but it is edited in this section's draft.
const PARALLEL_KEY = 'adDetectionParallelWindows';

// 'omitTemperature' is likewise a standalone global setting, edited here
// because it sits right next to the per-stage temperature controls it governs.
const OMIT_TEMPERATURE_KEY = 'omitTemperature';

type DraftValue = number | string | boolean | null;
type DraftRecord = Record<string, DraftValue>;

const TUNABLE_KEYS: (keyof StageTunables)[] = [
  ...STAGES.flatMap((s) => [s.temperatureKey, s.maxTokensKey, s.budgetKey, s.levelKey]),
  'windowSizeSeconds',
  'windowOverlapSeconds',
];

// Every key this section edits, in a stable order. Used for diffing the draft
// against the server baseline and for building the save payload.
const DRAFT_KEYS: string[] = [...TUNABLE_KEYS, PARALLEL_KEY, OMIT_TEMPERATURE_KEY];

// Server truth as a flat draft. null means "not set" (resolves to env/default
// at read time); a value means an explicit override.
export function baselineFromTunables<K extends keyof StageTunables>(
  tunables: StageTunables,
  keys: readonly K[],
): { [P in K]: StageTunables[P]['value'] } {
  const b = {} as { [P in K]: StageTunables[P]['value'] };
  for (const k of keys) {
    b[k] = tunables[k]?.value ?? null;
  }
  return b;
}

// Draft state diffed against server truth. Adopts a new baseline via a
// render-phase seed only while the user has no unsaved edits, so a background
// refetch never clobbers an in-progress edit.
export function useServerDraft<K extends string, V>(
  keys: readonly K[],
  serverBaseline: Record<K, V>,
) {
  const equal = (a: Record<K, V>, b: Record<K, V>) => keys.every((k) => a[k] === b[k]);
  const [draft, setDraft] = useState(serverBaseline);
  const [seenBaseline, setSeenBaseline] = useState(serverBaseline);
  if (serverBaseline !== seenBaseline) {
    if (equal(draft, seenBaseline)) {
      setDraft(serverBaseline);
    }
    setSeenBaseline(serverBaseline);
  }

  const dirty = !equal(draft, serverBaseline);

  const setField = (key: K, value: V) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  // Only the keys the user changed, so untouched fields are never rewritten.
  // A null value clears the row to default.
  const buildPayload = (): UpdateSettingsPayload => {
    const payload: Partial<Record<K, V>> = {};
    for (const k of keys) {
      if (draft[k] !== serverBaseline[k]) payload[k] = draft[k];
    }
    return payload as UpdateSettingsPayload;
  };

  return { draft, dirty, setField, buildPayload };
}

function buildBaseline(
  tunables: StageTunables,
  parallelWindows: number,
  omitTemperature: boolean,
): DraftRecord {
  return {
    ...baselineFromTunables(tunables, TUNABLE_KEYS),
    [PARALLEL_KEY]: parallelWindows,
    [OMIT_TEMPERATURE_KEY]: omitTemperature,
  };
}

// Empty input -> null (clear to default); otherwise the parsed integer, or null
// on garbage.
export function parseIntField(raw: string): number | null {
  if (raw.trim() === '') return null;
  const v = parseInt(raw, 10);
  return Number.isFinite(v) ? v : null;
}

export function readEnvOverride(entry: StageTunables[keyof StageTunables]): string | null {
  return entry?.envOverride ?? null;
}

export function ResetButton({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Reset to default"
      className="ml-2 text-xs text-muted-foreground hover:text-foreground underline disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed"
    >
      Reset
    </button>
  );
}

// Standard field row: label + Reset + number input + help text.
export function NumberFieldRow({
  label,
  labelMuted,
  resetDisabled,
  onReset,
  value,
  fallback,
  min,
  max,
  step,
  placeholder,
  parse,
  onChange,
  disabled,
  help,
}: {
  label: string;
  labelMuted?: boolean;
  resetDisabled: boolean;
  onReset: () => void;
  value: number | null;
  fallback: number | null;
  min: number;
  max: number;
  step: number;
  placeholder?: string;
  parse: (raw: string) => number | null;
  onChange: (parsed: number | null) => void;
  disabled?: boolean;
  help: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className={`block text-xs font-medium ${labelMuted ? 'text-muted-foreground' : 'text-foreground'}`}>
          {label}
        </label>
        <ResetButton disabled={resetDisabled} onClick={onReset} />
      </div>
      <DraftNumberInput
        value={value}
        fallback={fallback}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        parse={parse}
        onChange={onChange}
        className={DRAFT_NUMBER_INPUT_CLASS}
        disabled={disabled}
      />
      <p className="mt-1 text-xs text-muted-foreground">{help}</p>
    </div>
  );
}

function StageBlockEditor({
  block,
  tunables,
  defaults,
  draft,
  llmProvider,
  omitTemperature,
  setField,
}: {
  block: StageBlock;
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  draft: DraftRecord;
  llmProvider: LlmProvider;
  omitTemperature: boolean;
  setField: (key: string, value: DraftValue) => void;
}) {
  const tempEnv = readEnvOverride(tunables[block.temperatureKey]);
  const maxEnv = readEnvOverride(tunables[block.maxTokensKey]);
  const budgetEnv = readEnvOverride(tunables[block.budgetKey]);
  const levelEnv = readEnvOverride(tunables[block.levelKey]);

  const useAnthropic = llmProvider === LLM_PROVIDERS.ANTHROPIC;

  const tempDraft = draft[block.temperatureKey] as number | null;
  const maxDraft = draft[block.maxTokensKey] as number | null;
  const budgetDraft = draft[block.budgetKey] as number | null;
  const levelDraft = draft[block.levelKey] as ReasoningLevel | null;

  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">{block.label}</h4>
        <p className="text-xs text-muted-foreground mt-0.5">{block.description}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <NumberFieldRow
          label="Temperature"
          labelMuted={omitTemperature}
          resetDisabled={tempDraft === null || omitTemperature}
          onReset={() => setField(block.temperatureKey, null)}
          value={tempDraft}
          fallback={defaults[block.temperatureKey] as number | null}
          min={0}
          max={2}
          step={0.1}
          parse={(raw) => {
            if (raw.trim() === '') return null;
            const v = parseFloat(raw);
            return Number.isFinite(v) ? v : null;
          }}
          onChange={(parsed) => setField(block.temperatureKey, parsed)}
          disabled={omitTemperature}
          help={
            omitTemperature
              ? 'Not sent: "Do not send temperature" is on.'
              : tempEnv
                ? `Default from ${tempEnv}.`
                : '0.0 = deterministic. Higher = more variation.'
          }
        />

        <NumberFieldRow
          label="Max tokens"
          resetDisabled={maxDraft === null}
          onReset={() => setField(block.maxTokensKey, null)}
          value={maxDraft}
          fallback={defaults[block.maxTokensKey] as number | null}
          min={128}
          max={32768}
          step={128}
          parse={parseIntField}
          onChange={(parsed) => setField(block.maxTokensKey, parsed)}
          help={maxEnv ? `Default from ${maxEnv}.` : 'Response cap. Too low cuts off mid-JSON.'}
        />
      </div>

      {useAnthropic ? (
        <NumberFieldRow
          label="Reasoning budget (Anthropic)"
          resetDisabled={budgetDraft === null}
          onReset={() => setField(block.budgetKey, null)}
          value={budgetDraft}
          fallback={null}
          min={1024}
          max={65536}
          step={512}
          placeholder="Leave blank to disable extended thinking"
          parse={parseIntField}
          onChange={(parsed) => setField(block.budgetKey, parsed)}
          help={
            budgetEnv
              ? `Default from ${budgetEnv}.`
              : 'Anthropic thinking budget (1024-65536). Blank = off.'
          }
        />
      ) : (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Reasoning effort
            </label>
            <ResetButton
              disabled={levelDraft === null}
              onClick={() => setField(block.levelKey, null)}
            />
          </div>
          <select
            value={levelDraft ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              setField(block.levelKey, v === '' ? null : (v as ReasoningLevel));
            }}
            className={`w-full ${selectBase}`}
          >
            <option value="">Default (provider decides)</option>
            {REASONING_LEVEL_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <p className="mt-1 text-xs text-muted-foreground">
            {levelEnv
              ? `Default from ${levelEnv}.`
              : 'How hard the model thinks. Higher = slower but better.'}
          </p>
        </div>
      )}
    </div>
  );
}

function WindowConfigBlock({
  tunables,
  defaults,
  draft,
  crossFieldError,
  setField,
}: {
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  draft: DraftRecord;
  crossFieldError: string | null;
  setField: (key: string, value: DraftValue) => void;
}) {
  const sizeEnv = readEnvOverride(tunables.windowSizeSeconds);
  const overlapEnv = readEnvOverride(tunables.windowOverlapSeconds);

  const sizeDraft = draft.windowSizeSeconds as number | null;
  const overlapDraft = draft.windowOverlapSeconds as number | null;

  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">Detection Window</h4>
        <p className="text-xs text-muted-foreground mt-0.5">
          Transcript chunk size for ad detection. Shrink for small-context local LLMs.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <NumberFieldRow
          label="Window size (seconds)"
          resetDisabled={sizeDraft === null}
          onReset={() => setField('windowSizeSeconds', null)}
          value={sizeDraft}
          fallback={defaults.windowSizeSeconds as number | null}
          min={120}
          max={1800}
          step={30}
          parse={parseIntField}
          onChange={(parsed) => setField('windowSizeSeconds', parsed)}
          help={sizeEnv ? `Default from ${sizeEnv}.` : '120 to 1800. Default 600 (10 min).'}
        />

        <NumberFieldRow
          label="Overlap (seconds)"
          resetDisabled={overlapDraft === null}
          onReset={() => setField('windowOverlapSeconds', null)}
          value={overlapDraft}
          fallback={defaults.windowOverlapSeconds as number | null}
          min={0}
          max={1770}
          step={30}
          parse={parseIntField}
          onChange={(parsed) => setField('windowOverlapSeconds', parsed)}
          help={
            overlapEnv
              ? `Default from ${overlapEnv}.`
              : 'Must be less than window size. Default 180 (3 min).'
          }
        />
      </div>

      {crossFieldError && (
        <p className="text-xs text-destructive">{crossFieldError}</p>
      )}
    </div>
  );
}

function ConcurrencyConfigBlock({
  value,
  defaultValue,
  setField,
}: {
  value: number;
  defaultValue: number;
  setField: (key: string, value: DraftValue) => void;
}) {
  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">Detection Concurrency</h4>
        <p className="text-xs text-muted-foreground mt-0.5">
          Run multiple transcript windows through the LLM at once. 1 means sequential (original
          behavior). Higher values cut detection time but raise concurrent load on your LLM provider.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <NumberFieldRow
          label="Parallel ad-detection windows"
          resetDisabled={value === defaultValue}
          onReset={() => setField(PARALLEL_KEY, defaultValue)}
          value={value}
          fallback={defaultValue}
          min={1}
          max={32}
          step={1}
          parse={(raw) => {
            if (raw.trim() === '') return defaultValue;
            const v = parseInt(raw, 10);
            if (!Number.isFinite(v)) return null;
            return Math.max(1, Math.min(32, v));
          }}
          onChange={(parsed) => {
            if (parsed === null) return;
            setField(PARALLEL_KEY, parsed);
          }}
          help={`1 to 32. Default ${defaultValue}.`}
        />
      </div>
    </div>
  );
}

function OmitTemperatureToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="border border-border rounded-lg p-3">
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={checked}
          onChange={onChange}
          ariaLabel="Do not send temperature"
        />
        <span className="text-sm font-medium text-foreground">
          Do not send temperature
        </span>
      </label>
      <p className="mt-2 text-sm text-muted-foreground ml-14">
        Some newer models reject the temperature parameter and fail the request. Turn
        this on to leave it out of every LLM call. MinusPod already skips it for models
        known to reject it.
      </p>
    </div>
  );
}

function StageTunablesSection({
  tunables,
  defaults,
  llmProvider,
  onSave,
  saveIsPending,
  saveIsSuccess,
  saveError,
  parallelWindows,
  parallelWindowsDefault,
  omitTemperature,
}: StageTunablesSectionProps) {
  const serverBaseline = useMemo(
    () => buildBaseline(tunables, parallelWindows, omitTemperature),
    [tunables, parallelWindows, omitTemperature],
  );
  const { draft, dirty, setField, buildPayload } = useServerDraft(DRAFT_KEYS, serverBaseline);

  const sizeEff = (draft.windowSizeSeconds ?? defaults.windowSizeSeconds) as number | null;
  const overlapEff = (draft.windowOverlapSeconds ?? defaults.windowOverlapSeconds) as number | null;
  const crossFieldError =
    sizeEff !== null && overlapEff !== null && overlapEff >= sizeEff
      ? 'Overlap must be less than window size.'
      : null;
  const omitTemperatureDraft = draft[OMIT_TEMPERATURE_KEY] as boolean;

  return (
    <CollapsibleSection title="LLM Tunables">
      <p className="text-sm text-muted-foreground mb-3">
        Temperature, max tokens, reasoning, detection-window geometry, and parallelism. Applies on the next episode.
      </p>
      <div className="space-y-3">
        <OmitTemperatureToggle
          checked={omitTemperatureDraft}
          onChange={(checked) => setField(OMIT_TEMPERATURE_KEY, checked)}
        />
        {STAGES.map((block) => (
          <StageBlockEditor
            key={block.label}
            block={block}
            tunables={tunables}
            defaults={defaults}
            draft={draft}
            llmProvider={llmProvider}
            omitTemperature={omitTemperatureDraft}
            setField={setField}
          />
        ))}
        <WindowConfigBlock
          tunables={tunables}
          defaults={defaults}
          draft={draft}
          crossFieldError={crossFieldError}
          setField={setField}
        />
        <ConcurrencyConfigBlock
          value={draft[PARALLEL_KEY] as number}
          defaultValue={parallelWindowsDefault}
          setField={setField}
        />

        <div className="pt-2 flex items-center">
          <button
            type="button"
            onClick={() => onSave(buildPayload())}
            disabled={!dirty || saveIsPending || !!crossFieldError}
            className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm`}
          >
            {saveIsPending ? 'Saving...' : 'Save LLM Tunables'}
          </button>
          {saveIsSuccess && !dirty && !saveError && <SavedBadge className="ml-3" />}
          {saveError && (
            <span className="ml-3 text-sm text-destructive">{saveError}</span>
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default StageTunablesSection;

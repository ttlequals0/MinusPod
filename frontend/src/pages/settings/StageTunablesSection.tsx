import { useEffect, useMemo, useRef, useState } from 'react';
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

// Every key this section edits, in a stable order. Used for diffing the draft
// against the server baseline and for building the save payload.
// Every key CHAPTER_GEOMETRY_FIELDS renders. A key missing from DRAFT_KEYS is
// skipped by draftsEqual and by buildPayload, so the field would render, accept
// input, and never save.
const CHAPTER_GEOMETRY_KEYS = [
  'chapterTargetSeconds',
  'chapterWindowSeconds',
  'chapterMaxBoundaries',
  'chapterMinDurationSeconds',
] as const;

const DRAFT_KEYS: string[] = [
  ...STAGES.flatMap((s) => [s.temperatureKey, s.maxTokensKey, s.budgetKey, s.levelKey] as string[]),
  'windowSizeSeconds',
  'windowOverlapSeconds',
  ...CHAPTER_GEOMETRY_KEYS,
  PARALLEL_KEY,
  OMIT_TEMPERATURE_KEY,
];

// Server truth as a flat draft. null means "not set" (resolves to env/default at
// read time); a value means an explicit override.
function buildBaseline(
  tunables: StageTunables,
  parallelWindows: number,
  omitTemperature: boolean,
): DraftRecord {
  const b: DraftRecord = {};
  for (const block of STAGES) {
    b[block.temperatureKey] = tunables[block.temperatureKey]?.value ?? null;
    b[block.maxTokensKey] = tunables[block.maxTokensKey]?.value ?? null;
    b[block.budgetKey] = tunables[block.budgetKey]?.value ?? null;
    b[block.levelKey] = tunables[block.levelKey]?.value ?? null;
  }
  b.windowSizeSeconds = tunables.windowSizeSeconds?.value ?? null;
  b.windowOverlapSeconds = tunables.windowOverlapSeconds?.value ?? null;
  // A key missing here has an undefined baseline, so the dirty check compares a
  // number against undefined and the section reads as permanently unsaved.
  for (const key of CHAPTER_GEOMETRY_KEYS) {
    b[key] = tunables[key]?.value ?? null;
  }
  b[PARALLEL_KEY] = parallelWindows;
  b[OMIT_TEMPERATURE_KEY] = omitTemperature;
  return b;
}

function draftsEqual(a: DraftRecord, b: DraftRecord): boolean {
  return DRAFT_KEYS.every((k) => a[k] === b[k]);
}

// Empty input -> null (clear to default); otherwise the parsed integer, or null
// on garbage. Shared by every integer field in the section.
function parseIntField(raw: string): number | null {
  if (raw.trim() === '') return null;
  const v = parseInt(raw, 10);
  return Number.isFinite(v) ? v : null;
}

// One payload of only the keys the user changed. Untouched fields are omitted so
// the backend never rewrites them or flips their is_default flag. A null value
// clears the row to default; the backend ignores keys that aren't present.
function buildPayload(draft: DraftRecord, baseline: DraftRecord): UpdateSettingsPayload {
  const payload: DraftRecord = {};
  for (const k of DRAFT_KEYS) {
    if (draft[k] !== baseline[k]) payload[k] = draft[k];
  }
  return payload as UpdateSettingsPayload;
}

function readEnvOverride(entry: StageTunables[keyof StageTunables]): string | null {
  return entry?.envOverride ?? null;
}

// Controlled number input backed by section draft state. Reports every change up
// immediately (no commit-on-blur), so a typed value is captured even if the user
// never blurs -- important on mobile. Keeps a local text string for typing
// fluidity and re-syncs from the draft when not focused.
function DraftNumberInput({
  value,
  fallback,
  min,
  max,
  step,
  placeholder,
  parse,
  onChange,
  className,
  disabled,
}: {
  value: number | null;
  fallback: number | null;
  min: number;
  max: number;
  step: number;
  placeholder?: string;
  parse: (raw: string) => number | null;
  onChange: (parsed: number | null) => void;
  className: string;
  disabled?: boolean;
}) {
  const display = (v: number | null) => {
    if (v !== null && v !== undefined) return String(v);
    if (fallback !== null && fallback !== undefined) return String(fallback);
    return '';
  };
  const [text, setText] = useState(() => display(value));
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Re-sync from the draft (other tab, save refetch, reset) ONLY when not
  // actively editing, so a background update never clobbers in-progress text.
  useEffect(() => {
    if (inputRef.current && document.activeElement === inputRef.current) {
      return;
    }
    setText(display(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, fallback]);

  return (
    <input
      ref={inputRef}
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
      className={className}
      disabled={disabled}
    />
  );
}

function ResetButton({
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
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className={`block text-xs font-medium ${omitTemperature ? 'text-muted-foreground' : 'text-foreground'}`}>
              Temperature
            </label>
            <ResetButton
              disabled={tempDraft === null || omitTemperature}
              onClick={() => setField(block.temperatureKey, null)}
            />
          </div>
          <DraftNumberInput
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
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
            disabled={omitTemperature}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {omitTemperature
              ? 'Not sent: "Do not send temperature" is on.'
              : tempEnv
                ? `Default from ${tempEnv}.`
                : '0.0 = deterministic. Higher = more variation.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Max tokens
            </label>
            <ResetButton
              disabled={maxDraft === null}
              onClick={() => setField(block.maxTokensKey, null)}
            />
          </div>
          <DraftNumberInput
            value={maxDraft}
            fallback={defaults[block.maxTokensKey] as number | null}
            min={128}
            max={32768}
            step={128}
            parse={parseIntField}
            onChange={(parsed) => setField(block.maxTokensKey, parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {maxEnv
              ? `Default from ${maxEnv}.`
              : 'Response cap. Too low cuts off mid-JSON.'}
          </p>
        </div>
      </div>

      <div>
        {useAnthropic ? (
          <>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-xs font-medium text-foreground">
                Reasoning budget (Anthropic)
              </label>
              <ResetButton
                disabled={budgetDraft === null}
                onClick={() => setField(block.budgetKey, null)}
              />
            </div>
            <DraftNumberInput
              value={budgetDraft}
              fallback={null}
              min={1024}
              max={65536}
              step={512}
                placeholder="Leave blank to disable extended thinking"
              parse={parseIntField}
              onChange={(parsed) => setField(block.budgetKey, parsed)}
              className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              {budgetEnv
                ? `Default from ${budgetEnv}.`
                : 'Anthropic thinking budget (1024-65536). Blank = off.'}
            </p>
          </>
        ) : (
          <>
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
              className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
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
          </>
        )}
      </div>
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
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Window size (seconds)
            </label>
            <ResetButton
              disabled={sizeDraft === null}
              onClick={() => setField('windowSizeSeconds', null)}
            />
          </div>
          <DraftNumberInput
            value={sizeDraft}
            fallback={defaults.windowSizeSeconds as number | null}
            min={120}
            max={1800}
            step={30}
            parse={parseIntField}
            onChange={(parsed) => setField('windowSizeSeconds', parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {sizeEnv
              ? `Default from ${sizeEnv}.`
              : '120 to 1800. Default 600 (10 min).'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Overlap (seconds)
            </label>
            <ResetButton
              disabled={overlapDraft === null}
              onClick={() => setField('windowOverlapSeconds', null)}
            />
          </div>
          <DraftNumberInput
            value={overlapDraft}
            fallback={defaults.windowOverlapSeconds as number | null}
            min={0}
            max={1770}
            step={30}
            parse={parseIntField}
            onChange={(parsed) => setField('windowOverlapSeconds', parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {overlapEnv
              ? `Default from ${overlapEnv}.`
              : 'Must be less than window size. Default 180 (3 min).'}
          </p>
        </div>
      </div>

      {crossFieldError && (
        <p className="text-xs text-destructive">{crossFieldError}</p>
      )}
    </div>
  );
}


// Density, not sampling: these are durations and a count, so they do not fit
// StageBlock's temperature/maxTokens/budget/level shape and render as their own
// group beside Chapter Boundary Detection, the way the window geometry does.
const CHAPTER_GEOMETRY_FIELDS: Array<{
  key: typeof CHAPTER_GEOMETRY_KEYS[number];
  label: string;
  min: number;
  max: number;
  step: number;
  help: string;
}> = [
  {
    key: 'chapterTargetSeconds', label: 'Target chapter length (seconds)',
    min: 120, max: 3600, step: 30,
    help: '120 to 3600. Default 600 (10 min). Lower means more chapters.',
  },
  {
    key: 'chapterWindowSeconds', label: 'Transcript window (seconds)',
    min: 600, max: 10800, step: 300,
    help: '600 to 10800. Default 2700 (45 min). One LLM call per window.',
  },
  {
    key: 'chapterMaxBoundaries', label: 'Maximum chapters',
    min: 1, max: 200, step: 1,
    help: '1 to 200. Default 40. Was hardcoded to 6 before 2.82.0.',
  },
  {
    key: 'chapterMinDurationSeconds', label: 'Shortest chapter (seconds)',
    min: 30, max: 900, step: 15,
    help: '30 to 900. Default 180 (3 min). Shorter chapters merge into the previous one.',
  },
];

function ChapterGeometryBlock({
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
  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">Chapter Density</h4>
        <p className="text-xs text-muted-foreground mt-0.5">
          How many chapters a long episode gets, and how much transcript each
          detection call sees. Applies on the next episode.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {CHAPTER_GEOMETRY_FIELDS.map((field) => {
          const env = readEnvOverride(tunables[field.key]);
          const value = draft[field.key] as number | null;
          return (
            <div key={field.key}>
              <div className="flex items-center justify-between mb-1">
                <label className="block text-xs font-medium text-foreground">
                  {field.label}
                </label>
                <ResetButton
                  disabled={value === null}
                  onClick={() => setField(field.key, null)}
                />
              </div>
              <DraftNumberInput
                value={value}
                fallback={defaults[field.key] as number | null}
                min={field.min}
                max={field.max}
                step={field.step}
                parse={parseIntField}
                onChange={(parsed) => setField(field.key, parsed)}
                className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                {env ? `Default from ${env}.` : field.help}
              </p>
            </div>
          );
        })}
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
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Parallel ad-detection windows
            </label>
            <ResetButton
              disabled={value === defaultValue}
              onClick={() => setField(PARALLEL_KEY, defaultValue)}
            />
          </div>
          <DraftNumberInput
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
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            1 to 32. Default {defaultValue}.
          </p>
        </div>
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
  const [draft, setDraft] = useState<DraftRecord>(serverBaseline);

  // Render-phase seed from server truth (same pattern as the useSyncFromQuery
  // hook): when the baseline identity changes -- initial load, save refetch, an
  // external edit -- adopt it only if the user has no unsaved edits relative to
  // the previously-seen baseline. While dirty, keep the local draft so a
  // background refetch never clobbers an in-progress edit.
  const [seenBaseline, setSeenBaseline] = useState(serverBaseline);
  if (serverBaseline !== seenBaseline) {
    if (draftsEqual(draft, seenBaseline)) {
      setDraft(serverBaseline);
    }
    setSeenBaseline(serverBaseline);
  }

  const dirty = !draftsEqual(draft, serverBaseline);

  const setField = (key: string, value: DraftValue) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  const sizeEff = (draft.windowSizeSeconds ?? defaults.windowSizeSeconds) as number | null;
  const overlapEff = (draft.windowOverlapSeconds ?? defaults.windowOverlapSeconds) as number | null;
  const crossFieldError =
    sizeEff !== null && overlapEff !== null && overlapEff >= sizeEff
      ? 'Overlap must be less than window size.'
      : null;
  const chapterEff = (key: typeof CHAPTER_GEOMETRY_KEYS[number]) =>
    (draft[key] ?? defaults[key]) as number | null;
  const chapterTargetEff = chapterEff('chapterTargetSeconds');
  const chapterWindowEff = chapterEff('chapterWindowSeconds');
  const chapterMinEff = chapterEff('chapterMinDurationSeconds');
  let chapterError: string | null = null;
  if (chapterTargetEff !== null && chapterWindowEff !== null
      && chapterTargetEff > chapterWindowEff) {
    chapterError = 'Target chapter length must not exceed the transcript window.';
  } else if (chapterMinEff !== null && chapterTargetEff !== null
      && chapterMinEff > chapterTargetEff) {
    chapterError = 'Shortest chapter must not exceed the target chapter length.';
  }
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
        <ChapterGeometryBlock
          tunables={tunables}
          defaults={defaults}
          draft={draft}
          crossFieldError={chapterError}
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
            onClick={() => onSave(buildPayload(draft, serverBaseline))}
            disabled={!dirty || saveIsPending || !!crossFieldError || !!chapterError}
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

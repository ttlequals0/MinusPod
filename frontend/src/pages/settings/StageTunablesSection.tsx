import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  LlmProvider,
  ReasoningLevel,
  StageTunables,
  UpdateSettingsPayload,
} from '../../api/types';
import { LLM_PROVIDERS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';

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

type DraftValue = number | string | null;
type DraftRecord = Record<string, DraftValue>;

// Every key this section edits, in a stable order. Used for diffing the draft
// against the server baseline and for building the save payload.
const DRAFT_KEYS: string[] = [
  ...STAGES.flatMap((s) => [s.temperatureKey, s.maxTokensKey, s.budgetKey, s.levelKey] as string[]),
  'windowSizeSeconds',
  'windowOverlapSeconds',
  PARALLEL_KEY,
];

// Server truth as a flat draft. null means "not set" (resolves to env/default at
// read time); a value means an explicit override.
function buildBaseline(tunables: StageTunables, parallelWindows: number): DraftRecord {
  const b: DraftRecord = {};
  for (const block of STAGES) {
    b[block.temperatureKey] = tunables[block.temperatureKey]?.value ?? null;
    b[block.maxTokensKey] = tunables[block.maxTokensKey]?.value ?? null;
    b[block.budgetKey] = tunables[block.budgetKey]?.value ?? null;
    b[block.levelKey] = tunables[block.levelKey]?.value ?? null;
  }
  b.windowSizeSeconds = tunables.windowSizeSeconds?.value ?? null;
  b.windowOverlapSeconds = tunables.windowOverlapSeconds?.value ?? null;
  b[PARALLEL_KEY] = parallelWindows;
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
  disabled,
  placeholder,
  parse,
  onChange,
  className,
}: {
  value: number | null;
  fallback: number | null;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  placeholder?: string;
  parse: (raw: string) => number | null;
  onChange: (parsed: number | null) => void;
  className: string;
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
      disabled={disabled}
      onChange={(e) => {
        setText(e.target.value);
        onChange(parse(e.target.value));
      }}
      className={className}
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
  setField,
}: {
  block: StageBlock;
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  draft: DraftRecord;
  llmProvider: LlmProvider;
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
            <label className="block text-xs font-medium text-foreground">
              Temperature
            </label>
            <ResetButton
              disabled={!!tempEnv || tempDraft === null}
              onClick={() => setField(block.temperatureKey, null)}
            />
          </div>
          <DraftNumberInput
            value={tempDraft}
            fallback={defaults[block.temperatureKey] as number | null}
            min={0}
            max={2}
            step={0.1}
            disabled={!!tempEnv}
            parse={(raw) => {
              if (raw.trim() === '') return null;
              const v = parseFloat(raw);
              return Number.isFinite(v) ? v : null;
            }}
            onChange={(parsed) => setField(block.temperatureKey, parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {tempEnv
              ? `Set by ${tempEnv}; edit your environment to change.`
              : '0.0 = deterministic. Higher = more variation.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Max tokens
            </label>
            <ResetButton
              disabled={!!maxEnv || maxDraft === null}
              onClick={() => setField(block.maxTokensKey, null)}
            />
          </div>
          <DraftNumberInput
            value={maxDraft}
            fallback={defaults[block.maxTokensKey] as number | null}
            min={128}
            max={32768}
            step={128}
            disabled={!!maxEnv}
            parse={parseIntField}
            onChange={(parsed) => setField(block.maxTokensKey, parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {maxEnv
              ? `Set by ${maxEnv}; edit your environment to change.`
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
                disabled={!!budgetEnv || budgetDraft === null}
                onClick={() => setField(block.budgetKey, null)}
              />
            </div>
            <DraftNumberInput
              value={budgetDraft}
              fallback={null}
              min={1024}
              max={65536}
              step={512}
              disabled={!!budgetEnv}
              placeholder="Leave blank to disable extended thinking"
              parse={parseIntField}
              onChange={(parsed) => setField(block.budgetKey, parsed)}
              className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              {budgetEnv
                ? `Set by ${budgetEnv}; edit your environment to change.`
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
                disabled={!!levelEnv || levelDraft === null}
                onClick={() => setField(block.levelKey, null)}
              />
            </div>
            <select
              value={levelDraft ?? ''}
              disabled={!!levelEnv}
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
                ? `Set by ${levelEnv}; edit your environment to change.`
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
              disabled={!!sizeEnv || sizeDraft === null}
              onClick={() => setField('windowSizeSeconds', null)}
            />
          </div>
          <DraftNumberInput
            value={sizeDraft}
            fallback={defaults.windowSizeSeconds as number | null}
            min={120}
            max={1800}
            step={30}
            disabled={!!sizeEnv}
            parse={parseIntField}
            onChange={(parsed) => setField('windowSizeSeconds', parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {sizeEnv
              ? `Set by ${sizeEnv}; edit your environment to change.`
              : '120 to 1800. Default 600 (10 min).'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-foreground">
              Overlap (seconds)
            </label>
            <ResetButton
              disabled={!!overlapEnv || overlapDraft === null}
              onClick={() => setField('windowOverlapSeconds', null)}
            />
          </div>
          <DraftNumberInput
            value={overlapDraft}
            fallback={defaults.windowOverlapSeconds as number | null}
            min={0}
            max={1770}
            step={30}
            disabled={!!overlapEnv}
            parse={parseIntField}
            onChange={(parsed) => setField('windowOverlapSeconds', parsed)}
            className="w-full px-2 py-1 rounded border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {overlapEnv
              ? `Set by ${overlapEnv}; edit your environment to change.`
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
            disabled={false}
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
}: StageTunablesSectionProps) {
  const serverBaseline = useMemo(
    () => buildBaseline(tunables, parallelWindows),
    [tunables, parallelWindows],
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

  return (
    <CollapsibleSection title="LLM Tunables">
      <p className="text-sm text-muted-foreground mb-3">
        Temperature, max tokens, reasoning, detection-window geometry, and parallelism. Applies on the next episode.
      </p>
      <div className="space-y-3">
        {STAGES.map((block) => (
          <StageBlockEditor
            key={block.label}
            block={block}
            tunables={tunables}
            defaults={defaults}
            draft={draft}
            llmProvider={llmProvider}
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
            onClick={() => onSave(buildPayload(draft, serverBaseline))}
            disabled={!dirty || saveIsPending || !!crossFieldError}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm"
          >
            {saveIsPending ? 'Saving...' : 'Save LLM Tunables'}
          </button>
          {saveIsSuccess && !dirty && !saveError && (
            <span className="ml-3 text-sm text-green-600 dark:text-green-400">Saved</span>
          )}
          {saveError && (
            <span className="ml-3 text-sm text-destructive">{saveError}</span>
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default StageTunablesSection;

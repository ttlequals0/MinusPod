import { useEffect, useRef, useState } from 'react';
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
  onUpdate: (payload: UpdateSettingsPayload) => void;
  parallelWindows: number;
  parallelWindowsIsDefault: boolean;
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

function readEnvOverride(entry: StageTunables[keyof StageTunables]): string | null {
  return entry?.envOverride ?? null;
}

// Number inputs save on commit (blur or Enter) instead of every keystroke.
// Typing "4096" should fire one mutation, not four.
function NumberCommitInput({
  value,
  min,
  max,
  step,
  disabled,
  placeholder,
  parse,
  onCommit,
  className,
}: {
  value: number | null;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  placeholder?: string;
  parse: (raw: string) => number | null;
  onCommit: (parsed: number | null) => void;
  className: string;
}) {
  const initial = value === null || value === undefined ? '' : String(value);
  const [draft, setDraft] = useState(initial);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Re-sync from upstream (other tab, mutation success refetch) ONLY when
  // we're not actively editing -- otherwise a background refetch from
  // TanStack Query clobbers the user's in-progress draft.
  useEffect(() => {
    if (inputRef.current && document.activeElement === inputRef.current) {
      return;
    }
    setDraft(value === null || value === undefined ? '' : String(value));
  }, [value]);

  const commit = () => {
    const parsed = parse(draft);
    const current = value ?? null;
    if (parsed === current) return;
    onCommit(parsed);
  };

  return (
    <input
      ref={inputRef}
      type="number"
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          (e.target as HTMLInputElement).blur();
        }
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
  llmProvider,
  onUpdate,
}: {
  block: StageBlock;
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  llmProvider: LlmProvider;
  onUpdate: (payload: UpdateSettingsPayload) => void;
}) {
  const tempEntry = tunables[block.temperatureKey];
  const maxEntry = tunables[block.maxTokensKey];
  const budgetEntry = tunables[block.budgetKey];
  const levelEntry = tunables[block.levelKey];

  const tempEnv = readEnvOverride(tempEntry);
  const maxEnv = readEnvOverride(maxEntry);
  const budgetEnv = readEnvOverride(budgetEntry);
  const levelEnv = readEnvOverride(levelEntry);

  const useAnthropic = llmProvider === LLM_PROVIDERS.ANTHROPIC;

  const tempValue = (tempEntry?.value as number | null) ?? (defaults[block.temperatureKey] as number);
  const maxValue = (maxEntry?.value as number | null) ?? (defaults[block.maxTokensKey] as number);
  const budgetValue = (budgetEntry?.value as number | null) ?? null;
  const levelValue = (levelEntry?.value as ReasoningLevel | null) ?? null;

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
              disabled={!!tempEnv || tempEntry?.isDefault !== false}
              onClick={() => onUpdate({ [block.temperatureKey]: defaults[block.temperatureKey] as number } as UpdateSettingsPayload)}
            />
          </div>
          <NumberCommitInput
            value={tempValue}
            min={0}
            max={2}
            step={0.1}
            disabled={!!tempEnv}
            parse={(raw) => {
              const v = parseFloat(raw);
              return Number.isFinite(v) ? v : null;
            }}
            onCommit={(parsed) => {
              if (parsed === null) return;
              onUpdate({ [block.temperatureKey]: parsed } as UpdateSettingsPayload);
            }}
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
              disabled={!!maxEnv || maxEntry?.isDefault !== false}
              onClick={() => onUpdate({ [block.maxTokensKey]: defaults[block.maxTokensKey] as number } as UpdateSettingsPayload)}
            />
          </div>
          <NumberCommitInput
            value={maxValue}
            min={128}
            max={32768}
            step={128}
            disabled={!!maxEnv}
            parse={(raw) => {
              const v = parseInt(raw, 10);
              return Number.isFinite(v) ? v : null;
            }}
            onCommit={(parsed) => {
              if (parsed === null) return;
              onUpdate({ [block.maxTokensKey]: parsed } as UpdateSettingsPayload);
            }}
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
                disabled={!!budgetEnv || budgetEntry?.isDefault !== false}
                onClick={() => onUpdate({ [block.budgetKey]: null } as UpdateSettingsPayload)}
              />
            </div>
            <NumberCommitInput
              value={budgetValue}
              min={1024}
              max={65536}
              step={512}
              disabled={!!budgetEnv}
              placeholder="Leave blank to disable extended thinking"
              parse={(raw) => {
                if (raw === '') return null;
                const v = parseInt(raw, 10);
                return Number.isFinite(v) ? v : null;
              }}
              onCommit={(parsed) => {
                onUpdate({ [block.budgetKey]: parsed } as UpdateSettingsPayload);
              }}
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
                disabled={!!levelEnv || levelEntry?.isDefault !== false}
                onClick={() => onUpdate({ [block.levelKey]: null } as UpdateSettingsPayload)}
              />
            </div>
            <select
              value={levelValue ?? ''}
              disabled={!!levelEnv}
              onChange={(e) => {
                const v = e.target.value;
                onUpdate({ [block.levelKey]: v === '' ? null : (v as ReasoningLevel) } as UpdateSettingsPayload);
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
  onUpdate,
}: {
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  onUpdate: (payload: UpdateSettingsPayload) => void;
}) {
  const sizeEntry = tunables.windowSizeSeconds;
  const overlapEntry = tunables.windowOverlapSeconds;
  const sizeEnv = readEnvOverride(sizeEntry);
  const overlapEnv = readEnvOverride(overlapEntry);

  const sizeValue = (sizeEntry?.value as number | null) ?? (defaults.windowSizeSeconds as number);
  const overlapValue = (overlapEntry?.value as number | null) ?? (defaults.windowOverlapSeconds as number);

  const crossFieldError =
    sizeValue !== null && overlapValue !== null && overlapValue >= sizeValue
      ? 'Overlap must be less than window size.'
      : null;

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
              disabled={!!sizeEnv || sizeEntry?.isDefault !== false}
              onClick={() => onUpdate({ windowSizeSeconds: defaults.windowSizeSeconds as number })}
            />
          </div>
          <NumberCommitInput
            value={sizeValue}
            min={120}
            max={1800}
            step={30}
            disabled={!!sizeEnv}
            parse={(raw) => {
              const v = parseInt(raw, 10);
              return Number.isFinite(v) ? v : null;
            }}
            onCommit={(parsed) => {
              if (parsed === null) return;
              onUpdate({ windowSizeSeconds: parsed });
            }}
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
              disabled={!!overlapEnv || overlapEntry?.isDefault !== false}
              onClick={() => onUpdate({ windowOverlapSeconds: defaults.windowOverlapSeconds as number })}
            />
          </div>
          <NumberCommitInput
            value={overlapValue}
            min={0}
            max={1770}
            step={30}
            disabled={!!overlapEnv}
            parse={(raw) => {
              const v = parseInt(raw, 10);
              return Number.isFinite(v) ? v : null;
            }}
            onCommit={(parsed) => {
              if (parsed === null) return;
              onUpdate({ windowOverlapSeconds: parsed });
            }}
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
  isDefault,
  defaultValue,
  onUpdate,
}: {
  value: number;
  isDefault: boolean;
  defaultValue: number;
  onUpdate: (payload: UpdateSettingsPayload) => void;
}) {
  const clamp = (n: number) => Math.max(1, Math.min(32, n));

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
              disabled={isDefault}
              onClick={() => onUpdate({ adDetectionParallelWindows: defaultValue })}
            />
          </div>
          <NumberCommitInput
            value={value}
            min={1}
            max={32}
            step={1}
            disabled={false}
            parse={(raw) => {
              if (raw.trim() === '') return defaultValue;
              const v = parseInt(raw, 10);
              if (!Number.isFinite(v)) return null;
              return clamp(v);
            }}
            onCommit={(parsed) => {
              if (parsed === null) return;
              onUpdate({ adDetectionParallelWindows: parsed });
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
  onUpdate,
  parallelWindows,
  parallelWindowsIsDefault,
  parallelWindowsDefault,
}: StageTunablesSectionProps) {
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
            llmProvider={llmProvider}
            onUpdate={onUpdate}
          />
        ))}
        <WindowConfigBlock
          tunables={tunables}
          defaults={defaults}
          onUpdate={onUpdate}
        />
        <ConcurrencyConfigBlock
          value={parallelWindows}
          isDefault={parallelWindowsIsDefault}
          defaultValue={parallelWindowsDefault}
          onUpdate={onUpdate}
        />
      </div>
    </CollapsibleSection>
  );
}

export default StageTunablesSection;

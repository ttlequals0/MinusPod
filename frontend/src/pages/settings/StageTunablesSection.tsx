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
          <label className="block text-xs font-medium text-foreground mb-1">
            Temperature
          </label>
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
          <label className="block text-xs font-medium text-foreground mb-1">
            Max tokens
          </label>
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
            <label className="block text-xs font-medium text-foreground mb-1">
              Reasoning budget (Anthropic)
            </label>
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
            <label className="block text-xs font-medium text-foreground mb-1">
              Reasoning effort
            </label>
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

function StageTunablesSection({
  tunables,
  defaults,
  llmProvider,
  onUpdate,
}: StageTunablesSectionProps) {
  return (
    <CollapsibleSection title="LLM Tunables (per stage)">
      <p className="text-sm text-muted-foreground mb-3">
        Temperature, max tokens, and reasoning per pass. Applies on the next episode.
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
      </div>
    </CollapsibleSection>
  );
}

export default StageTunablesSection;

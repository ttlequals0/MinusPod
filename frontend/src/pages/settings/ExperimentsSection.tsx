import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import PromptField from './PromptField';
import { clampNumericInput } from '../../utils/clampNumericInput';

export interface ReviewerState {
  enabled: boolean;
  model: string;
  maxShift: number;
  reviewPrompt: string;
  resurrectPrompt: string;
  parallelAds: number;
  updatePatterns: boolean;
  minTrimThreshold: number;
}

interface ExperimentsSectionProps {
  reviewer: ReviewerState;
  onChange: (next: ReviewerState) => void;
  onResetPrompts: () => void;
  resetIsPending: boolean;
  modelOptions?: Array<{ id: string; label: string }>;
}

function ExperimentsSection({
  reviewer,
  onChange,
  onResetPrompts,
  resetIsPending,
  modelOptions = [],
}: ExperimentsSectionProps) {
  const update = <K extends keyof ReviewerState>(key: K, value: ReviewerState[K]) =>
    onChange({ ...reviewer, [key]: value });

  // Clamp numeric input on edit so an empty or out-of-range value never reaches
  // Save (the backend rejects them). maxShift stays out: it has weaker semantics
  // and leans on the native min/max only.
  const clampUpdate = (
    key: 'parallelAds' | 'minTrimThreshold',
    raw: string,
    lo: number,
    hi: number,
    fallback: number,
    parse: (s: string) => number,
  ) => {
    const v = clampNumericInput(raw, lo, hi, fallback, parse);
    if (v !== undefined) update(key, v);
  };

  return (
    <CollapsibleSection
      title="Ad Reviewer"
      subtitle="Reviews each detected ad and decides confirm, adjust, or reject before the cut. Off by default."
    >
      <div className="space-y-6">
        {/* Reviewer behavior */}
        <div className="space-y-6">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={reviewer.enabled}
                onChange={(v) => update('enabled', v)}
                ariaLabel="Enable ad reviewer"
              />
              <span className="text-sm font-medium text-foreground">
                Enable ad reviewer
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              Adds one LLM call per detected ad. Worth it on comedy, fiction, and sponsor-adjacent news podcasts where the detector struggles with editorial mentions.
            </p>
          </div>

          <div>
            <label htmlFor="reviewModel" className="block text-sm font-medium text-foreground mb-2">
              Review model
            </label>
            <select
              id="reviewModel"
              value={reviewer.model}
              onChange={(e) => update('model', e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
            >
              <option value="same_as_pass">Same as pass model</option>
              {modelOptions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              "Same as pass model" reuses each pass's own model for its review. Pick a specific model to use one for both passes instead.
            </p>
          </div>

          <div>
            <label htmlFor="reviewMaxBoundaryShift" className="block text-sm font-medium text-foreground mb-2">
              Max boundary shift
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                id="reviewMaxBoundaryShift"
                value={reviewer.maxShift}
                onChange={(e) => update('maxShift', parseInt(e.target.value, 10) || 60)}
                min={1}
                max={600}
                className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
              />
              <span className="text-sm text-muted-foreground">seconds (1-600)</span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Cap on how far the reviewer can move boundaries when it chooses adjust. Enforced in code, not just the prompt.
            </p>
          </div>

          <div>
            <label htmlFor="reviewerParallelAds" className="block text-sm font-medium text-foreground mb-2">
              Parallel ad reviews
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                id="reviewerParallelAds"
                value={reviewer.parallelAds}
                onChange={(e) => clampUpdate('parallelAds', e.target.value, 1, 32, 4, (s) => parseInt(s, 10))}
                min={1}
                max={32}
                step={1}
                className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
              />
              <span className="text-sm text-muted-foreground">ads at a time (1-32)</span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              How many ads the reviewer asks the LLM about at once. 1 is sequential (the original behavior). Higher values cut review time but add concurrent load on your LLM provider. Default 4.
            </p>
          </div>
        </div>

        {/* Pattern learning */}
        <div className="pt-6 border-t border-border space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={reviewer.updatePatterns}
                onChange={(v) => update('updatePatterns', v)}
                ariaLabel={reviewer.updatePatterns ? 'Pattern updates enabled' : 'Pattern updates disabled'}
              />
              <span className="text-sm font-medium text-foreground">
                Update patterns from reviewer adjustments
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              When a reviewer trims an ad's boundaries by more than the threshold, the matching local pattern's text is re-extracted from the new bounds. Community patterns are never rewritten automatically.
            </p>
          </div>

          {reviewer.updatePatterns && (
            <div>
              <label htmlFor="minTrimThreshold" className="block text-sm font-medium text-foreground mb-2">
                Minimum trim threshold
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  id="minTrimThreshold"
                  value={reviewer.minTrimThreshold}
                  onChange={(e) => clampUpdate('minTrimThreshold', e.target.value, 1, 120, 20, parseFloat)}
                  min={1}
                  max={120}
                  className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
                />
                <span className="text-sm text-muted-foreground">seconds (1-120)</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                Re-extract a pattern only when the reviewer trims at least this much off an ad. Smaller edits leave the saved pattern alone.
              </p>
            </div>
          )}
        </div>

        {/* Prompts */}
        <div className="pt-6 border-t border-border space-y-6">
          <PromptField
            id="reviewPrompt"
            label="Review prompt (confirm / adjust / reject)"
            value={reviewer.reviewPrompt}
            onChange={(v) => update('reviewPrompt', v)}
            helpText={
              <>
                Placeholders: <code>{'{sponsor_database}'}</code>, <code>{'{max_boundary_shift_seconds}'}</code>. Remove a placeholder to skip that injection.
              </>
            }
          />

          <PromptField
            id="resurrectPrompt"
            label="Resurrect prompt (resurrect / reject)"
            value={reviewer.resurrectPrompt}
            onChange={(v) => update('resurrectPrompt', v)}
            helpText={
              <>
                Second-guesses validator rejections in the resurrection band. Placeholder: <code>{'{sponsor_database}'}</code>.
              </>
            }
          />

          <button
            onClick={onResetPrompts}
            disabled={resetIsPending}
            className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm"
          >
            {resetIsPending ? 'Resetting...' : 'Reset Reviewer Prompts to Default'}
          </button>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default ExperimentsSection;

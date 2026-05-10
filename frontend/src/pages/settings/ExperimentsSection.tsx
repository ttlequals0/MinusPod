import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import PromptField from './PromptField';

export interface ReviewerState {
  enabled: boolean;
  model: string;
  maxShift: number;
  reviewPrompt: string;
  resurrectPrompt: string;
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

  return (
    <CollapsibleSection
      title="Ad Reviewer"
      subtitle="Reviews each detected ad and decides confirm, adjust, or reject before the cut. Off by default."
    >
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
          className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
        >
          {resetIsPending ? 'Resetting...' : 'Reset Reviewer Prompts to Default'}
        </button>
      </div>
    </CollapsibleSection>
  );
}

export default ExperimentsSection;

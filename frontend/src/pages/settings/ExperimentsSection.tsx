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
  // Available models from existing model dropdowns; we mirror that source so
  // the reviewer can select any model already configured for the providers.
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
    <CollapsibleSection title="Ad Reviewer">
      <div className="space-y-6">
        <div className="text-sm text-muted-foreground">
          Opt-in third LLM stage that reviews each detected ad before audio is
          cut. The reviewer can confirm a detection, adjust its boundaries
          within a configured cap, or reject it as a false positive. It also
          reviews validator-rejected detections that fell within 20 percentage
          points of the cut threshold and may resurrect them as real ads.
          Adds one LLM call per detected ad. Disabled by default.
        </div>

        <div className="flex items-center justify-between">
          <div>
            <label className="block text-sm font-medium text-foreground">
              Enable ad reviewer
            </label>
            <p className="text-sm text-muted-foreground">
              Adds an LLM cost per detected ad but can improve accuracy on
              comedy, fiction, and sponsor-adjacent news podcasts.
            </p>
          </div>
          <ToggleSwitch checked={reviewer.enabled} onChange={(v) => update('enabled', v)} />
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
            "Same as pass model" reuses the pass 1 detection model on pass 1
            review and the verification model on pass 2 review. Override to
            run a single specific model for both reviewer passes.
          </p>
        </div>

        <div>
          <label htmlFor="reviewMaxBoundaryShift" className="block text-sm font-medium text-foreground mb-2">
            Max boundary shift (seconds)
          </label>
          <input
            id="reviewMaxBoundaryShift"
            type="number"
            min={1}
            max={600}
            value={reviewer.maxShift}
            onChange={(e) => update('maxShift', parseInt(e.target.value, 10) || 60)}
            className="w-32 px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Cap on how far the reviewer can move start/end timestamps when it
            chooses adjust. Enforced in code regardless of the prompt content.
          </p>
        </div>

        <PromptField
          id="reviewPrompt"
          label="Review prompt (confirm / adjust / reject)"
          value={reviewer.reviewPrompt}
          onChange={(v) => update('reviewPrompt', v)}
          helpText={
            <>
              Available placeholders: <code>{'{sponsor_database}'}</code>,{' '}
              <code>{'{max_boundary_shift_seconds}'}</code>. Removing a
              placeholder means that content is not inserted at runtime.
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
              Used to second-guess validator rejections within the resurrection
              band. Available placeholder: <code>{'{sponsor_database}'}</code>.
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

import type { ClaudeModel } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatModelLabel } from './settingsUtils';

interface AIModelsSectionProps {
  models: ClaudeModel[] | undefined;
  modelsLoading: boolean;
  selectedModel: string;
  verificationModel: string;
  chaptersModel: string;
  onSelectedModelChange: (model: string) => void;
  onVerificationModelChange: (model: string) => void;
  onChaptersModelChange: (model: string) => void;
  onRefresh: () => void;
  refreshIsPending: boolean;
}

function AIModelsSection({
  models,
  modelsLoading,
  selectedModel,
  verificationModel,
  chaptersModel,
  onSelectedModelChange,
  onVerificationModelChange,
  onChaptersModelChange,
  onRefresh,
  refreshIsPending,
}: AIModelsSectionProps) {
  return (
    <CollapsibleSection
      title="AI Models"
      defaultOpen
      headerRight={
        <button
          onClick={onRefresh}
          disabled={refreshIsPending}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          title="Refresh model list from provider"
        >
          {refreshIsPending ? (
            <>
              <LoadingSpinner inline className="w-3.5 h-3.5" />
              Refreshing...
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh
            </>
          )}
        </button>
      }
    >
      {!modelsLoading && models && models.length === 0 && (
        <div className="mb-4 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
          <p className="text-sm text-yellow-600 dark:text-yellow-400">
            No models available from the LLM provider. Check that your provider is configured correctly and the endpoint is reachable.
          </p>
        </div>
      )}

      <div className="space-y-4">
        <div>
          <label htmlFor="model" className="block text-sm font-medium text-foreground mb-2">
            Ad Detection Model
          </label>
          <select
            id="model"
            value={selectedModel}
            onChange={(e) => onSelectedModelChange(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {models?.map((model) => (
              <option key={model.id} value={model.id}>
                {formatModelLabel(model)}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Primary model for analyzing transcripts and detecting ads
          </p>
        </div>

        <div>
          <label htmlFor="verificationModel" className="block text-sm font-medium text-foreground mb-2">
            Verification Model
          </label>
          <select
            id="verificationModel"
            value={verificationModel}
            onChange={(e) => onVerificationModelChange(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {models?.map((model) => (
              <option key={model.id} value={model.id}>
                {formatModelLabel(model)}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Re-runs detection on processed audio to catch missed ads (can differ for cost optimization)
          </p>
        </div>

        <div>
          <label htmlFor="chaptersModel" className="block text-sm font-medium text-foreground mb-2">
            Chapters Model
          </label>
          <select
            id="chaptersModel"
            value={chaptersModel}
            onChange={(e) => onChaptersModelChange(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {models?.map((model) => (
              <option key={model.id} value={model.id}>
                {formatModelLabel(model)}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Chapter title generation and topic detection (smaller/cheaper models work well)
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AIModelsSection;

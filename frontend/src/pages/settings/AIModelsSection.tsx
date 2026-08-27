import { useState } from 'react';
import type { ReactNode } from 'react';
import type { ClaudeModel } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatModelLabel } from './settingsUtils';
import { btnSecondary } from '../../components/buttonStyles';
import { selectBase } from '../../components/fieldStyles';
import { focusRing } from '../../components/fieldStyles';

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
  // A saved model id missing from the live catalog (wrong provider for
  // the stored tag, renamed model, transient probe failure) would render
  // the <select> blank, which users read as "the setting was reset".
  const isOrphan = (value: string) =>
    Boolean(value) && !!models && !models.some((m) => m.id === value);

  const renderOrphan = (value: string) => {
    if (!isOrphan(value)) return null;
    return <option value={value}>{value} (current, not in catalog)</option>;
  };

  // The catalog only lists what the provider advertises. Proxies, private
  // deployments, and brand-new model ids need a way in, so each field can
  // switch to free text. An orphaned value still renders as a list option
  // above, so the switch stays the user's call rather than an inference.
  const [typedFields, setTypedFields] = useState<Record<string, boolean>>({});

  const renderModelSelect = ({
    id,
    label,
    value,
    onChange,
    description,
  }: {
    id: string;
    label: string;
    value: string;
    onChange: (model: string) => void;
    description: ReactNode;
  }) => {
    const notConfigured = !value;
    const typed = typedFields[id] ?? false;
    return (
      <div>
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <label htmlFor={id} className="block text-sm font-medium text-foreground">
            {label}
          </label>
          <button
            type="button"
            onClick={() => setTypedFields((prev) => ({ ...prev, [id]: !typed }))}
            className={`text-xs text-primary hover:underline transition-colors rounded ${focusRing}`}
          >
            {typed ? 'Choose from list' : 'Type a model ID'}
          </button>
        </div>
        {typed ? (
          <input
            type="text"
            id={id}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="Provider's exact model ID"
            spellCheck={false}
            autoComplete="off"
            className={`w-full px-3 py-2 rounded-lg border border-input bg-background text-foreground text-sm ${focusRing}`}
          />
        ) : (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full ${selectBase}`}
        >
          {notConfigured && <option value="">Not configured</option>}
          {renderOrphan(value)}
          {models?.map((model) => (
            <option key={model.id} value={model.id}>
              {formatModelLabel(model)}
            </option>
          ))}
        </select>
        )}
        {notConfigured && (
          <p className="mt-1 text-sm text-muted-foreground">Pick a model before processing episodes.</p>
        )}
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
    );
  };

  return (
    <CollapsibleSection
      title="AI Models"
      defaultOpen
      headerRight={
        <button
          onClick={onRefresh}
          disabled={refreshIsPending}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
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
        <div className="mb-4 p-3 rounded-lg bg-warning/10 border border-warning/20">
          <p className="text-sm text-warning">
            No models available from the LLM provider. Check that your provider is configured correctly and the endpoint is reachable.
          </p>
        </div>
      )}

      <div className="space-y-4">
        {renderModelSelect({
          id: 'model',
          label: 'Ad Detection Model',
          value: selectedModel,
          onChange: onSelectedModelChange,
          description:
            'Primary model for analyzing transcripts and detecting ads. Set the model here; the OPENAI_MODEL env var only seeds this value while it is unset.',
        })}

        {renderModelSelect({
          id: 'verificationModel',
          label: 'Verification Model',
          value: verificationModel,
          onChange: onVerificationModelChange,
          description: 'Re-runs detection on processed audio to catch missed ads (can differ for cost optimization)',
        })}

        {renderModelSelect({
          id: 'chaptersModel',
          label: 'Chapters Model',
          value: chaptersModel,
          onChange: onChaptersModelChange,
          description: 'Chapter title generation and topic detection (smaller/cheaper models work well)',
        })}
      </div>
    </CollapsibleSection>
  );
}

export default AIModelsSection;

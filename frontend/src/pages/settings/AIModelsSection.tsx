import { useState } from 'react';
import type { ClaudeModel, ModelPricingOverride, ModelPricingOverrides } from '../../api/types';
import type { ModelCatalog } from '../../hooks/useModelCatalog';
import CatalogStatus from '../../components/CatalogStatus';
import CollapsibleSection from '../../components/CollapsibleSection';
import RefreshModelsButton from './RefreshModelsButton';
import type { ModelsRefresh } from '../../hooks/useModelsRefresh';
import ModelSelect from './ModelSelect';
import StageProviderSelect, { detectionSlotOptions, inheritedSlotOptions } from './StageProviderSelect';
import { btnSecondary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';
import { isSystemOneRoute } from './systemoneWarnings';

interface AIModelsSectionProps {
  // Each stage carries its own catalog and its own fetch state. No fallback
  // to detection's: verification/chapters can sit on a different provider,
  // and borrowing its list would offer models that provider never serves.
  detectionCatalog: ModelCatalog;
  verificationCatalog: ModelCatalog;
  chaptersCatalog: ModelCatalog;
  /** The header Refresh: one button covers all three stages. */
  modelsRefresh: ModelsRefresh;
  selectedModel: string;
  verificationModel: string;
  chaptersModel: string;
  onSelectedModelChange: (model: string) => void;
  onVerificationModelChange: (model: string) => void;
  onChaptersModelChange: (model: string) => void;
  // '' means "inherit" (detection falls back to the global LLM Provider;
  // verification/chapters fall back to detection's resolved provider).
  detectionProvider: string;
  verificationProvider: string;
  chaptersProvider: string;
  effectiveChaptersProvider: string;
  onDetectionProviderChange: (provider: string) => void;
  onVerificationProviderChange: (provider: string) => void;
  onChaptersProviderChange: (provider: string) => void;
  // Shows the Secondary option on each stage's provider select; hidden
  // (and the select never stores 'secondary') while the secondary provider
  // is off.
  secondaryProviderEnabled?: boolean;
  modelPricingOverrides?: ModelPricingOverrides;
  additionalModelIds?: string[];
  onPricingOverrideUpdate?: (
    modelId: string,
    override: ModelPricingOverride | null,
  ) => Promise<unknown>;
  pricingOverrideSavingModel?: string | null;
}

function AIModelsSection({
  detectionCatalog,
  verificationCatalog,
  chaptersCatalog,
  modelsRefresh,
  selectedModel,
  verificationModel,
  chaptersModel,
  onSelectedModelChange,
  onVerificationModelChange,
  onChaptersModelChange,
  detectionProvider,
  verificationProvider,
  chaptersProvider,
  effectiveChaptersProvider,
  onDetectionProviderChange,
  onVerificationProviderChange,
  onChaptersProviderChange,
  secondaryProviderEnabled = false,
  modelPricingOverrides = {},
  additionalModelIds = [],
  onPricingOverrideUpdate,
  pricingOverrideSavingModel = null,
}: AIModelsSectionProps) {
  // Merge every fetched catalog for pricing lookups: verification/chapters
  // can now be on a different provider than detection, each with its own
  // catalog entry (and price) for the same model id.
  const allCatalogModels = [
    ...(detectionCatalog.models ?? []),
    ...(verificationCatalog.models ?? []),
    ...(chaptersCatalog.models ?? []),
  ];
  const configuredModelIds = Array.from(new Set([
    selectedModel,
    verificationModel,
    chaptersModel,
    ...additionalModelIds,
    ...Object.keys(modelPricingOverrides),
  ].filter(Boolean)));

  // Detection picks primary or secondary directly; verification/chapters
  // also inherit detection's resolved slot via "Same as detection".
  const detectionOptions = detectionSlotOptions(secondaryProviderEnabled);
  const inheritedOptions = inheritedSlotOptions(secondaryProviderEnabled);

  return (
    <CollapsibleSection
      title="AI Models"
      defaultOpen
      headerRight={(
        <RefreshModelsButton onClick={modelsRefresh.refresh} isPending={modelsRefresh.isPending} />
      )}
    >
      {(!selectedModel || !verificationModel || !chaptersModel) && (
        <div className="mb-4 p-3 rounded-lg bg-warning/10 border border-warning/20">
          <p className="text-sm text-warning">
            No model selected from the provider.
          </p>
        </div>
      )}

      <div className="space-y-4 max-sm:[&_input]:min-h-11 max-sm:[&_select]:min-h-11 max-sm:[&_button]:min-h-11">
        <CatalogStatus refreshError={modelsRefresh.error} />

        {isSystemOneRoute(effectiveChaptersProvider, chaptersModel) && (
          <div role="status" className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
            System One cannot generate chapters. Select a chat provider and model.
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <StageProviderSelect
            id="detectionProvider"
            label="Ad Detection Provider"
            value={detectionProvider}
            options={detectionOptions}
            onChange={onDetectionProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
          />
          <ModelSelect
            id="model"
            label="Ad Detection Model"
            value={selectedModel}
            catalog={detectionCatalog}
            onChange={onSelectedModelChange}
            description="Model used to analyze transcripts and detect ads."
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <StageProviderSelect
            id="verificationProvider"
            label="Verification Provider"
            value={verificationProvider}
            options={inheritedOptions}
            onChange={onVerificationProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
          />
          <ModelSelect
            id="verificationModel"
            label="Verification Model"
            value={verificationModel}
            catalog={verificationCatalog}
            onChange={onVerificationModelChange}
            description="Re-runs detection on processed audio to catch missed ads (can differ for cost optimization)"
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <StageProviderSelect
            id="chaptersProvider"
            label="Chapters Provider"
            value={chaptersProvider}
            options={inheritedOptions}
            onChange={onChaptersProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
          />
          <ModelSelect
            id="chaptersModel"
            label="Chapters Model"
            value={chaptersModel}
            catalog={chaptersCatalog}
            onChange={onChaptersModelChange}
            description="Chapter title generation and topic detection (smaller/cheaper models work well)"
          />
        </div>

        {onPricingOverrideUpdate && configuredModelIds.length > 0 && (
          <div className="pt-4 border-t border-border space-y-4">
            <div>
              <h4 className="text-sm font-medium text-foreground">Custom pricing</h4>
              <p className="mt-1 text-sm text-muted-foreground">
                Set USD prices per 1 million tokens when the catalog price is missing or wrong. Leave both fields blank to use the catalog. Enter 0 for a free model.
              </p>
            </div>
            {configuredModelIds.map((modelId, index) => {
              const override = modelPricingOverrides[modelId];
              return <ModelPricingFields
                key={`${modelId}:${override?.inputCostPerMtok ?? ''}:${override?.outputCostPerMtok ?? ''}`}
                fieldId={`modelPricing-${index}`}
                modelId={modelId}
                override={override}
                catalogModel={allCatalogModels.find((model) => model.id === modelId)}
                saving={pricingOverrideSavingModel === modelId}
                onUpdate={onPricingOverrideUpdate}
              />;
            })}
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}

function formatUsdRate(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 6,
  }).format(value);
}

function ModelPricingFields({
  fieldId,
  modelId,
  override,
  catalogModel,
  saving,
  onUpdate,
}: {
  fieldId: string;
  modelId: string;
  override?: ModelPricingOverride;
  catalogModel?: ClaudeModel;
  saving: boolean;
  onUpdate: (modelId: string, override: ModelPricingOverride | null) => Promise<unknown>;
}) {
  const savedInput = override === undefined ? '' : String(override.inputCostPerMtok);
  const savedOutput = override === undefined ? '' : String(override.outputCostPerMtok);
  const [inputRate, setInputRate] = useState(savedInput);
  const [outputRate, setOutputRate] = useState(savedOutput);
  const [message, setMessage] = useState<string | null>(null);

  const isDirty = inputRate !== savedInput || outputRate !== savedOutput;
  const commit = async () => {
    setMessage(null);
    const bothBlank = inputRate.trim() === '' && outputRate.trim() === '';
    if ((inputRate.trim() === '') !== (outputRate.trim() === '')) {
      setMessage('Enter both prices, or leave both blank.');
      return;
    }
    const input = Number(inputRate);
    const output = Number(outputRate);
    if (!bothBlank && (!Number.isFinite(input) || !Number.isFinite(output)
      || input < 0 || output < 0)) {
      setMessage('Prices must be non-negative numbers.');
      return;
    }
    try {
      await onUpdate(modelId, bothBlank ? null : {
        inputCostPerMtok: input,
        outputCostPerMtok: output,
      });
      setMessage(bothBlank ? 'Using catalog pricing.' : 'Custom pricing saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save custom pricing.');
    }
  };

  const catalogAvailable = catalogModel?.inputCostPerMtok != null
    && catalogModel.outputCostPerMtok != null
    && catalogModel.pricingSource !== 'operator';

  return (
    <fieldset className="space-y-3">
      <legend className="font-mono text-sm text-foreground break-all">{modelId}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${fieldId}-input`} className="block text-sm font-medium text-foreground mb-1">
            Input, USD per 1 million tokens
          </label>
          <input
            id={`${fieldId}-input`}
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            value={inputRate}
            onChange={(event) => { setInputRate(event.target.value); setMessage(null); }}
            className={`w-full min-h-[44px] px-3 py-2 rounded-lg border border-input bg-background text-foreground text-sm ${focusRing}`}
          />
        </div>
        <div>
          <label htmlFor={`${fieldId}-output`} className="block text-sm font-medium text-foreground mb-1">
            Output, USD per 1 million tokens
          </label>
          <input
            id={`${fieldId}-output`}
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            value={outputRate}
            onChange={(event) => { setOutputRate(event.target.value); setMessage(null); }}
            className={`w-full min-h-[44px] px-3 py-2 rounded-lg border border-input bg-background text-foreground text-sm ${focusRing}`}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={commit}
          disabled={!isDirty || saving}
          className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {saving ? 'Saving...' : 'Save pricing'}
        </button>
        {catalogAvailable && (
          <span className="text-xs text-muted-foreground">
            Catalog: {formatUsdRate(catalogModel.inputCostPerMtok!)} input, {formatUsdRate(catalogModel.outputCostPerMtok!)} output
          </span>
        )}
      </div>
      {!override && !catalogAvailable && (
        <p className="text-xs text-warning">
          Set both prices to estimate cost when the provider does not report it.
        </p>
      )}
      {message && (
        <p role="status" className="text-xs text-muted-foreground">{message}</p>
      )}
    </fieldset>
  );
}

export default AIModelsSection;

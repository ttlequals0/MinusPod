import { useDraftField } from '../../hooks/useDraftField';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { useState } from 'react';
import type { ClaudeModel, ModelPricingOverride, ModelPricingOverrides } from '../../api/types';
import type { ModelCatalog } from '../../hooks/useModelCatalog';
import CatalogStatus from '../../components/CatalogStatus';
import CollapsibleSection from '../../components/CollapsibleSection';
import RefreshModelsButton from './RefreshModelsButton';
import type { ModelsRefresh } from '../../hooks/useModelsRefresh';
import ModelSelect from './ModelSelect';
import StageProviderSelect, { detectionSlotOptions, inheritedSlotOptions } from './StageProviderSelect';
import { isSystemOneRoute } from './systemoneWarnings';
import { pricingOverrideKey } from './modelPricing';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { inputBase, labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';

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
  pricingModelIds: string[];
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
  pricingModelIds,
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
  const configuredModelIds = [...new Set(pricingModelIds.filter(Boolean))];

  // Detection picks primary or secondary directly; verification/chapters
  // also inherit detection's resolved slot via "Same as detection".
  const detectionOptions = detectionSlotOptions(secondaryProviderEnabled);
  const inheritedOptions = inheritedSlotOptions(secondaryProviderEnabled);

  return (
    <CollapsibleSection
      title="AI models"
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

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3">
          <StageProviderSelect
            id="detectionProvider"
            label="Ad detection provider"
            value={detectionProvider}
            options={detectionOptions}
            onChange={onDetectionProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
            aligned
          />
          <ModelSelect
            id="model"
            label="Ad detection model"
            value={selectedModel}
            catalog={detectionCatalog}
            onChange={onSelectedModelChange}
            description="Model used to analyze transcripts and detect ads."
            aligned
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3">
          <StageProviderSelect
            id="verificationProvider"
            label="Verification provider"
            value={verificationProvider}
            options={inheritedOptions}
            onChange={onVerificationProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
            aligned
          />
          <ModelSelect
            id="verificationModel"
            label="Verification model"
            value={verificationModel}
            catalog={verificationCatalog}
            onChange={onVerificationModelChange}
            description="Re-runs detection on processed audio to catch missed ads (can differ for cost optimization)"
            aligned
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3">
          <StageProviderSelect
            id="chaptersProvider"
            label="Chapters provider"
            value={chaptersProvider}
            options={inheritedOptions}
            onChange={onChaptersProviderChange}
            secondaryEnabled={secondaryProviderEnabled}
            aligned
          />
          <ModelSelect
            id="chaptersModel"
            label="Chapters model"
            value={chaptersModel}
            catalog={chaptersCatalog}
            onChange={onChaptersModelChange}
            description="Chapter title generation and topic detection (smaller/cheaper models work well)"
            aligned
          />
        </div>

        {onPricingOverrideUpdate && configuredModelIds.length > 0 && (
          <div className="pt-4 border-t border-border space-y-4">
            <div>
              <h4 className={subHeading}>Custom pricing</h4>
              <p className="mt-1 text-sm text-muted-foreground">
                Set USD prices per 1 million tokens when the catalog price is missing or wrong. Leave input and output blank to use the catalog. The cached input price is optional. Enter 0 for a free model.
              </p>
            </div>
            {configuredModelIds.map((modelId, index) => {
              const overrideKey = pricingOverrideKey(modelId, modelPricingOverrides);
              const override = modelPricingOverrides[overrideKey];
              return <ModelPricingFields
                key={`${modelId}:${overrideKey}`}
                fieldId={`modelPricing-${index}`}
                modelId={modelId}
                override={override}
                catalogModel={allCatalogModels.find((model) => model.id === modelId)}
                saving={pricingOverrideSavingModel === overrideKey}
                onUpdate={(_modelId, value) => onPricingOverrideUpdate(overrideKey, value)}
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

function RateField({ id, label, value, onChange, disabled, describedBy }: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  describedBy?: string;
}) {
  return (
    <div className="grid grid-rows-subgrid row-span-2 gap-y-0 min-w-0">
      <label htmlFor={id} className={labelBase}>{label}</label>
      <input
        id={id}
        disabled={disabled}
        aria-describedby={describedBy}
        type="number"
        min="0"
        step="any"
        inputMode="decimal"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputBase} w-full`}
      />
    </div>
  );
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
  const savedCache = override?.cacheReadCostPerMtok == null ? '' : String(override.cacheReadCostPerMtok);
  const inputDraft = useDraftField(savedInput, (value) => value);
  const outputDraft = useDraftField(savedOutput, (value) => value);
  const cacheDraft = useDraftField(savedCache, (value) => value);
  const { value: inputRate, setValue: setInputRate } = inputDraft;
  const { value: outputRate, setValue: setOutputRate } = outputDraft;
  const { value: cacheRate, setValue: setCacheRate } = cacheDraft;
  const [message, setMessage] = useState<string | null>(null);

  const isDirty = inputRate !== savedInput || outputRate !== savedOutput || cacheRate !== savedCache;
  useUnsavedChanges(isDirty);
  const commit = async () => {
    setMessage(null);
    const blank = { input: inputRate.trim() === '', output: outputRate.trim() === '', cache: cacheRate.trim() === '' };
    const bothBlank = blank.input && blank.output;
    if (blank.input !== blank.output) {
      setMessage('Enter both prices, or leave both blank.');
      return;
    }
    if (bothBlank && !blank.cache) {
      setMessage('Enter input and output prices before a cached input price.');
      return;
    }
    const hasCache = !blank.cache;
    const input = Number(inputRate);
    const output = Number(outputRate);
    const cache = Number(cacheRate);
    const invalid = (blankField: boolean, n: number) => !blankField && (!Number.isFinite(n) || n < 0);
    if (invalid(blank.input, input) || invalid(blank.output, output) || invalid(blank.cache, cache)) {
      setMessage('Prices must be non-negative numbers.');
      return;
    }
    try {
      await onUpdate(modelId, bothBlank ? null : {
        inputCostPerMtok: input,
        outputCostPerMtok: output,
        ...(hasCache && { cacheReadCostPerMtok: cache }),
      });
      inputDraft.markClean(bothBlank ? '' : String(input));
      outputDraft.markClean(bothBlank ? '' : String(output));
      cacheDraft.markClean(bothBlank || !hasCache ? '' : String(cache));
      setMessage(bothBlank ? 'Using catalog pricing.' : 'Custom pricing saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save custom pricing.');
    }
  };

  const rateFields = [
    { key: 'input', label: 'Input', value: inputRate, setValue: setInputRate },
    { key: 'output', label: 'Output', value: outputRate, setValue: setOutputRate },
    { key: 'cache', label: 'Cached input', value: cacheRate, setValue: setCacheRate },
  ];

  const catalogAvailable = catalogModel?.inputCostPerMtok != null
    && catalogModel.outputCostPerMtok != null
    && catalogModel.pricingSource !== 'operator';

  return (
    <fieldset className="space-y-3">
      <legend className="font-mono text-sm text-foreground break-all">{modelId}</legend>
      <p className="text-sm text-muted-foreground">USD per 1 million tokens</p>
      <div className="grid grid-cols-3 gap-x-3">
        {rateFields.map(({ key, label, value, setValue }) => (
          <RateField
            key={key}
            id={`${fieldId}-${key}`}
            label={label}
            value={value}
            onChange={(next) => { setValue(next); setMessage(null); }}
            disabled={saving}
            describedBy={message ? `${fieldId}-result` : undefined}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={commit}
          disabled={!isDirty || saving}
          className={`${btnBase} ${btnSecondary}`}
        >
          {saving ? 'Saving...' : 'Save pricing'}
        </button>
        {catalogAvailable && (
          <span className="text-xs text-muted-foreground">
            Catalog: {formatUsdRate(catalogModel.inputCostPerMtok!)} input, {formatUsdRate(catalogModel.outputCostPerMtok!)} output{typeof catalogModel.cacheReadCostPerMtok === 'number' && `, ${formatUsdRate(catalogModel.cacheReadCostPerMtok)} cached input`}
          </span>
        )}
      </div>
      {!override && !catalogAvailable && (
        <p className="text-xs text-warning">
          Set both prices to estimate cost when the provider does not report it.
        </p>
      )}
      {message && (
        <p id={`${fieldId}-result`} role="status" className="text-xs text-muted-foreground">{message}</p>
      )}
    </fieldset>
  );
}

export default AIModelsSection;

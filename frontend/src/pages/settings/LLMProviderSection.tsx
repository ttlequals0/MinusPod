import { useEffect, useRef, useState } from 'react';
import type { AffectedRunsAction, LlmProvider, StageTunables, UpdateSettingsPayload } from '../../api/types';
import { LLM_PROVIDERS, SLOT_LABELS, SLOT_PRIMARY, SLOT_SECONDARY } from '../../api/types';
import AccountSwitchPreflight from './AccountSwitchPreflight';
import CollapsibleSection from '../../components/CollapsibleSection';
import { KEY_META, ProviderFields, keyMetaForType, keyProviderFor } from './ProviderFields';
import type { ConnectionTestResult, ProviderName, ProviderStatus, ProviderTestResult, ProvidersResponse } from '../../api/providers';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import { selectBase } from '../../components/fieldStyles';

interface LLMProviderSectionProps {
  llmProvider: LlmProvider;
  openaiBaseUrl: string;
  systemoneBaseUrl: string;
  pricingSourceMode: string;
  onProviderChange: (provider: LlmProvider) => void;
  onBaseUrlChange: (url: string) => void;
  onSystemOneBaseUrlChange: (url: string) => void;
  onPricingSourceModeChange: (mode: string) => void;
  providersState: ProvidersResponse | null;
  onProviderKeySave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onProviderKeyClear: (provider: ProviderName) => Promise<void>;
  onProviderKeyTest: (provider: ProviderName) => Promise<ProviderTestResult>;
  onConnectionTest: (provider: 'openai' | 'ollama' | 'anthropic' | 'openrouter' | 'typesafe' | 'systemone-compatible', baseUrl?: string) => Promise<ConnectionTestResult>;
  ollamaNumCtx?: StageTunables['ollamaNumCtx'];
  onOllamaNumCtxUpdate?: (payload: UpdateSettingsPayload) => void;
  llmJsonSchemaEnabled: boolean;
  onLlmJsonSchemaEnabledChange: (enabled: boolean) => void;
  // Optional second provider config; ad detection, verification, chapters,
  // and the reviewer can each route to it via the 'secondary' slot instead
  // of the primary provider above. Off by default.
  secondaryProviderEnabled: boolean;
  onSecondaryProviderEnabledChange: (enabled: boolean) => void;
  secondaryProvider: LlmProvider | '';
  onSecondaryProviderChange: (provider: LlmProvider) => void;
  secondaryProviderBaseUrl: string;
  onSecondaryProviderBaseUrlChange: (url: string) => void;
  secondaryProviderApiKeyConfigured: boolean;
  onSecondaryProviderKeySave: (apiKey: string) => Promise<void>;
  onSecondaryProviderKeyClear: () => Promise<void>;
  // Backs both the key field's inline Test button and the standalone
  // ConnectionTestButton: the secondary slot has one end-to-end probe
  // route, not the separate quick-key-check endpoint the primary keys have.
  // `provider` carries the current (possibly unsaved) type selection so the
  // test always probes what's in the form, not the last-saved type.
  onSecondaryConnectionTest: (provider?: LlmProvider | '', baseUrl?: string) => Promise<ConnectionTestResult>;
  // Manual per-provider request-rate limits (#747); 0 = no limit.
  providerRequestsPerMin: number;
  onProviderRequestsPerMinChange: (value: number) => void;
  providerRequestsPerDay: number;
  onProviderRequestsPerDayChange: (value: number) => void;
  secondaryProviderRequestsPerMin: number;
  onSecondaryProviderRequestsPerMinChange: (value: number) => void;
  secondaryProviderRequestsPerDay: number;
  onSecondaryProviderRequestsPerDayChange: (value: number) => void;
  providerTokensPerMin: number;
  onProviderTokensPerMinChange: (value: number) => void;
  secondaryProviderTokensPerMin: number;
  onSecondaryProviderTokensPerMinChange: (value: number) => void;
  // Per-provider request timeout and retry overrides (#806); null inherits
  // the provider type's default (see providerDefaults() in ProviderFields).
  providerATimeoutSeconds: number | null;
  onProviderATimeoutSecondsChange: (value: number | null) => void;
  providerAMaxRetries: number | null;
  onProviderAMaxRetriesChange: (value: number | null) => void;
  providerBTimeoutSeconds: number | null;
  onProviderBTimeoutSecondsChange: (value: number | null) => void;
  providerBMaxRetries: number | null;
  onProviderBMaxRetriesChange: (value: number | null) => void;
  // Set when the form's endpoint or provider type for that slot differs from
  // what is saved, which is what moves in-flight work to another account.
  primaryAccountChanged: boolean;
  secondaryAccountChanged: boolean;
  affectedRunsAction: AffectedRunsAction;
  onAffectedRunsActionChange: (action: AffectedRunsAction) => void;
}

const RATE_LIMIT_MAX = 1_000_000;
const TOKEN_LIMIT_MAX = 1_000_000_000;
const parseIntOrZero = (s: string) => {
  const n = parseInt(s, 10);
  return Number.isFinite(n) ? n : 0;
};

// Requests-per-minute, requests-per-day, and tokens-per-minute caps for one
// provider account. Shown for every provider type. Drafts committed to form
// state; the page Save button persists them.
function RateLimitFields({
  idPrefix, legend, rpm, onRpmChange, rpd, onRpdChange, tpm, onTpmChange,
}: {
  idPrefix: string;
  legend: string;
  rpm: number;
  onRpmChange: (value: number) => void;
  rpd: number;
  onRpdChange: (value: number) => void;
  tpm: number;
  onTpmChange: (value: number) => void;
}) {
  return (
    <fieldset>
      {/* Both blocks repeat the same three labels, so the legend is what tells
          a screen reader which provider they belong to. */}
      <legend className="text-sm font-medium text-foreground mb-2">{legend}</legend>
      <div className="flex flex-wrap gap-6">
        <div>
          <label htmlFor={`${idPrefix}Rpm`} className="block text-sm font-medium text-foreground mb-2">
            Requests per minute
          </label>
          <NumberInput
            id={`${idPrefix}Rpm`}
            value={rpm}
            min={0}
            max={RATE_LIMIT_MAX}
            fallback={0}
            step={1}
            parse={parseIntOrZero}
            onCommit={onRpmChange}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}Rpd`} className="block text-sm font-medium text-foreground mb-2">
            Requests per day
          </label>
          <NumberInput
            id={`${idPrefix}Rpd`}
            value={rpd}
            min={0}
            max={RATE_LIMIT_MAX}
            fallback={0}
            step={1}
            parse={parseIntOrZero}
            onCommit={onRpdChange}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}Tpm`} className="block text-sm font-medium text-foreground mb-2">
            Tokens per minute
          </label>
          <NumberInput
            id={`${idPrefix}Tpm`}
            value={tpm}
            min={0}
            max={TOKEN_LIMIT_MAX}
            fallback={0}
            step={1}
            parse={parseIntOrZero}
            onCommit={onTpmChange}
          />
        </div>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        0 means no limit. These throttle MinusPod to stay under this provider
        account's request and token limits, useful for free tiers.
      </p>
    </fieldset>
  );
}

const NONE_STATUS: ProviderStatus = { configured: false, source: 'none' };

function LLMProviderSection({
  llmProvider,
  openaiBaseUrl,
  systemoneBaseUrl,
  pricingSourceMode,
  onProviderChange,
  onBaseUrlChange,
  onSystemOneBaseUrlChange,
  onPricingSourceModeChange,
  providersState,
  onProviderKeySave,
  onProviderKeyClear,
  onProviderKeyTest,
  onConnectionTest,
  ollamaNumCtx,
  onOllamaNumCtxUpdate,
  llmJsonSchemaEnabled,
  onLlmJsonSchemaEnabledChange,
  secondaryProviderEnabled,
  onSecondaryProviderEnabledChange,
  secondaryProvider,
  onSecondaryProviderChange,
  secondaryProviderBaseUrl,
  onSecondaryProviderBaseUrlChange,
  secondaryProviderApiKeyConfigured,
  onSecondaryProviderKeySave,
  onSecondaryProviderKeyClear,
  onSecondaryConnectionTest,
  providerRequestsPerMin,
  onProviderRequestsPerMinChange,
  providerRequestsPerDay,
  onProviderRequestsPerDayChange,
  secondaryProviderRequestsPerMin,
  onSecondaryProviderRequestsPerMinChange,
  secondaryProviderRequestsPerDay,
  onSecondaryProviderRequestsPerDayChange,
  providerTokensPerMin,
  onProviderTokensPerMinChange,
  secondaryProviderTokensPerMin,
  onSecondaryProviderTokensPerMinChange,
  providerATimeoutSeconds,
  onProviderATimeoutSecondsChange,
  providerAMaxRetries,
  onProviderAMaxRetriesChange,
  providerBTimeoutSeconds,
  onProviderBTimeoutSecondsChange,
  providerBMaxRetries,
  onProviderBMaxRetriesChange,
  primaryAccountChanged,
  secondaryAccountChanged,
  affectedRunsAction,
  onAffectedRunsActionChange,
}: LLMProviderSectionProps) {
  const keyProvider = keyProviderFor(llmProvider);
  const status = keyProvider && providersState ? providersState[keyProvider] : NONE_STATUS;
  const cryptoReady = providersState?.cryptoReady ?? false;

  const secondaryKeyStatus: ProviderStatus = {
    configured: secondaryProviderApiKeyConfigured,
    source: secondaryProviderApiKeyConfigured ? 'db' : 'none',
  };
  const secondaryKeyMeta = keyMetaForType(secondaryProvider);

  return (
    <CollapsibleSection title="LLM Provider" defaultOpen>
      <div className="space-y-4 max-sm:[&_input]:min-h-11 max-sm:[&_select]:min-h-11 max-sm:[&_button]:min-h-11">
        <div>
          <span className="text-sm font-medium text-foreground">{SLOT_LABELS.primary}</span>
        </div>
        <ProviderFields
          providerSelectId="llmProvider"
          providerLabel="Provider"
          provider={llmProvider}
          onProviderChange={onProviderChange}
          baseUrlInputId={llmProvider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE ? 'systemoneBaseUrl' : 'openaiBaseUrl'}
          baseUrlLabel={llmProvider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE ? 'System One base URL' : 'Base URL'}
          baseUrl={llmProvider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE ? systemoneBaseUrl : openaiBaseUrl}
          onBaseUrlChange={llmProvider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE ? onSystemOneBaseUrlChange : onBaseUrlChange}
          slotLabel={SLOT_LABELS.primary}
          timeoutSeconds={providerATimeoutSeconds}
          onTimeoutChange={onProviderATimeoutSecondsChange}
          maxRetries={providerAMaxRetries}
          onMaxRetriesChange={onProviderAMaxRetriesChange}
          keyProvider={keyProvider ?? 'anthropic'}
          keyStatus={status}
          cryptoReady={cryptoReady}
          keyLabel={keyProvider ? KEY_META[keyProvider].label : 'API key'}
          keyPlaceholder={keyProvider ? KEY_META[keyProvider].placeholder : ''}
          keyHelper={keyProvider ? KEY_META[keyProvider].helper : undefined}
          onProviderKeySave={onProviderKeySave}
          onProviderKeyClear={onProviderKeyClear}
          onProviderKeyTest={onProviderKeyTest}
          onConnectionTest={(baseUrl) => onConnectionTest(
            llmProvider === LLM_PROVIDERS.OLLAMA
              ? 'ollama'
              : llmProvider === LLM_PROVIDERS.OPENAI_COMPATIBLE
                ? 'openai'
                : llmProvider === LLM_PROVIDERS.TYPESAFE
                  ? 'typesafe'
                  : llmProvider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE
                    ? 'systemone-compatible'
                : llmProvider === LLM_PROVIDERS.ANTHROPIC
                  ? 'anthropic'
                  : 'openrouter',
            baseUrl,
          )}
        />

        <AccountSwitchPreflight
          slot={SLOT_PRIMARY}
          changed={primaryAccountChanged}
          action={affectedRunsAction}
          onActionChange={onAffectedRunsActionChange}
        />

        {llmProvider === LLM_PROVIDERS.OLLAMA && ollamaNumCtx && onOllamaNumCtxUpdate && (
          <OllamaNumCtxField
            entry={ollamaNumCtx}
            onUpdate={onOllamaNumCtxUpdate}
          />
        )}

        {llmProvider === LLM_PROVIDERS.OPENAI_COMPATIBLE && (
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={llmJsonSchemaEnabled}
                onChange={onLlmJsonSchemaEnabledChange}
                ariaLabel="JSON schema response format"
              />
              <span className="text-sm font-medium text-foreground">
                JSON schema response format
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground">
              Use JSON schema for detection and review. Unsupported endpoints fall back to plain JSON.
            </p>
          </div>
        )}

        <RateLimitFields
          idPrefix="provider"
          legend={`${SLOT_LABELS.primary} rate limits`}
          rpm={providerRequestsPerMin}
          onRpmChange={onProviderRequestsPerMinChange}
          rpd={providerRequestsPerDay}
          onRpdChange={onProviderRequestsPerDayChange}
          tpm={providerTokensPerMin}
          onTpmChange={onProviderTokensPerMinChange}
        />

        <div className="pt-4 border-t border-border space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={secondaryProviderEnabled}
                onChange={onSecondaryProviderEnabledChange}
                ariaLabel="Enable Provider B"
              />
              <span className="text-sm font-medium text-foreground">
                {SLOT_LABELS.secondary}
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              A second provider that detection, verification, chapters and the reviewer can each route to instead of {SLOT_LABELS.primary}. Both are peers; use the Failover card below for automatic switching when one is down.
            </p>
          </div>

          {secondaryProviderEnabled && (
            <ProviderFields
              providerSelectId="secondaryProviderType"
              providerLabel="Provider B type"
              provider={secondaryProvider}
              onProviderChange={onSecondaryProviderChange}
              baseUrlInputId="secondaryProviderBaseUrl"
              baseUrlLabel="Provider B base URL"
              baseUrl={secondaryProviderBaseUrl}
              onBaseUrlChange={onSecondaryProviderBaseUrlChange}
              slotLabel={SLOT_LABELS.secondary}
              timeoutSeconds={providerBTimeoutSeconds}
              onTimeoutChange={onProviderBTimeoutSecondsChange}
              maxRetries={providerBMaxRetries}
              onMaxRetriesChange={onProviderBMaxRetriesChange}
              keyProvider="secondary"
              keyStatus={secondaryKeyStatus}
              cryptoReady={cryptoReady}
              keyLabel={secondaryKeyMeta.label}
              keyPlaceholder={secondaryKeyMeta.placeholder}
              keyHelper={secondaryKeyMeta.helper}
              onProviderKeySave={(_provider, apiKey) => onSecondaryProviderKeySave(apiKey)}
              onProviderKeyClear={() => onSecondaryProviderKeyClear()}
              onProviderKeyTest={async () => {
                const result = await onSecondaryConnectionTest(secondaryProvider);
                return { ok: result.ok, error: result.ok ? undefined : result.detail };
              }}
              onConnectionTest={(baseUrl) => onSecondaryConnectionTest(secondaryProvider, baseUrl)}
            />
          )}

          {secondaryProviderEnabled && (
            <AccountSwitchPreflight
              slot={SLOT_SECONDARY}
              changed={secondaryAccountChanged}
              action={affectedRunsAction}
              onActionChange={onAffectedRunsActionChange}
            />
          )}

          {secondaryProviderEnabled && (
            <RateLimitFields
              idPrefix="secondaryProvider"
              legend={`${SLOT_LABELS.secondary} rate limits`}
              rpm={secondaryProviderRequestsPerMin}
              onRpmChange={onSecondaryProviderRequestsPerMinChange}
              rpd={secondaryProviderRequestsPerDay}
              onRpdChange={onSecondaryProviderRequestsPerDayChange}
              tpm={secondaryProviderTokensPerMin}
              onTpmChange={onSecondaryProviderTokensPerMinChange}
            />
          )}
        </div>

        <div>
          <label htmlFor="pricingSourceMode" className="block text-sm font-medium text-foreground mb-2">
            Pricing source
          </label>
          <select
            id="pricingSourceMode"
            value={pricingSourceMode}
            onChange={(e) => onPricingSourceModeChange(e.target.value)}
            className={`w-full ${selectBase}`}
          >
            <option value="auto">Auto (recommended)</option>
            <option value="litellm">LiteLLM catalog</option>
            <option value="free">None (free local models)</option>
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Auto picks by provider. Choose None only if your endpoint serves free local models.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

// Saves on blur or Enter -- one mutation per committed edit, not per keystroke.
function OllamaNumCtxField({
  entry,
  onUpdate,
}: {
  entry: StageTunables['ollamaNumCtx'];
  onUpdate: (payload: UpdateSettingsPayload) => void;
}) {
  const upstream = (entry.value as number | null) ?? null;
  const [draft, setDraft] = useState(upstream === null ? '' : String(upstream));
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Skip re-syncing upstream into the draft while the user is mid-edit; a
  // TanStack Query background refetch would otherwise overwrite typed input.
  useEffect(() => {
    if (inputRef.current && document.activeElement === inputRef.current) {
      return;
    }
    setDraft(upstream === null ? '' : String(upstream));
  }, [upstream]);

  const commit = () => {
    const parsed = draft === '' ? null : parseInt(draft, 10);
    const normalized = parsed !== null && !Number.isFinite(parsed) ? null : parsed;
    if (normalized === upstream) return;
    onUpdate({ ollamaNumCtx: normalized });
  };

  return (
    <div>
      <label htmlFor="ollamaNumCtx" className="block text-sm font-medium text-foreground mb-2">
        Context window (num_ctx)
      </label>
      <DraftNumberInput
        id="ollamaNumCtx"
        min={512}
        max={131072}
        step={512}
        placeholder="Blank = model default"
        value={parseOptionalNumber(draft)}
        fallback={null}
        parse={parseOptionalNumber}
        onChange={(v) => setDraft(v === null ? '' : String(v))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm disabled:opacity-60"
      />
      <p className="mt-1 text-sm text-muted-foreground">
        {entry.envOverride
          ? `Default from ${entry.envOverride}.`
          : "Ollama default (often 2048) silently truncates long prompts. Set to your model's context limit (8192+)."}
      </p>
    </div>
  );
}

export default LLMProviderSection;

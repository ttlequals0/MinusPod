import { useEffect, useRef, useState } from 'react';
import type { LlmProvider, StageTunables, UpdateSettingsPayload } from '../../api/types';
import { LLM_PROVIDER_LABELS, LLM_PROVIDER_OPTIONS, LLM_PROVIDERS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ConnectionTestButton from './ConnectionTestButton';
import ProviderKeyField from './ProviderKeyField';
import type { ConnectionTestResult, ProviderName, ProviderStatus, ProviderTestResult, ProvidersResponse } from '../../api/providers';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import { selectBase } from '../../components/fieldStyles';

interface LLMProviderSectionProps {
  llmProvider: LlmProvider;
  openaiBaseUrl: string;
  pricingSourceMode: string;
  onProviderChange: (provider: LlmProvider) => void;
  onBaseUrlChange: (url: string) => void;
  onPricingSourceModeChange: (mode: string) => void;
  providersState: ProvidersResponse | null;
  onProviderKeySave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onProviderKeyClear: (provider: ProviderName) => Promise<void>;
  onProviderKeyTest: (provider: ProviderName) => Promise<ProviderTestResult>;
  onConnectionTest: (provider: 'openai' | 'ollama' | 'anthropic' | 'openrouter', baseUrl?: string) => Promise<ConnectionTestResult>;
  ollamaNumCtx?: StageTunables['ollamaNumCtx'];
  onOllamaNumCtxUpdate?: (payload: UpdateSettingsPayload) => void;
  llmJsonSchemaEnabled: boolean;
  onLlmJsonSchemaEnabledChange: (enabled: boolean) => void;
  // Optional second provider config; ad detection, verification, chapters,
  // and the reviewer can each route to it via the 'secondary' slot instead
  // of the primary provider above. Off by default.
  secondaryProviderEnabled: boolean;
  onSecondaryProviderEnabledChange: (enabled: boolean) => void;
  secondaryProvider: LlmProvider;
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
  idPrefix, rpm, onRpmChange, rpd, onRpdChange, tpm, onTpmChange,
}: {
  idPrefix: string;
  rpm: number;
  onRpmChange: (value: number) => void;
  rpd: number;
  onRpdChange: (value: number) => void;
  tpm: number;
  onTpmChange: (value: number) => void;
}) {
  return (
    <div>
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
    </div>
  );
}

const NONE_STATUS: ProviderStatus = { configured: false, source: 'none' };

function keyProviderFor(p: LlmProvider): Exclude<ProviderName, 'secondary'> | null {
  if (p === LLM_PROVIDERS.ANTHROPIC) return 'anthropic';
  if (p === LLM_PROVIDERS.OPENROUTER) return 'openrouter';
  if (p === LLM_PROVIDERS.OPENAI_COMPATIBLE) return 'openai';
  if (p === LLM_PROVIDERS.OLLAMA) return 'ollama';
  return null;
}

const KEY_META: Record<ProviderName, { placeholder: string; label: string; helper?: string }> = {
  anthropic:  { placeholder: 'sk-ant-...', label: 'Anthropic API key' },
  openrouter: { placeholder: 'sk-or-v1-...', label: 'OpenRouter API key', helper: 'Get your API key from openrouter.ai/keys' },
  openai:     { placeholder: 'sk-...', label: 'API key' },
  whisper:    { placeholder: 'sk-...', label: 'API key' },
  ollama:     { placeholder: 'Leave blank for local Ollama; paste an ollama.com key for Cloud', label: 'Ollama API key', helper: 'Local Ollama does not require a key. Ollama Cloud keys come from ollama.com/settings/keys.' },
  // Never read directly: the secondary block looks up its label/placeholder
  // by the chosen provider TYPE (keyProviderFor(secondaryProvider)) since
  // one secondary secret covers whichever type is selected. Present only
  // so KEY_META stays a total Record over ProviderName.
  secondary:  { placeholder: '', label: 'API key' },
};

// The provider type select, base URL (where the type needs one), key field,
// and connection test: identical controls for the primary and secondary
// provider config, differing only in which state/handlers they're bound to.
interface ProviderFieldsProps {
  providerSelectId: string;
  providerLabel: string;
  provider: LlmProvider;
  onProviderChange: (provider: LlmProvider) => void;
  baseUrlInputId: string;
  baseUrlLabel: string;
  baseUrl: string;
  onBaseUrlChange: (url: string) => void;
  keyProvider: ProviderName;
  keyStatus: ProviderStatus;
  cryptoReady: boolean;
  keyLabel: string;
  keyPlaceholder: string;
  keyHelper?: string;
  onProviderKeySave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onProviderKeyClear: (provider: ProviderName) => Promise<void>;
  onProviderKeyTest: (provider: ProviderName) => Promise<ProviderTestResult>;
  onConnectionTest: (baseUrl?: string) => Promise<ConnectionTestResult>;
}

function ProviderFields({
  providerSelectId, providerLabel, provider, onProviderChange,
  baseUrlInputId, baseUrlLabel, baseUrl, onBaseUrlChange,
  keyProvider, keyStatus, cryptoReady, keyLabel, keyPlaceholder, keyHelper,
  onProviderKeySave, onProviderKeyClear, onProviderKeyTest, onConnectionTest,
}: ProviderFieldsProps) {
  const hasBaseUrl = provider === LLM_PROVIDERS.OPENAI_COMPATIBLE || provider === LLM_PROVIDERS.OLLAMA;
  const hasFixedEndpoint = provider === LLM_PROVIDERS.ANTHROPIC || provider === LLM_PROVIDERS.OPENROUTER;

  return (
    <>
      <div>
        <label htmlFor={providerSelectId} className="block text-sm font-medium text-foreground mb-2">
          {providerLabel}
        </label>
        <select
          id={providerSelectId}
          value={provider}
          onChange={(e) => onProviderChange(e.target.value as LlmProvider)}
          className={`w-full ${selectBase}`}
        >
          {LLM_PROVIDER_OPTIONS.map((p) => (
            <option key={p} value={p}>{LLM_PROVIDER_LABELS[p]}</option>
          ))}
        </select>
      </div>

      {hasBaseUrl && (
        <div>
          <label htmlFor={baseUrlInputId} className="block text-sm font-medium text-foreground mb-2">
            {baseUrlLabel}
          </label>
          <input
            type="text"
            id={baseUrlInputId}
            value={baseUrl}
            onChange={(e) => onBaseUrlChange(e.target.value)}
            placeholder="http://localhost:11434/v1"
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
          />
          <p className="mt-1 text-sm text-muted-foreground">
            {provider === LLM_PROVIDERS.OLLAMA
              ? 'Ollama server URL (e.g. http://localhost:11434)'
              : 'OpenAI-compatible API endpoint (must end with /v1)'}
          </p>
          <ConnectionTestButton
            key={`${provider}|${baseUrl}|${keyStatus.configured}`}
            onTest={() => onConnectionTest(baseUrl)}
          />
        </div>
      )}

      <ProviderKeyField
        provider={keyProvider}
        status={keyStatus}
        cryptoReady={cryptoReady}
        placeholder={keyPlaceholder}
        label={keyLabel}
        helper={keyHelper}
        onSave={onProviderKeySave}
        onClear={onProviderKeyClear}
        onTest={onProviderKeyTest}
      />

      {hasFixedEndpoint && (
        <ConnectionTestButton
          key={`${provider}|${keyStatus.configured}`}
          onTest={() => onConnectionTest()}
        />
      )}
    </>
  );
}

function LLMProviderSection({
  llmProvider,
  openaiBaseUrl,
  pricingSourceMode,
  onProviderChange,
  onBaseUrlChange,
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
}: LLMProviderSectionProps) {
  const keyProvider = keyProviderFor(llmProvider);
  const status = keyProvider && providersState ? providersState[keyProvider] : NONE_STATUS;
  const cryptoReady = providersState?.cryptoReady ?? false;

  const secondaryKeyStatus: ProviderStatus = {
    configured: secondaryProviderApiKeyConfigured,
    source: secondaryProviderApiKeyConfigured ? 'db' : 'none',
  };
  const secondaryKeyMeta = KEY_META[keyProviderFor(secondaryProvider) ?? 'anthropic'];

  return (
    <CollapsibleSection title="LLM Provider" defaultOpen>
      <div className="space-y-4">
        <ProviderFields
          providerSelectId="llmProvider"
          providerLabel="Provider"
          provider={llmProvider}
          onProviderChange={onProviderChange}
          baseUrlInputId="openaiBaseUrl"
          baseUrlLabel="Base URL"
          baseUrl={openaiBaseUrl}
          onBaseUrlChange={onBaseUrlChange}
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
                : llmProvider === LLM_PROVIDERS.ANTHROPIC
                  ? 'anthropic'
                  : 'openrouter',
            baseUrl,
          )}
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
              Asks the endpoint to enforce a JSON schema on detection and
              review responses. Servers that implement it return cleaner
              JSON. Not every OpenAI-compatible server does: the app probes
              once and falls back to plain JSON mode, but verify that yours
              supports response_format with type json_schema.
            </p>
          </div>
        )}

        <RateLimitFields
          idPrefix="provider"
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
                ariaLabel="Enable secondary provider"
              />
              <span className="text-sm font-medium text-foreground">
                Secondary provider
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              A second full provider that ad detection, verification, chapters, and the reviewer can each route to instead of the primary provider above. Off by default: every stage stays on the primary provider until you point it here.
            </p>
          </div>

          {secondaryProviderEnabled && (
            <ProviderFields
              providerSelectId="secondaryProviderType"
              providerLabel="Secondary provider type"
              provider={secondaryProvider}
              onProviderChange={onSecondaryProviderChange}
              baseUrlInputId="secondaryProviderBaseUrl"
              baseUrlLabel="Secondary base URL"
              baseUrl={secondaryProviderBaseUrl}
              onBaseUrlChange={onSecondaryProviderBaseUrlChange}
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
            <RateLimitFields
              idPrefix="secondaryProvider"
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

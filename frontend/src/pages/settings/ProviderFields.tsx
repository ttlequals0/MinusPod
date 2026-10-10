import type { LlmProvider } from '../../api/types';
import { LLM_PROVIDER_LABELS, LLM_PROVIDER_OPTIONS, LLM_PROVIDERS } from '../../api/types';
import type { ConnectionTestResult, ProviderName, ProviderStatus, ProviderTestResult } from '../../api/providers';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import { inputBase, selectBase } from '../../components/fieldStyles';
import ConnectionTestButton from './ConnectionTestButton';
import ProviderKeyField from './ProviderKeyField';

// Placeholder shown for a blank timeout/retries override: mirrors the
// provider-type fallback in get_llm_timeout/get_llm_max_retries (llm_client.py).
function providerDefaults(type: LlmProvider | ''): { timeout: number; retries: number } {
  if (type === LLM_PROVIDERS.ANTHROPIC || type === LLM_PROVIDERS.OPENROUTER) {
    return { timeout: 120, retries: 3 };
  }
  if (type === LLM_PROVIDERS.TYPESAFE || type === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE) {
    return { timeout: 60, retries: 2 };
  }
  return { timeout: 600, retries: 2 };
}

export function keyProviderFor(
  p: LlmProvider | '',
): Exclude<ProviderName, 'secondary' | 'failover' | 'failover-whisper'> | null {
  if (p === LLM_PROVIDERS.ANTHROPIC) return 'anthropic';
  if (p === LLM_PROVIDERS.OPENROUTER) return 'openrouter';
  if (p === LLM_PROVIDERS.OPENAI_COMPATIBLE) return 'openai';
  if (p === LLM_PROVIDERS.OLLAMA) return 'ollama';
  if (p === LLM_PROVIDERS.TYPESAFE) return 'typesafe';
  if (p === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE) return 'systemone-compatible';
  return null;
}

export const KEY_META: Record<ProviderName, { placeholder: string; label: string; helper?: string }> = {
  anthropic:  { placeholder: 'sk-ant-...', label: 'Anthropic API key' },
  openrouter: { placeholder: 'sk-or-v1-...', label: 'OpenRouter API key', helper: 'Get your API key from openrouter.ai/keys' },
  openai:     { placeholder: 'sk-...', label: 'API key' },
  whisper:    { placeholder: 'sk-...', label: 'API key' },
  ollama:     { placeholder: 'Leave blank for local Ollama; paste an ollama.com key for Cloud', label: 'Ollama API key', helper: 'Local Ollama does not require a key. Ollama Cloud keys come from ollama.com/settings/keys.' },
  // Never read directly: these slots look up their label/placeholder by the
  // chosen provider TYPE (keyMetaForType) since one secret covers whichever
  // type is selected. Present only so KEY_META stays a total Record.
  secondary:  { placeholder: '', label: 'API key' },
  failover:   { placeholder: '', label: 'API key' },
  'failover-whisper': { placeholder: '', label: 'API key' },
  typesafe: { placeholder: '', label: 'TypeSafe API key' },
  'systemone-compatible': { placeholder: '', label: 'System One API key' },
};

export function keyMetaForType(type: LlmProvider | '') {
  return KEY_META[keyProviderFor(type) ?? 'anthropic'];
}

// The provider type select, base URL (where the type needs one), key field,
// and connection test: identical controls for Provider A, Provider B and the
// failover account, differing only in which state/handlers they're bound to.
interface ProviderFieldsProps {
  providerSelectId: string;
  providerLabel: string;
  provider: LlmProvider | '';
  onProviderChange: (provider: LlmProvider) => void;
  baseUrlInputId: string;
  baseUrlLabel: string;
  baseUrl: string;
  onBaseUrlChange: (url: string) => void;
  // "Provider A", "Provider B" or "Failover": labels the timeout/retries inputs.
  slotLabel: string;
  timeoutSeconds: number | null;
  onTimeoutChange: (value: number | null) => void;
  maxRetries: number | null;
  onMaxRetriesChange: (value: number | null) => void;
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
  allowSystemOne?: boolean;
}

export function ProviderFields({
  providerSelectId, providerLabel, provider, onProviderChange,
  baseUrlInputId, baseUrlLabel, baseUrl, onBaseUrlChange,
  slotLabel, timeoutSeconds, onTimeoutChange, maxRetries, onMaxRetriesChange,
  keyProvider, keyStatus, cryptoReady, keyLabel, keyPlaceholder, keyHelper,
  onProviderKeySave, onProviderKeyClear, onProviderKeyTest, onConnectionTest,
  allowSystemOne = true,
}: ProviderFieldsProps) {
  const hasBaseUrl = provider === LLM_PROVIDERS.OPENAI_COMPATIBLE || provider === LLM_PROVIDERS.OLLAMA || provider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE;
  const hasFixedEndpoint = provider === LLM_PROVIDERS.ANTHROPIC || provider === LLM_PROVIDERS.OPENROUTER || provider === LLM_PROVIDERS.TYPESAFE;
  const providerOptions = allowSystemOne ? LLM_PROVIDER_OPTIONS : LLM_PROVIDER_OPTIONS.filter(
    (p) => p !== LLM_PROVIDERS.TYPESAFE && p !== LLM_PROVIDERS.SYSTEMONE_COMPATIBLE,
  );
  const defaults = providerDefaults(provider);
  const nativeSystemOne = provider === LLM_PROVIDERS.TYPESAFE || provider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE;

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
          {/* No type saved yet: without an option of its own the select would
              render blank, reading as the first provider in the list. */}
          {!provider && <option value="">Choose a provider</option>}
          {providerOptions.map((p) => (
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
            className={`w-full ${inputBase} placeholder:text-muted-foreground font-mono`}
          />
          <p className="mt-1 text-sm text-muted-foreground">
            {provider === LLM_PROVIDERS.OLLAMA
              ? 'Ollama server URL (e.g. http://localhost:11434)'
              : provider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE
                ? 'Base URL; /systemone is added automatically.'
                : 'OpenAI-compatible API endpoint (must end with /v1)'}
          </p>
          <ConnectionTestButton
            key={`${provider}|${baseUrl}|${keyStatus.configured}`}
            onTest={() => onConnectionTest(baseUrl)}
          />
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label htmlFor={`${providerSelectId}TimeoutSeconds`} className="block text-sm font-medium text-foreground mb-2">
            {slotLabel} request timeout (seconds)
          </label>
          <DraftNumberInput
            id={`${providerSelectId}TimeoutSeconds`}
            min={nativeSystemOne ? Number.MIN_VALUE : 10}
            max={nativeSystemOne ? undefined : 3600}
            step={nativeSystemOne ? 'any' : 1}
            placeholder={String(defaults.timeout)}
            value={timeoutSeconds}
            fallback={null}
            parse={parseOptionalNumber}
            onChange={onTimeoutChange}
            className={`w-full ${inputBase} placeholder:text-muted-foreground`}
          />
        </div>
        <div>
          <label htmlFor={`${providerSelectId}MaxRetries`} className="block text-sm font-medium text-foreground mb-2">
            {slotLabel} max retries
          </label>
          <DraftNumberInput
            id={`${providerSelectId}MaxRetries`}
            min={0}
            max={10}
            step={1}
            placeholder={String(defaults.retries)}
            value={maxRetries}
            fallback={null}
            parse={parseOptionalNumber}
            onChange={onMaxRetriesChange}
            className={`w-full ${inputBase} placeholder:text-muted-foreground`}
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Blank uses the provider default. 0 sends one request with no retries.
          </p>
        </div>
      </div>

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

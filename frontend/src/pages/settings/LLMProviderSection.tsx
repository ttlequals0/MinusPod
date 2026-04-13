import type { LlmProvider } from '../../api/types';
import { LLM_PROVIDERS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ProviderKeyField from './ProviderKeyField';
import type { ProviderName, ProviderStatus, ProviderTestResult, ProvidersResponse } from '../../api/providers';

interface LLMProviderSectionProps {
  llmProvider: LlmProvider;
  openaiBaseUrl: string;
  onProviderChange: (provider: LlmProvider) => void;
  onBaseUrlChange: (url: string) => void;
  providersState: ProvidersResponse | null;
  onProviderKeySave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onProviderKeyClear: (provider: ProviderName) => Promise<void>;
  onProviderKeyTest: (provider: ProviderName) => Promise<ProviderTestResult>;
}

const NONE_STATUS: ProviderStatus = { configured: false, source: 'none' };

function keyProviderFor(p: LlmProvider): ProviderName | null {
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
};

function LLMProviderSection({
  llmProvider,
  openaiBaseUrl,
  onProviderChange,
  onBaseUrlChange,
  providersState,
  onProviderKeySave,
  onProviderKeyClear,
  onProviderKeyTest,
}: LLMProviderSectionProps) {
  const keyProvider = keyProviderFor(llmProvider);
  const status = keyProvider && providersState ? providersState[keyProvider] : NONE_STATUS;
  const cryptoReady = providersState?.cryptoReady ?? false;

  return (
    <CollapsibleSection title="LLM Provider" defaultOpen>
      <div className="space-y-4">
        <div>
          <label htmlFor="llmProvider" className="block text-sm font-medium text-foreground mb-2">
            Provider
          </label>
          <select
            id="llmProvider"
            value={llmProvider}
            onChange={(e) => onProviderChange(e.target.value as LlmProvider)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value={LLM_PROVIDERS.ANTHROPIC}>Anthropic</option>
            <option value={LLM_PROVIDERS.OPENROUTER}>OpenRouter</option>
            <option value={LLM_PROVIDERS.OPENAI_COMPATIBLE}>OpenAI Compatible</option>
            <option value={LLM_PROVIDERS.OLLAMA}>Ollama</option>
          </select>
        </div>

        {(llmProvider === LLM_PROVIDERS.OPENAI_COMPATIBLE || llmProvider === LLM_PROVIDERS.OLLAMA) && (
          <div>
            <label htmlFor="openaiBaseUrl" className="block text-sm font-medium text-foreground mb-2">
              Base URL
            </label>
            <input
              type="text"
              id="openaiBaseUrl"
              value={openaiBaseUrl}
              onChange={(e) => onBaseUrlChange(e.target.value)}
              placeholder="http://localhost:11434/v1"
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
            />
            <p className="mt-1 text-sm text-muted-foreground">
              {llmProvider === LLM_PROVIDERS.OLLAMA
                ? 'Ollama server URL (e.g. http://localhost:11434)'
                : 'OpenAI-compatible API endpoint (must end with /v1)'}
            </p>
          </div>
        )}

        {keyProvider && (
          <ProviderKeyField
            provider={keyProvider}
            status={status}
            cryptoReady={cryptoReady}
            placeholder={KEY_META[keyProvider].placeholder}
            label={KEY_META[keyProvider].label}
            helper={KEY_META[keyProvider].helper}
            onSave={onProviderKeySave}
            onClear={onProviderKeyClear}
            onTest={onProviderKeyTest}
          />
        )}
      </div>
    </CollapsibleSection>
  );
}

export default LLMProviderSection;

import type { LlmProvider } from '../../api/types';
import { LLM_PROVIDERS } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';

interface LLMProviderSectionProps {
  llmProvider: LlmProvider;
  openaiBaseUrl: string;
  apiKeyConfigured: boolean | undefined;
  openrouterApiKey: string;
  openrouterApiKeyConfigured: boolean | undefined;
  onProviderChange: (provider: LlmProvider) => void;
  onBaseUrlChange: (url: string) => void;
  onOpenrouterApiKeyChange: (key: string) => void;
}

function LLMProviderSection({
  llmProvider,
  openaiBaseUrl,
  apiKeyConfigured,
  openrouterApiKey,
  openrouterApiKeyConfigured,
  onProviderChange,
  onBaseUrlChange,
  onOpenrouterApiKeyChange,
}: LLMProviderSectionProps) {
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

        {llmProvider === LLM_PROVIDERS.OPENROUTER && (
          <div>
            <label htmlFor="openrouterApiKey" className="block text-sm font-medium text-foreground mb-2">
              OpenRouter API Key
            </label>
            <input
              type="password"
              id="openrouterApiKey"
              value={openrouterApiKey}
              onChange={(e) => onOpenrouterApiKeyChange(e.target.value)}
              placeholder={openrouterApiKeyConfigured ? '(configured - enter new value to change)' : 'sk-or-v1-...'}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
            />
            <p className="mt-1 text-sm text-muted-foreground">
              Get your API key from openrouter.ai/keys
            </p>
            <div className="mt-2">
              {openrouterApiKeyConfigured ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  Configured
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                  Not configured
                </span>
              )}
            </div>
          </div>
        )}

        <div>
          <p className="text-sm font-medium text-foreground mb-1">API Key Status</p>
          {llmProvider === LLM_PROVIDERS.OPENROUTER ? (
            openrouterApiKeyConfigured ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                Configured
              </span>
            ) : (
              <>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                  Not configured
                </span>
                <p className="mt-2 text-sm text-muted-foreground">
                  Set OPENROUTER_API_KEY environment variable or enter it above
                </p>
              </>
            )
          ) : apiKeyConfigured ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
              Configured (env)
            </span>
          ) : llmProvider === LLM_PROVIDERS.ANTHROPIC ? (
            <>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
                <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                Not configured
              </span>
              <p className="mt-2 text-sm text-muted-foreground">
                Set ANTHROPIC_API_KEY environment variable to enable Anthropic API access
              </p>
            </>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-muted text-muted-foreground">
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
              Not required
            </span>
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default LLMProviderSection;

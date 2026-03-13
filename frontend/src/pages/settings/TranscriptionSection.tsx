import type { WhisperModel, WhisperBackend, WhisperApiConfig } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';

interface TranscriptionSectionProps {
  whisperModel: string;
  whisperModels: WhisperModel[] | undefined;
  onWhisperModelChange: (model: string) => void;
  whisperBackend: WhisperBackend;
  onWhisperBackendChange: (backend: WhisperBackend) => void;
  apiConfig: WhisperApiConfig;
  onApiConfigChange: (field: keyof WhisperApiConfig, value: string) => void;
}

function TranscriptionSection({
  whisperModel,
  whisperModels,
  onWhisperModelChange,
  whisperBackend,
  onWhisperBackendChange,
  apiConfig,
  onApiConfigChange,
}: TranscriptionSectionProps) {
  return (
    <CollapsibleSection title="Transcription">
      <div className="space-y-4">
        <div>
          <label htmlFor="whisperBackend" className="block text-sm font-medium text-foreground mb-2">
            Backend
          </label>
          <select
            id="whisperBackend"
            value={whisperBackend}
            onChange={(e) => onWhisperBackendChange(e.target.value as WhisperBackend)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="local">Local (faster-whisper)</option>
            <option value="openai-api">Remote API (OpenAI-compatible)</option>
          </select>
        </div>

        {whisperBackend === 'local' && (
          <div>
            <label htmlFor="whisperModel" className="block text-sm font-medium text-foreground mb-2">
              Whisper Model
            </label>
            <select
              id="whisperModel"
              value={whisperModel}
              onChange={(e) => onWhisperModelChange(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {whisperModels?.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} - {model.vram} VRAM, {model.quality}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Larger models produce better transcriptions but require more GPU memory
            </p>
            {whisperModels && (
              <div className="mt-3 text-xs text-muted-foreground">
                <span className="font-medium">Current:</span> {whisperModels.find(m => m.id === whisperModel)?.speed || ''}
              </div>
            )}
          </div>
        )}

        {whisperBackend === 'openai-api' && (
          <>
            <div>
              <label htmlFor="whisperApiBaseUrl" className="block text-sm font-medium text-foreground mb-2">
                API Base URL
              </label>
              <input
                type="text"
                id="whisperApiBaseUrl"
                value={apiConfig.baseUrl}
                onChange={(e) => onApiConfigChange('baseUrl', e.target.value)}
                placeholder="http://host.docker.internal:8765/v1"
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                OpenAI-compatible transcription endpoint (e.g. whisper.cpp, Groq, OpenAI)
              </p>
            </div>

            <div>
              <label htmlFor="whisperApiKey" className="block text-sm font-medium text-foreground mb-2">
                API Key
              </label>
              <input
                type="password"
                id="whisperApiKey"
                value={apiConfig.apiKey}
                onChange={(e) => onApiConfigChange('apiKey', e.target.value)}
                placeholder={apiConfig.apiKeyConfigured ? '(configured - enter new value to change)' : '(optional - leave blank if not required)'}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
              <div className="mt-1">
                {apiConfig.apiKeyConfigured ? (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                    Configured
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-muted text-muted-foreground">
                    <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
                    Not configured (optional for local servers)
                  </span>
                )}
              </div>
            </div>

            <div>
              <label htmlFor="whisperApiModel" className="block text-sm font-medium text-foreground mb-2">
                Model Name
              </label>
              <input
                type="text"
                id="whisperApiModel"
                value={apiConfig.model}
                onChange={(e) => onApiConfigChange('model', e.target.value)}
                placeholder="whisper-1"
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                Model identifier sent to the API (e.g. whisper-1, whisper-large-v3-turbo)
              </p>
            </div>
          </>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default TranscriptionSection;

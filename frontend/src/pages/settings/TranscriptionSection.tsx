import { WHISPER_BACKENDS, type WhisperModel, type WhisperBackend, type WhisperApiConfig } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ProviderKeyField from './ProviderKeyField';
import type { ProviderName, ProviderStatus, ProviderTestResult, ProvidersResponse } from '../../api/providers';

interface TranscriptionSectionProps {
  whisperModel: string;
  whisperModels: WhisperModel[] | undefined;
  onWhisperModelChange: (model: string) => void;
  whisperBackend: WhisperBackend;
  onWhisperBackendChange: (backend: WhisperBackend) => void;
  apiConfig: WhisperApiConfig;
  onApiConfigChange: (field: keyof WhisperApiConfig, value: string) => void;
  providersState: ProvidersResponse | null;
  onProviderKeySave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onProviderKeyClear: (provider: ProviderName) => Promise<void>;
  onProviderKeyTest: (provider: ProviderName) => Promise<ProviderTestResult>;
}

const NONE_STATUS: ProviderStatus = { configured: false, source: 'none' };

function TranscriptionSection({
  whisperModel,
  whisperModels,
  onWhisperModelChange,
  whisperBackend,
  onWhisperBackendChange,
  apiConfig,
  onApiConfigChange,
  providersState,
  onProviderKeySave,
  onProviderKeyClear,
  onProviderKeyTest,
}: TranscriptionSectionProps) {
  const whisperStatus = providersState?.whisper ?? NONE_STATUS;
  const cryptoReady = providersState?.cryptoReady ?? false;
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
            <option value={WHISPER_BACKENDS.LOCAL}>Local (faster-whisper)</option>
            <option value={WHISPER_BACKENDS.OPENAI_API}>Remote API (OpenAI-compatible)</option>
          </select>
        </div>

        {whisperBackend === WHISPER_BACKENDS.LOCAL && (
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

        {whisperBackend === WHISPER_BACKENDS.OPENAI_API && (
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

            <ProviderKeyField
              provider="whisper"
              status={whisperStatus}
              cryptoReady={cryptoReady}
              placeholder="(optional - leave blank if not required)"
              label="API Key"
              onSave={onProviderKeySave}
              onClear={onProviderKeyClear}
              onTest={onProviderKeyTest}
            />

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

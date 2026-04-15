import { WHISPER_BACKENDS, type WhisperModel, type WhisperBackend, type WhisperApiConfig } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LanguageCombobox from '../../components/LanguageCombobox';
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
  whisperLanguage: string;
  onWhisperLanguageChange: (language: string) => void;
  softTimeoutMinutes: number;
  hardTimeoutMinutes: number;
  softMinMinutes: number;
  hardMaxMinutes: number;
  onSoftTimeoutChange: (minutes: number) => void;
  onHardTimeoutChange: (minutes: number) => void;
  onTimeoutsSave: () => void;
  timeoutsSaveIsPending: boolean;
  timeoutsSaveIsSuccess: boolean;
  timeoutsError: string | null;
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
  whisperLanguage,
  onWhisperLanguageChange,
  softTimeoutMinutes,
  hardTimeoutMinutes,
  softMinMinutes,
  hardMaxMinutes,
  onSoftTimeoutChange,
  onHardTimeoutChange,
  onTimeoutsSave,
  timeoutsSaveIsPending,
  timeoutsSaveIsSuccess,
  timeoutsError,
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

        <div className="pt-2 border-t border-border">
          <label htmlFor="whisperLanguage" className="block text-sm font-medium text-foreground mb-2">
            Language
          </label>
          <LanguageCombobox
            id="whisperLanguage"
            value={whisperLanguage || 'en'}
            onChange={onWhisperLanguageChange}
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Pinning a language keeps Whisper from misdetecting on music intros. Pick Auto-detect for multilingual podcasts. See
            {' '}<a href="https://whisper-api.com/docs/languages/" target="_blank" rel="noreferrer" className="underline hover:text-foreground">supported languages</a>.
          </p>
        </div>

        <div className="pt-2 border-t border-border space-y-3">
          <div className="flex items-center gap-3">
            <label htmlFor="softTimeoutMinutes" className="text-sm text-muted-foreground w-36">
              Soft timeout:
            </label>
            <input
              type="number"
              id="softTimeoutMinutes"
              value={softTimeoutMinutes}
              onChange={(e) => onSoftTimeoutChange(parseInt(e.target.value, 10) || 0)}
              min={softMinMinutes}
              max={hardMaxMinutes}
              className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <span className="text-sm text-muted-foreground">minutes (default 60)</span>
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="hardTimeoutMinutes" className="text-sm text-muted-foreground w-36">
              Hard timeout:
            </label>
            <input
              type="number"
              id="hardTimeoutMinutes"
              value={hardTimeoutMinutes}
              onChange={(e) => onHardTimeoutChange(parseInt(e.target.value, 10) || 0)}
              min={softMinMinutes + 1}
              max={hardMaxMinutes}
              className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <span className="text-sm text-muted-foreground">minutes (default 120)</span>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onTimeoutsSave}
              disabled={timeoutsSaveIsPending}
              className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm"
            >
              {timeoutsSaveIsPending ? 'Saving...' : 'Save Timeouts'}
            </button>
            {timeoutsSaveIsSuccess && !timeoutsError && (
              <span className="text-sm text-green-600 dark:text-green-400">Saved</span>
            )}
            {timeoutsError && (
              <span className="text-sm text-red-600 dark:text-red-400">{timeoutsError}</span>
            )}
          </div>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default TranscriptionSection;

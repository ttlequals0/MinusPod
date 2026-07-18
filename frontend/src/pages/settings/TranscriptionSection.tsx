import { WHISPER_BACKENDS, type WhisperModel, type WhisperBackend, type WhisperApiConfig } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LanguageCombobox from '../../components/LanguageCombobox';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import ProviderKeyField from './ProviderKeyField';
import SavedBadge from './SavedBadge';
import type { ProviderName, ProviderStatus, ProviderTestResult, ProvidersResponse } from '../../api/providers';
import { btnPrimary } from '../../components/buttonStyles';

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
  whisperComputeType: string;
  onWhisperComputeTypeChange: (computeType: string) => void;
  transcribeMaxChunkSeconds: number;
  onTranscribeMaxChunkSecondsChange: (value: number) => void;
  transcribeConcurrentChunks: number;
  onTranscribeConcurrentChunksChange: (value: number) => void;
  transcribeChunkOverlapSeconds: number;
  onTranscribeChunkOverlapSecondsChange: (value: number) => void;
  skipFlacCompression: boolean;
  onSkipFlacCompressionChange: (value: boolean) => void;
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
  whisperComputeType,
  onWhisperComputeTypeChange,
  transcribeMaxChunkSeconds,
  onTranscribeMaxChunkSecondsChange,
  transcribeConcurrentChunks,
  onTranscribeConcurrentChunksChange,
  transcribeChunkOverlapSeconds,
  onTranscribeChunkOverlapSecondsChange,
  skipFlacCompression,
  onSkipFlacCompressionChange,
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
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
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
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
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
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
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
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                Model identifier sent to the API (e.g. whisper-1, whisper-large-v3-turbo)
              </p>
            </div>

            <div className="pt-2 border-t border-border">
              <h4 className="text-sm font-medium text-foreground mb-3">Parallel chunked transcription</h4>
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <label htmlFor="transcribeMaxChunkSeconds" className="text-sm text-muted-foreground w-44">
                    Max chunk seconds:
                  </label>
                  <NumberInput
                    id="transcribeMaxChunkSeconds"
                    value={transcribeMaxChunkSeconds}
                    min={1}
                    max={7200}
                    fallback={600}
                    parse={(s) => parseInt(s, 10)}
                    onCommit={onTranscribeMaxChunkSecondsChange}
                  />
                  <span className="text-sm text-muted-foreground">600 for Whisper, 28 for Parakeet</span>
                </div>
                <div className="flex items-center gap-3">
                  <label htmlFor="transcribeConcurrentChunks" className="text-sm text-muted-foreground w-44">
                    Concurrent chunks:
                  </label>
                  <NumberInput
                    id="transcribeConcurrentChunks"
                    value={transcribeConcurrentChunks}
                    min={1}
                    max={32}
                    fallback={4}
                    parse={(s) => parseInt(s, 10)}
                    onCommit={onTranscribeConcurrentChunksChange}
                  />
                  <span className="text-sm text-muted-foreground">match backend's worker count</span>
                </div>
                <div className="flex items-center gap-3">
                  <label htmlFor="transcribeChunkOverlapSeconds" className="text-sm text-muted-foreground w-44">
                    Chunk overlap seconds:
                  </label>
                  <NumberInput
                    id="transcribeChunkOverlapSeconds"
                    value={transcribeChunkOverlapSeconds}
                    min={1}
                    max={600}
                    fallback={30}
                    parse={(s) => parseInt(s, 10)}
                    onCommit={onTranscribeChunkOverlapSecondsChange}
                  />
                  <span className="text-sm text-muted-foreground">for word-boundary dedupe</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  These tune the parallel API path: chunks are extracted with ffmpeg and submitted to the
                  remote backend concurrently. Chunks are extracted as (max chunk + overlap), so for
                  Parakeet's 30s ONNX cap set max chunk to 28 AND overlap to 1.
                </p>
              </div>
            </div>

          </>
        )}

        <div className="pt-2 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={skipFlacCompression}
              onChange={onSkipFlacCompressionChange}
              ariaLabel={skipFlacCompression ? 'Skip FLAC compression enabled' : 'Skip FLAC compression disabled'}
            />
            <span className="text-sm font-medium text-foreground">
              Skip FLAC compression
            </span>
          </label>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload the preprocessed WAV directly to the Whisper API instead of re-encoding to FLAC first.
            Only applies when the Whisper backend is set to API. Useful for self-hosted Whisper servers
            that accept uncompressed audio. Default off so that public OpenAI / OpenRouter endpoints
            stay under their upload size limits.
          </p>
        </div>

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

        {whisperBackend === WHISPER_BACKENDS.LOCAL && (
          <div className="pt-2 border-t border-border">
            <label htmlFor="whisperComputeType" className="block text-sm font-medium text-foreground mb-2">
              GPU compute type
            </label>
            <select
              id="whisperComputeType"
              value={whisperComputeType || 'auto'}
              onChange={(e) => onWhisperComputeTypeChange(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
            >
              <option value="auto">Auto (float16 on CUDA, int8 on CPU)</option>
              <option value="float16">float16 (Volta and newer: V100, RTX 20xx+, A100, H100)</option>
              <option value="int8_float16">int8_float16 (Volta and newer; lower VRAM)</option>
              <option value="int8">int8 (universal; Pascal GTX 10xx should pick this)</option>
              <option value="float32">float32 (Maxwell GTX 9xx or Pascal P100)</option>
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Older GPUs (Pascal, Maxwell) cannot run float16. On init failure the server retries int8_float16, then int8, then float32 automatically. See the README &quot;GPU Compute Type&quot; section for a per-GPU table.
            </p>
          </div>
        )}

        <div className="pt-2 border-t border-border space-y-3">
          <div className="flex items-center gap-3">
            <label htmlFor="softTimeoutMinutes" className="text-sm text-muted-foreground w-36">
              Soft timeout:
            </label>
            <NumberInput
              id="softTimeoutMinutes"
              value={softTimeoutMinutes}
              min={softMinMinutes}
              max={hardMaxMinutes}
              fallback={softMinMinutes}
              parse={(s) => parseInt(s, 10)}
              onCommit={onSoftTimeoutChange}
            />
            <span className="text-sm text-muted-foreground">minutes (default 60)</span>
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="hardTimeoutMinutes" className="text-sm text-muted-foreground w-36">
              Hard timeout:
            </label>
            <NumberInput
              id="hardTimeoutMinutes"
              value={hardTimeoutMinutes}
              min={softMinMinutes + 1}
              max={hardMaxMinutes}
              fallback={softMinMinutes + 1}
              parse={(s) => parseInt(s, 10)}
              onCommit={onHardTimeoutChange}
            />
            <span className="text-sm text-muted-foreground">minutes (default 120)</span>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onTimeoutsSave}
              disabled={timeoutsSaveIsPending}
              className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm`}
            >
              {timeoutsSaveIsPending ? 'Saving...' : 'Save Timeouts'}
            </button>
            {timeoutsSaveIsSuccess && !timeoutsError && <SavedBadge />}
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

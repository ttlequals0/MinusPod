import { useState, useEffect, useMemo } from 'react';
import { useSyncFromQuery } from '../hooks/useSyncFromQuery';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing, refreshModels, getRetention, updateRetention, getProcessingTimeouts, updateProcessingTimeouts, getAudioSettings, updateAudioSettings } from '../api/settings';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';
import type { LlmProvider, WhisperBackend, WhisperApiConfig, UpdateSettingsPayload } from '../api/types';
import { LLM_PROVIDERS } from '../api/types';

import SystemStatusSection from './settings/SystemStatusSection';
import StorageRetentionSection from './settings/StorageRetentionSection';
import DataManagementSection from './settings/DataManagementSection';
import WebhooksSection from './settings/WebhooksSection';
import SecuritySection from './settings/SecuritySection';
import ProcessingQueueSection from './settings/ProcessingQueueSection';
import AppearanceSection from './settings/AppearanceSection';
import PodcastIndexSection from './settings/PodcastIndexSection';
import LLMProviderSection from './settings/LLMProviderSection';
import {
  listProviders,
  updateProvider,
  clearProvider,
  testProvider,
  type ProviderName,
  type ProvidersResponse,
} from '../api/providers';
import AIModelsSection from './settings/AIModelsSection';
import StageTunablesSection from './settings/StageTunablesSection';
import TranscriptionSection from './settings/TranscriptionSection';
import AudioSection from './settings/AudioSection';
import AdDetectionSection from './settings/AdDetectionSection';
import GlobalDefaultsSection from './settings/GlobalDefaultsSection';
import Podcasting20Section from './settings/Podcasting20Section';
import PromptsSection from './settings/PromptsSection';
import ExperimentsSection from './settings/ExperimentsSection';
import AdReviewerSection from './settings/AdReviewerSection';
import CommunityPatternsSection from './settings/CommunityPatternsSection';
import { formatModelLabel } from './settings/settingsUtils';

function SettingsGroupHeader({ title }: { title: string }) {
  return (
    <div className="pt-4 pb-1">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        {title}
      </h3>
    </div>
  );
}

function Settings() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const { isPasswordSet, logout, refreshStatus } = useAuth();

  const [systemPrompt, setSystemPrompt] = useState('');
  const [verificationPrompt, setVerificationPrompt] = useState('');
  const [reviewer, setReviewer] = useState({
    enabled: false,
    model: 'same_as_pass',
    maxShift: 60,
    reviewPrompt: '',
    resurrectPrompt: '',
  });
  const [selectedModel, setSelectedModel] = useState('');
  const [verificationModel, setVerificationModel] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(true);
  const [maxFeedEpisodes, setMaxFeedEpisodes] = useState(300);
  const [onlyExposeProcessedDefault, setOnlyExposeProcessedDefault] = useState(false);
  const [audioBitrate, setAudioBitrate] = useState('128k');
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(true);
  const [chaptersEnabled, setChaptersEnabled] = useState(true);
  const [chaptersModel, setChaptersModel] = useState('');
  const [minCutConfidence, setMinCutConfidence] = useState(0.80);
  const [llmProvider, setLlmProvider] = useState<LlmProvider>(LLM_PROVIDERS.ANTHROPIC);
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('http://localhost:8000/v1');
  const [whisperBackend, setWhisperBackend] = useState<WhisperBackend>('local');
  const [whisperApiConfig, setWhisperApiConfig] = useState<WhisperApiConfig>({
    baseUrl: '', model: 'whisper-1',
  });
  const [whisperLanguage, setWhisperLanguage] = useState('en');
  const [whisperComputeType, setWhisperComputeType] = useState('auto');
  const [providersState, setProvidersState] = useState<ProvidersResponse | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const reloadProviders = () =>
    listProviders()
      .then((r) => { setProvidersState(r); setProvidersError(null); })
      .catch((e) => setProvidersError(e instanceof Error ? e.message : 'Failed to load providers'));

  useEffect(() => { reloadProviders(); }, []);

  const handleProviderKeySave = async (provider: ProviderName, apiKey: string) => {
    await updateProvider(provider, { apiKey });
    await reloadProviders();
  };
  const handleProviderKeyClear = async (provider: ProviderName) => {
    await clearProvider(provider);
    await reloadProviders();
  };
  const handleProviderKeyTest = (provider: ProviderName) => testProvider(provider);
  const [podcastIndexApiKey, setPodcastIndexApiKey] = useState('');
  const [podcastIndexApiSecret, setPodcastIndexApiSecret] = useState('');
  const [retentionDays, setRetentionDays] = useState(30);
  const [keepOriginalAudio, setKeepOriginalAudio] = useState(true);
  const [softTimeoutMinutes, setSoftTimeoutMinutes] = useState(60);
  const [hardTimeoutMinutes, setHardTimeoutMinutes] = useState(120);
  const [timeoutsError, setTimeoutsError] = useState<string | null>(null);
  const [retentionEnabled, setRetentionEnabled] = useState(true);

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const { data: models, isLoading: modelsLoading } = useQuery({
    queryKey: ['models', llmProvider],
    queryFn: () => getModels(llmProvider),
    enabled: !settingsLoading,
  });

  const { data: whisperModels } = useQuery({
    queryKey: ['whisperModels'],
    queryFn: getWhisperModels,
  });

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['status'],
    queryFn: getSystemStatus,
  });

  const { data: processingEpisodes } = useQuery({
    queryKey: ['processing-episodes'],
    queryFn: getProcessingEpisodes,
    refetchInterval: 5000,
  });

  const { data: retention } = useQuery({
    queryKey: ['retention'],
    queryFn: getRetention,
  });

  const { data: processingTimeouts } = useQuery({
    queryKey: ['processing-timeouts'],
    queryFn: getProcessingTimeouts,
  });

  const { data: audioSettings } = useQuery({
    queryKey: ['audio-settings'],
    queryFn: getAudioSettings,
  });

  // System Status section uses defaultOpen on its CollapsibleSection
  // (see SystemStatusSection.tsx) so it starts expanded on first visit.
  // After that the user's collapsed/expanded preference is persisted via
  // CollapsibleSection's storage key and respected on subsequent loads --
  // the previous setItem('true') write here forced it open on every load,
  // overriding the user's choice.

  // Auto-expand and scroll to section when navigated via hash link
  useEffect(() => {
    if (location.hash === '#podcast-index') {
      localStorage.setItem('settings-section-podcast-index', 'true');
      setTimeout(() => {
        document.getElementById('podcast-index')?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
    }
  }, [location.hash]);

  // Sync form fields with server data via during-render compare. This is the
  // React 19 alternative to a useEffect+setState that fires whenever the
  // upstream query data identity changes. useSyncFromQuery encapsulates the
  // snapshot+conditional-setState pattern; staying render-phase preserves
  // the May 4 fix that moved these blocks off useEffect.
  useSyncFromQuery(retention, (r) => {
    setRetentionDays(r.retentionDays || 30);
    setRetentionEnabled(r.enabled);
  });

  useSyncFromQuery(processingTimeouts, (t) => {
    setSoftTimeoutMinutes(Math.round(t.softTimeoutSeconds / 60));
    setHardTimeoutMinutes(Math.round(t.hardTimeoutSeconds / 60));
  });

  useSyncFromQuery(audioSettings, (a) => {
    setKeepOriginalAudio(a.keepOriginalAudio);
  });

  const audioSettingsMutation = useMutation({
    mutationFn: (keep: boolean) => updateAudioSettings(keep),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['audio-settings'] }),
  });

  const cancelMutation = useMutation({
    mutationFn: (params: { slug: string; episodeId: string }) =>
      cancelProcessing(params.slug, params.episodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['processing-episodes'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });

  const retentionMutation = useMutation({
    mutationFn: (days: number) => updateRetention(days),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['retention'] });
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const processingTimeoutsMutation = useMutation({
    mutationFn: ({ soft, hard }: { soft: number; hard: number }) =>
      updateProcessingTimeouts(soft, hard),
    onMutate: () => setTimeoutsError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['processing-timeouts'] });
    },
    onError: (err: Error) => setTimeoutsError(err.message || 'Failed to save'),
  });

  const [settingsSnapshot, setSettingsSnapshot] = useState(settings);
  if (settings !== settingsSnapshot) {
    setSettingsSnapshot(settings);
    if (settings) {
      setSystemPrompt(settings.systemPrompt?.value || '');
      setVerificationPrompt(settings.verificationPrompt?.value || '');
      setReviewer({
        enabled: settings.enableAdReview?.value ?? false,
        model: settings.reviewModel?.value || 'same_as_pass',
        maxShift: settings.reviewMaxBoundaryShift?.value ?? 60,
        reviewPrompt: settings.reviewPrompt?.value || '',
        resurrectPrompt: settings.resurrectPrompt?.value || '',
      });
      setSelectedModel(settings.claudeModel?.value || '');
      setVerificationModel(settings.verificationModel?.value || '');
      setWhisperModel(settings.whisperModel?.value || 'small');
      setAutoProcessEnabled(settings.autoProcessEnabled?.value ?? true);
      setMaxFeedEpisodes(settings.maxFeedEpisodes?.value ?? 300);
      setOnlyExposeProcessedDefault(settings.onlyExposeProcessedDefault?.value ?? false);
      setAudioBitrate(settings.audioBitrate?.value || '128k');
      setVttTranscriptsEnabled(settings.vttTranscriptsEnabled?.value ?? true);
      setChaptersEnabled(settings.chaptersEnabled?.value ?? true);
      setChaptersModel(settings.chaptersModel?.value || '');
      setMinCutConfidence(settings.minCutConfidence?.value ?? 0.80);
      setLlmProvider((settings.llmProvider?.value || LLM_PROVIDERS.ANTHROPIC) as LlmProvider);
      setOpenaiBaseUrl(settings.openaiBaseUrl?.value || 'http://localhost:8000/v1');
      setWhisperBackend((settings.whisperBackend?.value || 'local') as WhisperBackend);
      setWhisperApiConfig({
        baseUrl: settings.whisperApiBaseUrl?.value || '',
        model: settings.whisperApiModel?.value || 'whisper-1',
      });
      setWhisperLanguage(settings.whisperLanguage?.value || 'en');
      setWhisperComputeType(settings.whisperComputeType?.value || 'auto');
    }
  }

  // Build a payload of fields whose current state differs from the loaded
  // API value. Backend PUT handlers use `if 'fieldName' in data:` guards,
  // so omitted fields stay untouched in the DB; that's what lets a Save
  // change one field without wiping the rest, and also closes the
  // hydration-race window where Save could fire before loaded values were
  // copied into local state. Caller must verify settings is non-null.
  // String fields use || (treat empty string as "fall back to default");
  // boolean and numeric fields use ?? (false and 0 are meaningful values).
  // audioBitrate's default isn't in settings.defaults so it stays inline.
  const computeChangedFields = (): UpdateSettingsPayload => {
    if (!settings) return {};
    const d = settings.defaults;
    const payload: UpdateSettingsPayload = {};

    if (systemPrompt !== (settings.systemPrompt?.value || d.systemPrompt)) payload.systemPrompt = systemPrompt;
    if (verificationPrompt !== (settings.verificationPrompt?.value || d.verificationPrompt)) payload.verificationPrompt = verificationPrompt;
    if (reviewer.reviewPrompt !== (settings.reviewPrompt?.value || d.reviewPrompt)) payload.reviewPrompt = reviewer.reviewPrompt;
    if (reviewer.resurrectPrompt !== (settings.resurrectPrompt?.value || d.resurrectPrompt)) payload.resurrectPrompt = reviewer.resurrectPrompt;
    if (reviewer.enabled !== (settings.enableAdReview?.value ?? d.enableAdReview)) payload.enableAdReview = reviewer.enabled;
    if (reviewer.model !== (settings.reviewModel?.value || d.reviewModel)) payload.reviewModel = reviewer.model;
    if (reviewer.maxShift !== (settings.reviewMaxBoundaryShift?.value ?? d.reviewMaxBoundaryShift)) payload.reviewMaxBoundaryShift = reviewer.maxShift;
    if (selectedModel !== (settings.claudeModel?.value || d.claudeModel)) payload.claudeModel = selectedModel;
    if (verificationModel !== (settings.verificationModel?.value || d.verificationModel)) payload.verificationModel = verificationModel;
    if (whisperModel !== (settings.whisperModel?.value || d.whisperModel)) payload.whisperModel = whisperModel;
    if (chaptersModel !== (settings.chaptersModel?.value || d.chaptersModel)) payload.chaptersModel = chaptersModel;
    if (llmProvider !== (settings.llmProvider?.value || d.llmProvider)) payload.llmProvider = llmProvider;
    if (openaiBaseUrl !== (settings.openaiBaseUrl?.value || d.openaiBaseUrl)) payload.openaiBaseUrl = openaiBaseUrl;
    if (whisperBackend !== (settings.whisperBackend?.value || d.whisperBackend)) payload.whisperBackend = whisperBackend;
    if (whisperApiConfig.baseUrl !== (settings.whisperApiBaseUrl?.value || d.whisperApiBaseUrl)) payload.whisperApiBaseUrl = whisperApiConfig.baseUrl;
    if (whisperApiConfig.model !== (settings.whisperApiModel?.value || d.whisperApiModel)) payload.whisperApiModel = whisperApiConfig.model;
    if (whisperLanguage !== (settings.whisperLanguage?.value || d.whisperLanguage)) payload.whisperLanguage = whisperLanguage;
    if (whisperComputeType !== (settings.whisperComputeType?.value || d.whisperComputeType)) payload.whisperComputeType = whisperComputeType;
    if (audioBitrate !== (settings.audioBitrate?.value || '128k')) payload.audioBitrate = audioBitrate;

    if (autoProcessEnabled !== (settings.autoProcessEnabled?.value ?? d.autoProcessEnabled)) payload.autoProcessEnabled = autoProcessEnabled;
    if (onlyExposeProcessedDefault !== (settings.onlyExposeProcessedDefault?.value ?? d.onlyExposeProcessedDefault)) payload.onlyExposeProcessedDefault = onlyExposeProcessedDefault;
    if (vttTranscriptsEnabled !== (settings.vttTranscriptsEnabled?.value ?? d.vttTranscriptsEnabled)) payload.vttTranscriptsEnabled = vttTranscriptsEnabled;
    if (chaptersEnabled !== (settings.chaptersEnabled?.value ?? d.chaptersEnabled)) payload.chaptersEnabled = chaptersEnabled;
    if (maxFeedEpisodes !== (settings.maxFeedEpisodes?.value ?? d.maxFeedEpisodes)) payload.maxFeedEpisodes = maxFeedEpisodes;
    if (minCutConfidence !== (settings.minCutConfidence?.value ?? d.minCutConfidence)) payload.minCutConfidence = minCutConfidence;

    return payload;
  };

  const hasChanges = useMemo(() => {
    if (!settings) return false;
    if (Object.keys(computeChangedFields()).length > 0) return true;
    return podcastIndexApiKey !== '' && podcastIndexApiSecret !== '';
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemPrompt, verificationPrompt, reviewer, selectedModel, verificationModel, whisperModel, autoProcessEnabled, maxFeedEpisodes, onlyExposeProcessedDefault, audioBitrate, vttTranscriptsEnabled, chaptersEnabled, chaptersModel, minCutConfidence, llmProvider, openaiBaseUrl, whisperBackend, whisperApiConfig.baseUrl, whisperApiConfig.model, whisperLanguage, whisperComputeType, podcastIndexApiKey, podcastIndexApiSecret, settings]);

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!settings) throw new Error('Settings not loaded yet');
      const payload = computeChangedFields();
      if (podcastIndexApiKey) payload.podcastIndexApiKey = podcastIndexApiKey;
      if (podcastIndexApiSecret) payload.podcastIndexApiSecret = podcastIndexApiSecret;
      return updateSettings(payload);
    },
    onSuccess: () => {
      setPodcastIndexApiKey('');
      setPodcastIndexApiSecret('');
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
  });

  // Per-stage tunables save immediately rather than waiting for the global Save
  // button: each field is independent and users tweak them iteratively.
  const tunableMutation = useMutation({
    mutationFn: (payload: UpdateSettingsPayload) => updateSettings(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const refreshModelsMutation = useMutation({
    mutationFn: refreshModels,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
  });

  const resetMutation = useMutation({
    mutationFn: resetSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
  });

  const resetPromptsMutation = useMutation({
    mutationFn: resetPrompts,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const cleanupMutation = useMutation({
    mutationFn: runCleanup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });

  if (settingsLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4 pb-20">
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold text-foreground mb-2">Settings</h1>
          <p className="text-muted-foreground">
            Configure ad detection prompts and system settings
          </p>
        </div>
        <a
          href="/api/v1/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap shrink-0"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          API Docs
        </a>
      </div>

      <SystemStatusSection
        status={status}
        statusLoading={statusLoading}
      />

      <ProcessingQueueSection
        processingEpisodes={processingEpisodes}
        onCancel={(params) => cancelMutation.mutate(params)}
        cancelIsPending={cancelMutation.isPending}
      />

      <SettingsGroupHeader title="Appearance" />

      <AppearanceSection />

      <SettingsGroupHeader title="Podcast Discovery" />

      <div id="podcast-index">
        <PodcastIndexSection
          podcastIndexApiKeyConfigured={settings?.podcastIndexApiKeyConfigured}
          podcastIndexApiKey={podcastIndexApiKey}
          podcastIndexApiSecret={podcastIndexApiSecret}
          onApiKeyChange={setPodcastIndexApiKey}
          onApiSecretChange={setPodcastIndexApiSecret}
        />
      </div>

      <SettingsGroupHeader title="AI & Processing" />

      {providersError && (
        <p className="text-sm text-destructive mb-2">Could not load provider status: {providersError}</p>
      )}

      <GlobalDefaultsSection
        autoProcessEnabled={autoProcessEnabled}
        onAutoProcessEnabledChange={setAutoProcessEnabled}
        maxFeedEpisodes={maxFeedEpisodes}
        onMaxFeedEpisodesChange={setMaxFeedEpisodes}
        onlyExposeProcessedDefault={onlyExposeProcessedDefault}
        onOnlyExposeProcessedDefaultChange={setOnlyExposeProcessedDefault}
      />

      <LLMProviderSection
        llmProvider={llmProvider}
        openaiBaseUrl={openaiBaseUrl}
        onProviderChange={(p) => {
          setLlmProvider(p);
          setSelectedModel('');
          setVerificationModel('');
          setChaptersModel('');
        }}
        onBaseUrlChange={setOpenaiBaseUrl}
        providersState={providersState}
        onProviderKeySave={handleProviderKeySave}
        onProviderKeyClear={handleProviderKeyClear}
        onProviderKeyTest={handleProviderKeyTest}
        ollamaNumCtx={settings?.stageTunables?.ollamaNumCtx}
        onOllamaNumCtxUpdate={(payload) => tunableMutation.mutate(payload)}
      />

      <AIModelsSection
        models={models}
        modelsLoading={modelsLoading}
        selectedModel={selectedModel}
        verificationModel={verificationModel}
        chaptersModel={chaptersModel}
        onSelectedModelChange={setSelectedModel}
        onVerificationModelChange={setVerificationModel}
        onChaptersModelChange={setChaptersModel}
        onRefresh={() => refreshModelsMutation.mutate()}
        refreshIsPending={refreshModelsMutation.isPending}
      />

      {settings?.stageTunables && settings?.stageTunableDefaults && (
        <StageTunablesSection
          tunables={settings.stageTunables}
          defaults={settings.stageTunableDefaults}
          llmProvider={llmProvider}
          onUpdate={(payload) => tunableMutation.mutate(payload)}
        />
      )}

      <TranscriptionSection
        whisperModel={whisperModel}
        whisperModels={whisperModels}
        onWhisperModelChange={setWhisperModel}
        whisperBackend={whisperBackend}
        onWhisperBackendChange={setWhisperBackend}
        apiConfig={whisperApiConfig}
        onApiConfigChange={(field, value) =>
          setWhisperApiConfig(prev => ({ ...prev, [field]: value }))
        }
        providersState={providersState}
        onProviderKeySave={handleProviderKeySave}
        onProviderKeyClear={handleProviderKeyClear}
        onProviderKeyTest={handleProviderKeyTest}
        whisperLanguage={whisperLanguage}
        onWhisperLanguageChange={setWhisperLanguage}
        whisperComputeType={whisperComputeType}
        onWhisperComputeTypeChange={setWhisperComputeType}
        softTimeoutMinutes={softTimeoutMinutes}
        hardTimeoutMinutes={hardTimeoutMinutes}
        softMinMinutes={processingTimeouts ? Math.max(1, Math.ceil(processingTimeouts.limits.softMin / 60)) : 5}
        hardMaxMinutes={processingTimeouts ? Math.floor(processingTimeouts.limits.hardMax / 60) : 1440}
        onSoftTimeoutChange={setSoftTimeoutMinutes}
        onHardTimeoutChange={setHardTimeoutMinutes}
        onTimeoutsSave={() => processingTimeoutsMutation.mutate({
          soft: softTimeoutMinutes * 60,
          hard: hardTimeoutMinutes * 60,
        })}
        timeoutsSaveIsPending={processingTimeoutsMutation.isPending}
        timeoutsSaveIsSuccess={processingTimeoutsMutation.isSuccess}
        timeoutsError={timeoutsError}
      />

      <AdDetectionSection
        minCutConfidence={minCutConfidence}
        onMinCutConfidenceChange={setMinCutConfidence}
      />

      <PromptsSection
        systemPrompt={systemPrompt}
        verificationPrompt={verificationPrompt}
        onSystemPromptChange={setSystemPrompt}
        onVerificationPromptChange={setVerificationPrompt}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
      />

      <AdReviewerSection />

      <CommunityPatternsSection />

      <SettingsGroupHeader title="Experiments" />

      <ExperimentsSection
        reviewer={reviewer}
        onChange={setReviewer}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
        modelOptions={models?.map((m) => ({ id: m.id, label: formatModelLabel(m) })) ?? []}
      />

      <SettingsGroupHeader title="Output" />

      <AudioSection
        audioBitrate={audioBitrate}
        onAudioBitrateChange={setAudioBitrate}
      />

      <Podcasting20Section
        vttTranscriptsEnabled={vttTranscriptsEnabled}
        chaptersEnabled={chaptersEnabled}
        onVttTranscriptsEnabledChange={setVttTranscriptsEnabled}
        onChaptersEnabledChange={setChaptersEnabled}
      />

      <SettingsGroupHeader title="Data & Security" />

      <StorageRetentionSection
        keepOriginalAudio={keepOriginalAudio}
        onKeepOriginalAudioChange={(enabled) => {
          setKeepOriginalAudio(enabled);
          audioSettingsMutation.mutate(enabled);
        }}
        keepOriginalSaveIsPending={audioSettingsMutation.isPending}
        retentionEnabled={retentionEnabled}
        retentionDays={retentionDays}
        onRetentionEnabledChange={setRetentionEnabled}
        onRetentionDaysChange={setRetentionDays}
        onSave={() => retentionMutation.mutate(retentionEnabled ? retentionDays : 0)}
        saveIsPending={retentionMutation.isPending}
        saveIsSuccess={retentionMutation.isSuccess}
      />

      <DataManagementSection
        onResetEpisodes={() => cleanupMutation.mutate()}
        resetIsPending={cleanupMutation.isPending}
        resetData={cleanupMutation.data}
      />

      <WebhooksSection />

      <SecuritySection
        isPasswordSet={isPasswordSet}
        logout={logout}
        refreshStatus={refreshStatus}
        cryptoReady={providersState?.cryptoReady ?? false}
        plaintextSecretsCount={status?.security?.plaintextSecretsCount ?? 0}
      />

      {/* Error display */}
      {(updateMutation.error || resetMutation.error || resetPromptsMutation.error) && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
          {((updateMutation.error || resetMutation.error || resetPromptsMutation.error) as Error).message}
        </div>
      )}

      {/* Sticky save bar */}
      {hasChanges && (
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background/80 backdrop-blur-md">
          <div className="max-w-3xl mx-auto flex items-center justify-between gap-4 px-4 py-3">
            <button
              onClick={() => resetMutation.mutate()}
              disabled={resetMutation.isPending}
              className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm"
            >
              {resetMutation.isPending ? 'Resetting...' : 'Reset All'}
            </button>
            <button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className="px-6 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm font-medium"
            >
              {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default Settings;

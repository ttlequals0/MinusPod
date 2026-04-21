import { useState, useEffect, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing, refreshModels, getRetention, updateRetention, getProcessingTimeouts, updateProcessingTimeouts, getAudioSettings, updateAudioSettings } from '../api/settings';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';
import type { LlmProvider, WhisperBackend, WhisperApiConfig } from '../api/types';
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
import TranscriptionSection from './settings/TranscriptionSection';
import AudioSection from './settings/AudioSection';
import AdDetectionSection from './settings/AdDetectionSection';
import Podcasting20Section from './settings/Podcasting20Section';
import PromptsSection from './settings/PromptsSection';

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
  const [selectedModel, setSelectedModel] = useState('');
  const [verificationModel, setVerificationModel] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(true);
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

  // Ensure System Status section is always expanded on page load
  useEffect(() => {
    localStorage.setItem('settings-section-system-status', 'true');
  }, []);

  // Auto-expand and scroll to section when navigated via hash link
  useEffect(() => {
    if (location.hash === '#podcast-index') {
      localStorage.setItem('settings-section-podcast-index', 'true');
      setTimeout(() => {
        document.getElementById('podcast-index')?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
    }
  }, [location.hash]);

  useEffect(() => {
    if (retention) {
      setRetentionDays(retention.retentionDays || 30);
      setRetentionEnabled(retention.enabled);
    }
  }, [retention]);

  useEffect(() => {
    if (processingTimeouts) {
      setSoftTimeoutMinutes(Math.round(processingTimeouts.softTimeoutSeconds / 60));
      setHardTimeoutMinutes(Math.round(processingTimeouts.hardTimeoutSeconds / 60));
    }
  }, [processingTimeouts]);

  useEffect(() => {
    if (audioSettings) {
      setKeepOriginalAudio(audioSettings.keepOriginalAudio);
    }
  }, [audioSettings]);

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

  useEffect(() => {
    if (settings) {
      setSystemPrompt(settings.systemPrompt?.value || '');
      setVerificationPrompt(settings.verificationPrompt?.value || '');
      setSelectedModel(settings.claudeModel?.value || '');
      setVerificationModel(settings.verificationModel?.value || '');
      setWhisperModel(settings.whisperModel?.value || 'small');
      setAutoProcessEnabled(settings.autoProcessEnabled?.value ?? true);
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
  }, [settings]);

  const hasChanges = useMemo(() => {
    if (!settings) return false;
    return (
      systemPrompt !== (settings.systemPrompt?.value || '') ||
      verificationPrompt !== (settings.verificationPrompt?.value || '') ||
      selectedModel !== (settings.claudeModel?.value || '') ||
      verificationModel !== (settings.verificationModel?.value || '') ||
      whisperModel !== (settings.whisperModel?.value || 'small') ||
      autoProcessEnabled !== (settings.autoProcessEnabled?.value ?? true) ||
      audioBitrate !== (settings.audioBitrate?.value || '128k') ||
      vttTranscriptsEnabled !== (settings.vttTranscriptsEnabled?.value ?? true) ||
      chaptersEnabled !== (settings.chaptersEnabled?.value ?? true) ||
      chaptersModel !== (settings.chaptersModel?.value || '') ||
      minCutConfidence !== (settings.minCutConfidence?.value ?? 0.80) ||
      llmProvider !== (settings.llmProvider?.value || LLM_PROVIDERS.ANTHROPIC) ||
      openaiBaseUrl !== (settings.openaiBaseUrl?.value || 'http://localhost:8000/v1') ||
      whisperBackend !== (settings.whisperBackend?.value || 'local') ||
      whisperApiConfig.baseUrl !== (settings.whisperApiBaseUrl?.value || '') ||
      whisperApiConfig.model !== (settings.whisperApiModel?.value || 'whisper-1') ||
      whisperLanguage !== (settings.whisperLanguage?.value || 'en') ||
      whisperComputeType !== (settings.whisperComputeType?.value || 'auto') ||
      (podcastIndexApiKey !== '' && podcastIndexApiSecret !== '')
    );
  }, [systemPrompt, verificationPrompt, selectedModel, verificationModel, whisperModel, autoProcessEnabled, audioBitrate, vttTranscriptsEnabled, chaptersEnabled, chaptersModel, minCutConfidence, llmProvider, openaiBaseUrl, whisperBackend, whisperApiConfig.baseUrl, whisperApiConfig.model, whisperLanguage, whisperComputeType, podcastIndexApiKey, podcastIndexApiSecret, settings]);

  const updateMutation = useMutation({
    mutationFn: () =>
      updateSettings({
        systemPrompt,
        verificationPrompt,
        claudeModel: selectedModel,
        verificationModel,
        whisperModel,
        autoProcessEnabled,
        audioBitrate,
        vttTranscriptsEnabled,
        chaptersEnabled,
        chaptersModel,
        minCutConfidence,
        llmProvider,
        openaiBaseUrl,
        whisperBackend,
        whisperApiBaseUrl: whisperApiConfig.baseUrl,
        whisperApiModel: whisperApiConfig.model,
        whisperLanguage,
        whisperComputeType,
        ...(podcastIndexApiKey ? { podcastIndexApiKey } : {}),
        ...(podcastIndexApiSecret ? { podcastIndexApiSecret } : {}),
      }),
    onSuccess: () => {
      setPodcastIndexApiKey('');
      setPodcastIndexApiSecret('');
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
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
          className="text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap flex-shrink-0"
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
        autoProcessEnabled={autoProcessEnabled}
        onMinCutConfidenceChange={setMinCutConfidence}
        onAutoProcessEnabledChange={setAutoProcessEnabled}
      />

      <PromptsSection
        systemPrompt={systemPrompt}
        verificationPrompt={verificationPrompt}
        onSystemPromptChange={setSystemPrompt}
        onVerificationPromptChange={setVerificationPrompt}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
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

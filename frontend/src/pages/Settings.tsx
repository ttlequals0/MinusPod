import { useState, useEffect, useMemo } from 'react';
import { useSyncFromQuery } from '../hooks/useSyncFromQuery';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing, refreshModels, getRetention, updateRetention, getProcessingTimeouts, updateProcessingTimeouts, getAudioSettings, updateAudioSettings } from '../api/settings';
import { getReviewerSettings, updateReviewerSettings } from '../api/community';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';
import type { LlmProvider, WhisperBackend, WhisperApiConfig, UpdateSettingsPayload } from '../api/types';

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
import AudioCueDetectionSection from './settings/AudioCueDetectionSection';
import PositionalPriorSection from './settings/PositionalPriorSection';
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
  // Form state holds no hardcoded defaults: every field is hydrated from the
  // loaded settings (or the backend-provided `settings.defaults.*`) before the
  // form renders (the page returns a loader while `settingsLoading`, and the
  // hydration block below runs in the render phase). These initializers are
  // neutral placeholders that are never displayed.
  const [reviewer, setReviewer] = useState({
    enabled: false,
    model: '',
    maxShift: 0,
    reviewPrompt: '',
    resurrectPrompt: '',
    parallelAds: 0,
    // From the separate /settings/reviewer endpoint (reviewerSettings query),
    // merged into this section so the page Save persists everything together.
    updatePatterns: true,
    minTrimThreshold: 20,
  });
  const [audioCue, setAudioCue] = useState({
    enabled: false,
    freqMinHz: 1500,
    freqMaxHz: 8000,
    prominenceDb: 9,
    minConfidence: 0.8,
  });
  const [positionalPriorEnabled, setPositionalPriorEnabled] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');
  const [verificationModel, setVerificationModel] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(false);
  const [maxFeedEpisodes, setMaxFeedEpisodes] = useState(0);
  const [onlyExposeProcessedDefault, setOnlyExposeProcessedDefault] = useState(false);
  const [audioBitrate, setAudioBitrate] = useState('');
  const [skipFlacCompression, setSkipFlacCompression] = useState(false);
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(false);
  const [chaptersEnabled, setChaptersEnabled] = useState(false);
  const [chaptersModel, setChaptersModel] = useState('');
  const [minCutConfidence, setMinCutConfidence] = useState(0);
  // Neutral placeholder (cast); replaced by hydration before the form renders.
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('' as LlmProvider);
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('');
  const [whisperBackend, setWhisperBackend] = useState<WhisperBackend>('' as WhisperBackend);
  const [whisperApiConfig, setWhisperApiConfig] = useState<WhisperApiConfig>({
    baseUrl: '', model: '',
  });
  const [whisperLanguage, setWhisperLanguage] = useState('');
  const [whisperComputeType, setWhisperComputeType] = useState('');
  const [providersState, setProvidersState] = useState<ProvidersResponse | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const reloadProviders = () =>
    listProviders()
      .then((r) => { setProvidersState(r); setProvidersError(null); })
      .catch((e) => setProvidersError(e instanceof Error ? e.message : 'Failed to load providers'));

  useEffect(() => { reloadProviders(); }, []);

  const handleProviderKeySave = async (provider: ProviderName, apiKey: string) => {
    // Co-persist base URL with the key (#234). Skip if empty so a pre-hydration save doesn't clear it (#235).
    const body: { apiKey: string; baseUrl?: string } = { apiKey };
    if (provider === 'openai' && openaiBaseUrl) body.baseUrl = openaiBaseUrl;
    else if (provider === 'whisper' && whisperApiConfig.baseUrl) body.baseUrl = whisperApiConfig.baseUrl;
    await updateProvider(provider, body);
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
  const [originalRetentionDays, setOriginalRetentionDays] = useState(30);
  const [keepOriginalAudio, setKeepOriginalAudio] = useState(true);
  const [softTimeoutMinutes, setSoftTimeoutMinutes] = useState(60);
  const [hardTimeoutMinutes, setHardTimeoutMinutes] = useState(120);
  const [timeoutsError, setTimeoutsError] = useState<string | null>(null);
  const [retentionEnabled, setRetentionEnabled] = useState(true);

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  // The Ad Reviewer pattern-update toggle and trim threshold live on a
  // separate endpoint but are surfaced in the same section; seeded into
  // `reviewer` so the global Save writes them alongside the rest.
  const { data: reviewerSettings } = useQuery({
    queryKey: ['reviewerSettings'],
    queryFn: getReviewerSettings,
  });

  const { data: models, isLoading: modelsLoading } = useQuery({
    queryKey: ['models', llmProvider],
    queryFn: () => getModels(llmProvider),
    // Gate on llmProvider too: it is an empty placeholder until hydration runs.
    enabled: !settingsLoading && !!llmProvider,
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
    setOriginalRetentionDays(r.originalRetentionDays ?? r.retentionDays ?? 30);
    setRetentionEnabled(r.enabled);
  });

  useSyncFromQuery(processingTimeouts, (t) => {
    setSoftTimeoutMinutes(Math.round(t.softTimeoutSeconds / 60));
    setHardTimeoutMinutes(Math.round(t.hardTimeoutSeconds / 60));
  });

  useSyncFromQuery(reviewerSettings, (rs) => {
    setReviewer((prev) => ({
      ...prev,
      updatePatterns: rs.updatePatternsFromReviewerAdjustments,
      minTrimThreshold: rs.minTrimThreshold,
    }));
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
    mutationFn: ({ days, originalDays }: { days: number; originalDays: number }) =>
      updateRetention(days, originalDays),
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

  // Skip re-seeding form fields from a settings refetch while the user has
  // unsaved edits, or an immediate-save refetch (tunables/retention invalidate
  // ['settings']) would clobber them (fe-settings-history-1).
  // Hydrate the form from loaded settings. The snapshot starts undefined (not
  // `settings`) so the first render after any (re)mount with cached query data
  // re-hydrates -- otherwise `settings === settingsSnapshot` on remount and the
  // form would show the neutral placeholders instead of the saved values (#323).
  // The `!formDirty` guard still prevents a background refetch from clobbering
  // unsaved edits. Defaults come from the backend `settings.defaults` block, not
  // hardcoded literals; the hydration and computeChangedFields fallbacks MUST
  // match (see fe-settings-history-1 / #234) or `hasChanges` never settles.
  const [formDirty, setFormDirty] = useState(false);
  const [settingsSnapshot, setSettingsSnapshot] = useState<typeof settings>(undefined);
  if (settings && settings !== settingsSnapshot) {
    setSettingsSnapshot(settings);
    if (!formDirty) {
      const d = settings.defaults;
      setSystemPrompt(settings.systemPrompt?.value || '');
      setVerificationPrompt(settings.verificationPrompt?.value || '');
      // Spread prev so the pattern-update fields seeded from the separate
      // reviewerSettings query (see useSyncFromQuery below) are preserved.
      setReviewer((prev) => ({
        ...prev,
        enabled: settings.enableAdReview?.value ?? d.enableAdReview,
        model: settings.reviewModel?.value || d.reviewModel,
        maxShift: settings.reviewMaxBoundaryShift?.value ?? d.reviewMaxBoundaryShift,
        reviewPrompt: settings.reviewPrompt?.value || '',
        resurrectPrompt: settings.resurrectPrompt?.value || '',
        parallelAds: settings.adReviewerParallelAds?.value ?? d.adReviewerParallelAds,
      }));
      setSelectedModel(settings.claudeModel?.value || '');
      setVerificationModel(settings.verificationModel?.value || '');
      setWhisperModel(settings.whisperModel?.value || d.whisperModel);
      setAutoProcessEnabled(settings.autoProcessEnabled?.value ?? d.autoProcessEnabled);
      setMaxFeedEpisodes(settings.maxFeedEpisodes?.value ?? d.maxFeedEpisodes);
      setOnlyExposeProcessedDefault(settings.onlyExposeProcessedDefault?.value ?? d.onlyExposeProcessedDefault);
      setAudioBitrate(settings.audioBitrate?.value || d.audioBitrate);
      setSkipFlacCompression(settings.skipFlacCompression?.value ?? d.skipFlacCompression);
      setAudioCue({
        enabled: settings.audioCueDetectionEnabled?.value ?? d.audioCueDetectionEnabled,
        freqMinHz: settings.audioCueFreqMinHz?.value ?? d.audioCueFreqMinHz,
        freqMaxHz: settings.audioCueFreqMaxHz?.value ?? d.audioCueFreqMaxHz,
        prominenceDb: settings.audioCueProminenceDb?.value ?? d.audioCueProminenceDb,
        minConfidence: settings.audioCueMinConfidence?.value ?? d.audioCueMinConfidence,
      });
      setPositionalPriorEnabled(
        settings.positionalPriorEnabled?.value ?? d.positionalPriorEnabled);
      setVttTranscriptsEnabled(settings.vttTranscriptsEnabled?.value ?? d.vttTranscriptsEnabled);
      setChaptersEnabled(settings.chaptersEnabled?.value ?? d.chaptersEnabled);
      setChaptersModel(settings.chaptersModel?.value || '');
      setMinCutConfidence(settings.minCutConfidence?.value ?? d.minCutConfidence);
      setLlmProvider((settings.llmProvider?.value || d.llmProvider) as LlmProvider);
      setOpenaiBaseUrl(settings.openaiBaseUrl?.value || d.openaiBaseUrl);
      setWhisperBackend((settings.whisperBackend?.value || d.whisperBackend) as WhisperBackend);
      setWhisperApiConfig({
        baseUrl: settings.whisperApiBaseUrl?.value || '',
        model: settings.whisperApiModel?.value || d.whisperApiModel,
      });
      setWhisperLanguage(settings.whisperLanguage?.value || d.whisperLanguage);
      setWhisperComputeType(settings.whisperComputeType?.value || d.whisperComputeType);
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
  // Defaults come from settings.defaults; these MUST match the hydration block.
  const computeChangedFields = (): UpdateSettingsPayload => {
    if (!settings) return {};
    const d = settings.defaults;
    const payload: UpdateSettingsPayload = {};

    // Compare against the SAME fallback the hydration block used (see
    // setSystemPrompt / setSelectedModel etc above). If the two diverge, a
    // server-stored value differs from the defaults-derived value, hasChanges
    // flips permanently true, and Save Changes never goes away (#234 follow-up).
    if (systemPrompt !== (settings.systemPrompt?.value || '')) payload.systemPrompt = systemPrompt;
    if (verificationPrompt !== (settings.verificationPrompt?.value || '')) payload.verificationPrompt = verificationPrompt;
    if (reviewer.reviewPrompt !== (settings.reviewPrompt?.value || '')) payload.reviewPrompt = reviewer.reviewPrompt;
    if (reviewer.resurrectPrompt !== (settings.resurrectPrompt?.value || '')) payload.resurrectPrompt = reviewer.resurrectPrompt;
    if (reviewer.enabled !== (settings.enableAdReview?.value ?? d.enableAdReview)) payload.enableAdReview = reviewer.enabled;
    if (reviewer.model !== (settings.reviewModel?.value || d.reviewModel)) payload.reviewModel = reviewer.model;
    if (reviewer.maxShift !== (settings.reviewMaxBoundaryShift?.value ?? d.reviewMaxBoundaryShift)) payload.reviewMaxBoundaryShift = reviewer.maxShift;
    if (reviewer.parallelAds !== (settings.adReviewerParallelAds?.value ?? d.adReviewerParallelAds)) payload.adReviewerParallelAds = reviewer.parallelAds;
    if (audioCue.enabled !== (settings.audioCueDetectionEnabled?.value ?? d.audioCueDetectionEnabled)) payload.audioCueDetectionEnabled = audioCue.enabled;
    if (audioCue.freqMinHz !== (settings.audioCueFreqMinHz?.value ?? d.audioCueFreqMinHz)) payload.audioCueFreqMinHz = audioCue.freqMinHz;
    if (audioCue.freqMaxHz !== (settings.audioCueFreqMaxHz?.value ?? d.audioCueFreqMaxHz)) payload.audioCueFreqMaxHz = audioCue.freqMaxHz;
    if (audioCue.prominenceDb !== (settings.audioCueProminenceDb?.value ?? d.audioCueProminenceDb)) payload.audioCueProminenceDb = audioCue.prominenceDb;
    if (audioCue.minConfidence !== (settings.audioCueMinConfidence?.value ?? d.audioCueMinConfidence)) payload.audioCueMinConfidence = audioCue.minConfidence;
    if (positionalPriorEnabled !== (settings.positionalPriorEnabled?.value ?? d.positionalPriorEnabled)) payload.positionalPriorEnabled = positionalPriorEnabled;
    if (selectedModel !== (settings.claudeModel?.value || '')) payload.claudeModel = selectedModel;
    if (verificationModel !== (settings.verificationModel?.value || '')) payload.verificationModel = verificationModel;
    if (whisperModel !== (settings.whisperModel?.value || d.whisperModel)) payload.whisperModel = whisperModel;
    if (chaptersModel !== (settings.chaptersModel?.value || '')) payload.chaptersModel = chaptersModel;
    if (llmProvider !== (settings.llmProvider?.value || d.llmProvider)) payload.llmProvider = llmProvider;
    if (openaiBaseUrl !== (settings.openaiBaseUrl?.value || d.openaiBaseUrl)) payload.openaiBaseUrl = openaiBaseUrl;
    if (whisperBackend !== (settings.whisperBackend?.value || d.whisperBackend)) payload.whisperBackend = whisperBackend;
    if (whisperApiConfig.baseUrl !== (settings.whisperApiBaseUrl?.value || '')) payload.whisperApiBaseUrl = whisperApiConfig.baseUrl;
    if (whisperApiConfig.model !== (settings.whisperApiModel?.value || d.whisperApiModel)) payload.whisperApiModel = whisperApiConfig.model;
    if (whisperLanguage !== (settings.whisperLanguage?.value || d.whisperLanguage)) payload.whisperLanguage = whisperLanguage;
    if (whisperComputeType !== (settings.whisperComputeType?.value || d.whisperComputeType)) payload.whisperComputeType = whisperComputeType;
    if (audioBitrate !== (settings.audioBitrate?.value || d.audioBitrate)) payload.audioBitrate = audioBitrate;
    if (skipFlacCompression !== (settings.skipFlacCompression?.value ?? d.skipFlacCompression)) payload.skipFlacCompression = skipFlacCompression;

    if (autoProcessEnabled !== (settings.autoProcessEnabled?.value ?? d.autoProcessEnabled)) payload.autoProcessEnabled = autoProcessEnabled;
    if (onlyExposeProcessedDefault !== (settings.onlyExposeProcessedDefault?.value ?? d.onlyExposeProcessedDefault)) payload.onlyExposeProcessedDefault = onlyExposeProcessedDefault;
    if (vttTranscriptsEnabled !== (settings.vttTranscriptsEnabled?.value ?? d.vttTranscriptsEnabled)) payload.vttTranscriptsEnabled = vttTranscriptsEnabled;
    if (chaptersEnabled !== (settings.chaptersEnabled?.value ?? d.chaptersEnabled)) payload.chaptersEnabled = chaptersEnabled;
    if (maxFeedEpisodes !== (settings.maxFeedEpisodes?.value ?? d.maxFeedEpisodes)) payload.maxFeedEpisodes = maxFeedEpisodes;
    if (minCutConfidence !== (settings.minCutConfidence?.value ?? d.minCutConfidence)) payload.minCutConfidence = minCutConfidence;

    return payload;
  };

  // The two pattern-update fields save via /settings/reviewer, not the main
  // ad-detection PUT, so they are diffed separately from computeChangedFields.
  const reviewerPatternsChanged = () => {
    if (!reviewerSettings) return false;
    return reviewer.updatePatterns !== reviewerSettings.updatePatternsFromReviewerAdjustments
      || reviewer.minTrimThreshold !== reviewerSettings.minTrimThreshold;
  };

  const hasChanges = useMemo(() => {
    if (!settings) return false;
    if (Object.keys(computeChangedFields()).length > 0) return true;
    if (reviewerPatternsChanged()) return true;
    return podcastIndexApiKey !== '' && podcastIndexApiSecret !== '';
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemPrompt, verificationPrompt, reviewer, audioCue, positionalPriorEnabled, selectedModel, verificationModel, whisperModel, autoProcessEnabled, maxFeedEpisodes, onlyExposeProcessedDefault, audioBitrate, skipFlacCompression, vttTranscriptsEnabled, chaptersEnabled, chaptersModel, minCutConfidence, llmProvider, openaiBaseUrl, whisperBackend, whisperApiConfig.baseUrl, whisperApiConfig.model, whisperLanguage, whisperComputeType, podcastIndexApiKey, podcastIndexApiSecret, settings, reviewerSettings]);

  // Mirror hasChanges into render-readable state so the hydration guard above
  // (which runs before hasChanges is defined) skips re-seeding while dirty.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setFormDirty(hasChanges); }, [hasChanges]);

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!settings) throw new Error('Settings not loaded yet');
      const payload = computeChangedFields();
      if (podcastIndexApiKey) payload.podcastIndexApiKey = podcastIndexApiKey;
      if (podcastIndexApiSecret) payload.podcastIndexApiSecret = podcastIndexApiSecret;
      const tasks: Promise<unknown>[] = [];
      // Skip the main PUT when nothing in its payload changed (e.g. only the
      // reviewer-pattern fields are dirty) to avoid a no-op request.
      if (Object.keys(payload).length > 0) tasks.push(updateSettings(payload));
      if (reviewerPatternsChanged()) {
        tasks.push(updateReviewerSettings({
          updatePatternsFromReviewerAdjustments: reviewer.updatePatterns,
          minTrimThreshold: reviewer.minTrimThreshold,
        }));
      }
      await Promise.all(tasks);
    },
    onSuccess: () => {
      setPodcastIndexApiKey('');
      setPodcastIndexApiSecret('');
    },
    // onSettled (not onSuccess) so a partial failure across the two writes
    // still re-hydrates the form from server truth instead of leaving stale
    // local state next to a write that did land.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
      queryClient.invalidateQueries({ queryKey: ['reviewerSettings'] });
    },
  });

  // Single-field tunable saves (e.g. Ollama context window) commit immediately.
  const tunableMutation = useMutation({
    mutationFn: (payload: UpdateSettingsPayload) => updateSettings(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  // The LLM Tunables section batches all its field edits behind one explicit
  // Save button; its own mutation keeps the Saving/Saved state scoped to that
  // section rather than flashing on unrelated single-field saves.
  const stageTunablesMutation = useMutation({
    mutationFn: (payload: UpdateSettingsPayload) => updateSettings(payload),
    // onSettled (not onSuccess): the PUT applies fields in phases and commits
    // each as it goes, so a 400 on a later field can still leave an earlier one
    // written. Re-hydrate on both outcomes so the section reflects what landed.
    onSettled: () => {
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
          onSave={(payload) => stageTunablesMutation.mutate(payload)}
          saveIsPending={stageTunablesMutation.isPending}
          saveIsSuccess={stageTunablesMutation.isSuccess}
          saveError={stageTunablesMutation.error ? (stageTunablesMutation.error as Error).message : null}
          parallelWindows={settings.adDetectionParallelWindows?.value ?? settings.defaults?.adDetectionParallelWindows ?? 4}
          parallelWindowsDefault={settings.defaults?.adDetectionParallelWindows ?? 4}
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
        skipFlacCompression={skipFlacCompression}
        onSkipFlacCompressionChange={setSkipFlacCompression}
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

      <CommunityPatternsSection />

      <SettingsGroupHeader title="Experiments" />

      <ExperimentsSection
        reviewer={reviewer}
        onChange={setReviewer}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
        modelOptions={models?.map((m) => ({ id: m.id, label: formatModelLabel(m) })) ?? []}
      />

      <AudioCueDetectionSection audioCue={audioCue} onChange={setAudioCue} />

      <PositionalPriorSection
        enabled={positionalPriorEnabled}
        onChange={setPositionalPriorEnabled}
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
        originalRetentionDays={originalRetentionDays}
        onOriginalRetentionDaysChange={setOriginalRetentionDays}
        onSave={() => retentionMutation.mutate({
          days: retentionEnabled ? retentionDays : 0,
          originalDays: Math.min(originalRetentionDays, retentionDays),
        })}
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

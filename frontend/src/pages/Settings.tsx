import { useState, useEffect, useRef } from 'react';
import { useSyncFromQuery } from '../hooks/useSyncFromQuery';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing, refreshModels, getRetention, updateRetention, getProcessingTimeouts, updateProcessingTimeouts, getAudioSettings, updateAudioSettings } from '../api/settings';
import { getReviewerSettings, updateReviewerSettings } from '../api/community';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';
import type { LlmProvider, WhisperBackend, WhisperApiConfig, UpdateSettingsPayload, Settings as SettingsShape } from '../api/types';

import SystemStatusSection from './settings/SystemStatusSection';
import StorageRetentionSection from './settings/StorageRetentionSection';
import DataManagementSection from './settings/DataManagementSection';
import NotificationsSection from './settings/NotificationsSection';
import AuthenticatedFeedsSection from './settings/AuthenticatedFeedsSection';
import SecuritySection from './settings/SecuritySection';
import ProcessingQueueSection from './settings/ProcessingQueueSection';
import ConfirmResetButton from './settings/ConfirmResetButton';
import AppearanceSection from './settings/AppearanceSection';
import PodcastIndexSection from './settings/PodcastIndexSection';
import LLMProviderSection from './settings/LLMProviderSection';
import {
  listProviders,
  updateProvider,
  clearProvider,
  testProvider,
  testWhisperConnection,
  testLlmConnection,
  testPodcastIndex,
  type ProviderName,
  type ProvidersResponse,
} from '../api/providers';
import AIModelsSection from './settings/AIModelsSection';
import StageTunablesSection from './settings/StageTunablesSection';
import TranscriptionSection from './settings/TranscriptionSection';
import AudioSection from './settings/AudioSection';
import CoverArtSection from './settings/CoverArtSection';
import { refreshAllArtwork } from '../api/feeds';
import AdDetectionSection from './settings/AdDetectionSection';
import GlobalDefaultsSection from './settings/GlobalDefaultsSection';
import Podcasting20Section from './settings/Podcasting20Section';
import PromptsSection from './settings/PromptsSection';
import ExperimentsSection from './settings/ExperimentsSection';
import AudioCueDetectionSection from './settings/AudioCueDetectionSection';
import PositionalPriorSection from './settings/PositionalPriorSection';
import CommunityPatternsSection from './settings/CommunityPatternsSection';
import DatabaseBackupSection from './settings/DatabaseBackupSection';
import OfflineQueueSection from './settings/OfflineQueueSection';
import { Search, X } from 'lucide-react';
import { SettingsSearchContext, useSettingsSearch } from '../context/SettingsSearchContext';
import { formatModelLabel } from './settings/settingsUtils';
import { btnPrimary } from '../components/buttonStyles';

function SettingsGroupHeader({ title }: { title: string }) {
  // During an active settings search the group labels are noise (sections are
  // filtered individually), so hide them and let the matching cards stand alone.
  if (useSettingsSearch() !== null) return null;
  return (
    <div className="pt-4 pb-1">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        {title}
      </h3>
    </div>
  );
}

type SettingScalar = string | number | boolean;

// One registry row per Save-bar field. Hydration, the changed-field diff,
// and dirty detection all resolve the server-side value through
// fieldBaseline(), so the fallback chains can no longer drift apart --
// the drift class behind #234, #513 and fe-settings-history-1 (hydration
// and diff MUST use identical fallbacks) is structurally closed.
interface FieldSpec {
  // Key into settings, settings.defaults, and the PUT payload (all match).
  key: keyof UpdateSettingsPayload;
  // 'str': empty string falls back to the default ( || ); 'val': only
  // null/undefined falls back ( ?? ) so false and 0 are meaningful values.
  kind: 'str' | 'val';
  // Current form value for this field.
  value: SettingScalar;
  // Look up settings.defaults[key] ahead of the literal.
  useDefault?: boolean;
  // Trailing fallback when the default is absent; str-kind rows without a
  // default fall back to ''.
  literal?: SettingScalar;
  // Hydration target: a flat state setter ((v: never) so any concrete
  // Dispatch<SetStateAction<...>> is assignable)...
  set?: (v: never) => void;
  // ...or a property patch collected into one of the nested state objects.
  obj?: 'reviewer' | 'audioCue' | 'whisperApi';
  prop?: string;
}

function fieldBaseline(settings: SettingsShape, f: FieldSpec): SettingScalar | undefined {
  const sv = (settings as unknown as Record<string, { value?: SettingScalar } | undefined>)[f.key]?.value;
  const dv = f.useDefault
    ? (settings.defaults as unknown as Record<string, SettingScalar | undefined>)[f.key]
    : undefined;
  const dflt = dv ?? f.literal ?? (f.kind === 'str' ? '' : undefined);
  return f.kind === 'str' ? (sv || dflt) : (sv ?? dflt);
}

function Settings() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const { isPasswordSet, logout, refreshStatus } = useAuth();

  const [systemPrompt, setSystemPrompt] = useState('');
  const [verificationPrompt, setVerificationPrompt] = useState('');
  const [systemPromptOverride, setSystemPromptOverride] = useState('');
  const [verificationPromptOverride, setVerificationPromptOverride] = useState('');
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
    reviewPromptOverride: '',
    resurrectPromptOverride: '',
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
    templateScore: 0.75,
    formantAttenDb: 0,
    createFromPairs: false,
    snapConfidence: 0.8,
    snapLeadSeconds: 10,
    snapLagSeconds: 4,
    captureMinSeconds: 0.2,
    captureMaxSeconds: 10,
    captureMaxIntroSeconds: 60,
    captureMaxOutroSeconds: 60,
    pairConfidence: 0.85,
    pairMinBreakSeconds: 30,
    pairMaxBreakSeconds: 480,
    pairMaxBreakFraction: 0.5,
    silenceSnapNoiseDb: -50,
    silenceSnapMinDurationSeconds: 0.3,
    silenceSnapMaxDistanceSeconds: 2,
  });
  const [positionalPriorEnabled, setPositionalPriorEnabled] = useState(false);
  const [settingsQuery, setSettingsQuery] = useState('');
  // null = no active search; otherwise the set of matching section keys.
  // Computed in the event handler (the lint forbids ref reads in render and
  // setState in effects); hidden sections keep their textContent, so each
  // keystroke can rescan every section.
  const [settingsMatchKeys, setSettingsMatchKeys] = useState<Set<string> | null>(null);
  const searchRegionRef = useRef<HTMLDivElement>(null);
  const runSettingsSearch = (q: string) => {
    setSettingsQuery(q);
    const norm = q.trim().toLowerCase();
    if (!norm) {
      setSettingsMatchKeys(null);
      return;
    }
    // Scope the scan to the searchable region so the two sections above the
    // search box (System Status, Processing Queue) don't count toward matches.
    const matches = new Set<string>();
    searchRegionRef.current?.querySelectorAll<HTMLElement>('[data-search-key]').forEach((el) => {
      if ((el.textContent ?? '').toLowerCase().includes(norm)) {
        const key = el.getAttribute('data-search-key');
        if (key) matches.add(key);
      }
    });
    setSettingsMatchKeys(matches);
  };
  // Paint the matched query text yellow within the searchable region as the user
  // types -- CSS Custom Highlight API, so no DOM mutation and React stays in
  // charge of the tree. Runs after the filter commit so ranges point at the
  // freshly expanded sections; no-op where the API is unavailable (filtering
  // still works). offsetParent skips text in display:none (non-matching) cards.
  useEffect(() => {
    if (typeof CSS === 'undefined' || !('highlights' in CSS)) return;
    const norm = settingsQuery.trim().toLowerCase();
    const region = searchRegionRef.current;
    if (!norm || !region) {
      CSS.highlights.delete('settings-search');
      return;
    }
    const ranges: Range[] = [];
    const walker = document.createTreeWalker(region, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.nodeValue && n.parentElement?.offsetParent
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT,
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const hay = n.nodeValue!.toLowerCase();
      for (let i = hay.indexOf(norm); i !== -1; i = hay.indexOf(norm, i + norm.length)) {
        const r = document.createRange();
        r.setStart(n, i);
        r.setEnd(n, i + norm.length);
        ranges.push(r);
      }
    }
    CSS.highlights.set('settings-search', new Highlight(...ranges));
    return () => { CSS.highlights.delete('settings-search'); };
  }, [settingsQuery, settingsMatchKeys]);
  const [selectedModel, setSelectedModel] = useState('');
  const [verificationModel, setVerificationModel] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(false);
  const [maxFeedEpisodes, setMaxFeedEpisodes] = useState(0);
  const [podpingEnabled, setPodpingEnabled] = useState(false);
  const [rssRefreshIntervalMinutes, setRssRefreshIntervalMinutes] = useState(15);
  const [onlyExposeProcessedDefault, setOnlyExposeProcessedDefault] = useState(false);
  const [artworkWatermarkEnabled, setArtworkWatermarkEnabled] = useState(false);
  const [audioBitrate, setAudioBitrate] = useState('');
  const [audioNormalizeEnabled, setAudioNormalizeEnabled] = useState(false);
  const [audioNormalizeIntensity, setAudioNormalizeIntensity] = useState('normal');
  const [skipFlacCompression, setSkipFlacCompression] = useState(false);
  const [maxArtworkBytes, setMaxArtworkBytes] = useState(26214400);
  const [maxRssBytes, setMaxRssBytes] = useState(209715200);
  const [maxAudioDownloadMb, setMaxAudioDownloadMb] = useState(500);
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(false);
  const [chaptersEnabled, setChaptersEnabled] = useState(false);
  const [chaptersModel, setChaptersModel] = useState('');
  const [minCutConfidence, setMinCutConfidence] = useState(0);
  const [minContentBetweenAdsSeconds, setMinContentBetweenAdsSeconds] = useState(12);
  const [verificationMissHoldMinConfidence, setVerificationMissHoldMinConfidence] = useState(0.6);
  const [verificationMissAutocutMinConfidence, setVerificationMissAutocutMinConfidence] = useState(0);
  const [learningMinConfidence, setLearningMinConfidence] = useState(0.85);
  const [learningMinConfidenceLong, setLearningMinConfidenceLong] = useState(0.92);
  const [differentialMeasuredCorrMax, setDifferentialMeasuredCorrMax] = useState(0.6);
  const [differentialHoldMinSeconds, setDifferentialHoldMinSeconds] = useState(10);
  // Neutral placeholder (cast); replaced by hydration before the form renders.
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('' as LlmProvider);
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('');
  const [pricingSourceMode, setPricingSourceMode] = useState('auto');
  const [whisperBackend, setWhisperBackend] = useState<WhisperBackend>('' as WhisperBackend);
  const [whisperApiConfig, setWhisperApiConfig] = useState<WhisperApiConfig>({
    baseUrl: '', model: '',
  });
  const [whisperLanguage, setWhisperLanguage] = useState('');
  const [whisperComputeType, setWhisperComputeType] = useState('');
  const [transcribeMaxChunkSeconds, setTranscribeMaxChunkSeconds] = useState(600);
  const [transcribeConcurrentChunks, setTranscribeConcurrentChunks] = useState(4);
  const [transcribeChunkOverlapSeconds, setTranscribeChunkOverlapSeconds] = useState(30);
  const [providersState, setProvidersState] = useState<ProvidersResponse | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const reloadProviders = () =>
    listProviders()
      .then((r) => { setProvidersState(r); setProvidersError(null); })
      .catch((e) => setProvidersError(getErrorMessage(e, 'Failed to load providers')));

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
  const [podcastSearchProvider, setPodcastSearchProvider] = useState('');
  const [podcastIndexApiKey, setPodcastIndexApiKey] = useState('');
  const [podcastIndexApiSecret, setPodcastIndexApiSecret] = useState('');
  const [retentionDays, setRetentionDays] = useState(30);
  const [originalRetentionDays, setOriginalRetentionDays] = useState(30);
  const [keepOriginalAudio, setKeepOriginalAudio] = useState(true);
  const [softTimeoutMinutes, setSoftTimeoutMinutes] = useState(60);
  const [hardTimeoutMinutes, setHardTimeoutMinutes] = useState(120);
  const [timeoutsError, setTimeoutsError] = useState<string | null>(null);
  const [retentionEnabled, setRetentionEnabled] = useState(true);

  const {
    data: settings,
    isLoading: settingsLoading,
    dataUpdatedAt: settingsUpdatedAt,
  } = useQuery({
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

  // The field registry. Each row appears exactly once; everything else
  // (hydration, diff payload, dirty detection) is derived from it. Rebuilt
  // per render so `value` always reflects current state (cheap).
  const FIELDS: FieldSpec[] = [
    // Prompts (no defaults: a cleared box round-trips as '').
    { key: 'systemPrompt', kind: 'str', value: systemPrompt, set: setSystemPrompt },
    { key: 'verificationPrompt', kind: 'str', value: verificationPrompt, set: setVerificationPrompt },
    { key: 'systemPromptOverride', kind: 'str', value: systemPromptOverride, set: setSystemPromptOverride },
    { key: 'verificationPromptOverride', kind: 'str', value: verificationPromptOverride, set: setVerificationPromptOverride },
    // Ad reviewer (nested `reviewer` state; updatePatterns/minTrimThreshold
    // save via /settings/reviewer and are diffed by reviewerPatternsChanged).
    { key: 'reviewPrompt', kind: 'str', value: reviewer.reviewPrompt, obj: 'reviewer', prop: 'reviewPrompt' },
    { key: 'resurrectPrompt', kind: 'str', value: reviewer.resurrectPrompt, obj: 'reviewer', prop: 'resurrectPrompt' },
    { key: 'reviewPromptOverride', kind: 'str', value: reviewer.reviewPromptOverride, obj: 'reviewer', prop: 'reviewPromptOverride' },
    { key: 'resurrectPromptOverride', kind: 'str', value: reviewer.resurrectPromptOverride, obj: 'reviewer', prop: 'resurrectPromptOverride' },
    { key: 'enableAdReview', kind: 'val', useDefault: true, value: reviewer.enabled, obj: 'reviewer', prop: 'enabled' },
    { key: 'reviewModel', kind: 'str', useDefault: true, value: reviewer.model, obj: 'reviewer', prop: 'model' },
    { key: 'reviewMaxBoundaryShift', kind: 'val', useDefault: true, value: reviewer.maxShift, obj: 'reviewer', prop: 'maxShift' },
    { key: 'adReviewerParallelAds', kind: 'val', useDefault: true, value: reviewer.parallelAds, obj: 'reviewer', prop: 'parallelAds' },
    // Models
    { key: 'claudeModel', kind: 'str', value: selectedModel, set: setSelectedModel },
    { key: 'verificationModel', kind: 'str', value: verificationModel, set: setVerificationModel },
    { key: 'chaptersModel', kind: 'str', value: chaptersModel, set: setChaptersModel },
    { key: 'whisperModel', kind: 'str', useDefault: true, value: whisperModel, set: setWhisperModel },
    // Providers
    { key: 'llmProvider', kind: 'str', useDefault: true, value: llmProvider, set: (v) => setLlmProvider(v as LlmProvider) },
    { key: 'podcastSearchProvider', kind: 'str', value: podcastSearchProvider, set: setPodcastSearchProvider },
    { key: 'openaiBaseUrl', kind: 'str', useDefault: true, value: openaiBaseUrl, set: setOpenaiBaseUrl },
    { key: 'pricingSourceMode', kind: 'str', useDefault: true, value: pricingSourceMode, set: setPricingSourceMode },
    // Transcription
    { key: 'whisperBackend', kind: 'str', useDefault: true, value: whisperBackend, set: (v) => setWhisperBackend(v as WhisperBackend) },
    { key: 'whisperApiBaseUrl', kind: 'str', value: whisperApiConfig.baseUrl, obj: 'whisperApi', prop: 'baseUrl' },
    { key: 'whisperApiModel', kind: 'str', useDefault: true, value: whisperApiConfig.model, obj: 'whisperApi', prop: 'model' },
    { key: 'whisperLanguage', kind: 'str', useDefault: true, value: whisperLanguage, set: setWhisperLanguage },
    { key: 'whisperComputeType', kind: 'str', useDefault: true, value: whisperComputeType, set: setWhisperComputeType },
    { key: 'transcribeMaxChunkSeconds', kind: 'val', useDefault: true, literal: 600, value: transcribeMaxChunkSeconds, set: setTranscribeMaxChunkSeconds },
    { key: 'transcribeConcurrentChunks', kind: 'val', useDefault: true, literal: 4, value: transcribeConcurrentChunks, set: setTranscribeConcurrentChunks },
    { key: 'transcribeChunkOverlapSeconds', kind: 'val', useDefault: true, literal: 30, value: transcribeChunkOverlapSeconds, set: setTranscribeChunkOverlapSeconds },
    // Audio output
    { key: 'audioBitrate', kind: 'str', useDefault: true, value: audioBitrate, set: setAudioBitrate },
    { key: 'audioNormalizeEnabled', kind: 'val', useDefault: true, value: audioNormalizeEnabled, set: setAudioNormalizeEnabled },
    { key: 'audioNormalizeIntensity', kind: 'str', useDefault: true, value: audioNormalizeIntensity, set: setAudioNormalizeIntensity },
    { key: 'skipFlacCompression', kind: 'val', useDefault: true, value: skipFlacCompression, set: setSkipFlacCompression },
    { key: 'maxArtworkBytes', kind: 'val', useDefault: true, value: maxArtworkBytes, set: setMaxArtworkBytes },
    { key: 'maxRssBytes', kind: 'val', useDefault: true, value: maxRssBytes, set: setMaxRssBytes },
    { key: 'maxAudioDownloadMb', kind: 'val', useDefault: true, value: maxAudioDownloadMb, set: setMaxAudioDownloadMb },
    // Global behavior / output toggles
    { key: 'autoProcessEnabled', kind: 'val', useDefault: true, value: autoProcessEnabled, set: setAutoProcessEnabled },
    { key: 'onlyExposeProcessedDefault', kind: 'val', useDefault: true, value: onlyExposeProcessedDefault, set: setOnlyExposeProcessedDefault },
    { key: 'artworkWatermarkEnabled', kind: 'val', useDefault: true, value: artworkWatermarkEnabled, set: setArtworkWatermarkEnabled },
    { key: 'vttTranscriptsEnabled', kind: 'val', useDefault: true, value: vttTranscriptsEnabled, set: setVttTranscriptsEnabled },
    { key: 'chaptersEnabled', kind: 'val', useDefault: true, value: chaptersEnabled, set: setChaptersEnabled },
    { key: 'maxFeedEpisodes', kind: 'val', useDefault: true, value: maxFeedEpisodes, set: setMaxFeedEpisodes },
    { key: 'podpingEnabled', kind: 'val', useDefault: true, value: podpingEnabled, set: setPodpingEnabled },
    { key: 'rssRefreshIntervalMinutes', kind: 'val', useDefault: true, literal: 15, value: rssRefreshIntervalMinutes, set: setRssRefreshIntervalMinutes },
    // Ad detection
    { key: 'minCutConfidence', kind: 'val', useDefault: true, value: minCutConfidence, set: setMinCutConfidence },
    { key: 'minContentBetweenAdsSeconds', kind: 'val', useDefault: true, literal: 12, value: minContentBetweenAdsSeconds, set: setMinContentBetweenAdsSeconds },
    { key: 'positionalPriorEnabled', kind: 'val', useDefault: true, value: positionalPriorEnabled, set: setPositionalPriorEnabled },
    { key: 'verificationMissHoldMinConfidence', kind: 'val', useDefault: true, literal: 0.6, value: verificationMissHoldMinConfidence, set: setVerificationMissHoldMinConfidence },
    { key: 'verificationMissAutocutMinConfidence', kind: 'val', useDefault: true, literal: 0, value: verificationMissAutocutMinConfidence, set: setVerificationMissAutocutMinConfidence },
    { key: 'learningMinConfidence', kind: 'val', useDefault: true, literal: 0.85, value: learningMinConfidence, set: setLearningMinConfidence },
    { key: 'learningMinConfidenceLong', kind: 'val', useDefault: true, literal: 0.92, value: learningMinConfidenceLong, set: setLearningMinConfidenceLong },
    { key: 'differentialMeasuredCorrMax', kind: 'val', useDefault: true, literal: 0.6, value: differentialMeasuredCorrMax, set: setDifferentialMeasuredCorrMax },
    { key: 'differentialHoldMinSeconds', kind: 'val', useDefault: true, literal: 10, value: differentialHoldMinSeconds, set: setDifferentialHoldMinSeconds },
    // Audio cue detection (nested `audioCue` state)
    { key: 'audioCueDetectionEnabled', kind: 'val', useDefault: true, value: audioCue.enabled, obj: 'audioCue', prop: 'enabled' },
    { key: 'audioCueFreqMinHz', kind: 'val', useDefault: true, value: audioCue.freqMinHz, obj: 'audioCue', prop: 'freqMinHz' },
    { key: 'audioCueFreqMaxHz', kind: 'val', useDefault: true, value: audioCue.freqMaxHz, obj: 'audioCue', prop: 'freqMaxHz' },
    { key: 'audioCueProminenceDb', kind: 'val', useDefault: true, value: audioCue.prominenceDb, obj: 'audioCue', prop: 'prominenceDb' },
    { key: 'audioCueMinConfidence', kind: 'val', useDefault: true, value: audioCue.minConfidence, obj: 'audioCue', prop: 'minConfidence' },
    { key: 'audioCueTemplateScore', kind: 'val', useDefault: true, literal: 0.75, value: audioCue.templateScore, obj: 'audioCue', prop: 'templateScore' },
    { key: 'audioCueFormantAttenDb', kind: 'val', useDefault: true, literal: 0, value: audioCue.formantAttenDb, obj: 'audioCue', prop: 'formantAttenDb' },
    { key: 'audioCueCreateFromPairs', kind: 'val', useDefault: true, literal: false, value: audioCue.createFromPairs, obj: 'audioCue', prop: 'createFromPairs' },
    { key: 'audioCueSnapConfidence', kind: 'val', useDefault: true, literal: 0.8, value: audioCue.snapConfidence, obj: 'audioCue', prop: 'snapConfidence' },
    { key: 'audioCueSnapLeadSeconds', kind: 'val', useDefault: true, literal: 10, value: audioCue.snapLeadSeconds, obj: 'audioCue', prop: 'snapLeadSeconds' },
    { key: 'audioCueSnapLagSeconds', kind: 'val', useDefault: true, literal: 4, value: audioCue.snapLagSeconds, obj: 'audioCue', prop: 'snapLagSeconds' },
    { key: 'audioCueCaptureMinSeconds', kind: 'val', useDefault: true, literal: 0.2, value: audioCue.captureMinSeconds, obj: 'audioCue', prop: 'captureMinSeconds' },
    { key: 'audioCueCaptureMaxSeconds', kind: 'val', useDefault: true, literal: 10, value: audioCue.captureMaxSeconds, obj: 'audioCue', prop: 'captureMaxSeconds' },
    { key: 'audioCueCaptureMaxIntroSeconds', kind: 'val', useDefault: true, literal: 60, value: audioCue.captureMaxIntroSeconds, obj: 'audioCue', prop: 'captureMaxIntroSeconds' },
    { key: 'audioCueCaptureMaxOutroSeconds', kind: 'val', useDefault: true, literal: 60, value: audioCue.captureMaxOutroSeconds, obj: 'audioCue', prop: 'captureMaxOutroSeconds' },
    { key: 'audioCuePairConfidence', kind: 'val', useDefault: true, literal: 0.85, value: audioCue.pairConfidence, obj: 'audioCue', prop: 'pairConfidence' },
    { key: 'audioCuePairMinBreakSeconds', kind: 'val', useDefault: true, literal: 30, value: audioCue.pairMinBreakSeconds, obj: 'audioCue', prop: 'pairMinBreakSeconds' },
    { key: 'audioCuePairMaxBreakSeconds', kind: 'val', useDefault: true, literal: 480, value: audioCue.pairMaxBreakSeconds, obj: 'audioCue', prop: 'pairMaxBreakSeconds' },
    { key: 'audioCuePairMaxBreakFraction', kind: 'val', useDefault: true, literal: 0.5, value: audioCue.pairMaxBreakFraction, obj: 'audioCue', prop: 'pairMaxBreakFraction' },
    { key: 'silenceSnapNoiseDb', kind: 'val', useDefault: true, literal: -50, value: audioCue.silenceSnapNoiseDb, obj: 'audioCue', prop: 'silenceSnapNoiseDb' },
    { key: 'silenceSnapMinDurationSeconds', kind: 'val', useDefault: true, literal: 0.3, value: audioCue.silenceSnapMinDurationSeconds, obj: 'audioCue', prop: 'silenceSnapMinDurationSeconds' },
    { key: 'silenceSnapMaxDistanceSeconds', kind: 'val', useDefault: true, literal: 2, value: audioCue.silenceSnapMaxDistanceSeconds, obj: 'audioCue', prop: 'silenceSnapMaxDistanceSeconds' },
  ];

  // Skip re-seeding form fields from a settings refetch while the user has
  // unsaved edits, or an immediate-save refetch (tunables/retention invalidate
  // ['settings']) would clobber them (fe-settings-history-1).
  // Hydrate the form from loaded settings. The snapshot starts undefined (not
  // `settings`) so the first render after any (re)mount with cached query data
  // re-hydrates -- otherwise `settings === settingsSnapshot` on remount and the
  // form would show the neutral placeholders instead of the saved values (#323).
  // The `!formDirty` guard still prevents a background refetch from clobbering
  // unsaved edits. Defaults come from the backend `settings.defaults` block via
  // fieldBaseline(); hydration and computeChangedFields share it, so their
  // fallbacks cannot diverge (see fe-settings-history-1 / #234).
  const [formDirty, setFormDirty] = useState(false);
  const [settingsSnapshot, setSettingsSnapshot] = useState<typeof settings>(undefined);
  // After a save or reset lands, the refetch it triggers must re-seed the
  // form even though formDirty is still true from the pre-save edits.
  // Without this, clearing a prompt box and saving leaves the box empty
  // while the backend serves the restored default, so hasChanges (state ''
  // vs default text) never settles and Save Changes never goes away; a
  // prompts reset likewise needed a browser refresh to show the defaults
  // again (#513). State (not a ref) so the render-phase read is legal.
  //
  // The flag is consumed on the next completed fetch (dataUpdatedAt), not on
  // object identity: react-query's structural sharing returns the SAME object
  // when a refetch is deep-equal (e.g. resetting prompts already at their
  // defaults), and keying on identity alone would strand the flag as true,
  // where a later unrelated refetch would clobber genuinely-unsaved edits.
  const [rehydratePending, setRehydratePending] = useState(false);
  const [seenSettingsUpdatedAt, setSeenSettingsUpdatedAt] = useState(0);
  const settingsJustFetched = settingsUpdatedAt !== seenSettingsUpdatedAt;
  if (settings && (settings !== settingsSnapshot || settingsJustFetched)) {
    if (settings !== settingsSnapshot) setSettingsSnapshot(settings);
    if (settingsJustFetched) setSeenSettingsUpdatedAt(settingsUpdatedAt);
    if (!formDirty || (rehydratePending && settingsJustFetched)) {
      if (rehydratePending && settingsJustFetched) setRehydratePending(false);
      // Seed every registered field from its baseline. Flat fields set
      // directly (render-phase setState, same pattern as before); nested
      // fields are collected into per-object patches and applied once.
      const patches: Record<'reviewer' | 'audioCue' | 'whisperApi', Record<string, SettingScalar | undefined>> = {
        reviewer: {}, audioCue: {}, whisperApi: {},
      };
      for (const f of FIELDS) {
        const v = fieldBaseline(settings, f);
        if (f.set) f.set(v as never);
        else if (f.obj && f.prop) patches[f.obj][f.prop] = v;
      }
      // Spread prev so reviewer fields seeded from the separate
      // reviewerSettings query (see useSyncFromQuery above) are preserved.
      setReviewer((prev) => ({ ...prev, ...(patches.reviewer as Partial<typeof prev>) }));
      setAudioCue((prev) => ({ ...prev, ...(patches.audioCue as Partial<typeof prev>) }));
      setWhisperApiConfig((prev) => ({ ...prev, ...(patches.whisperApi as Partial<typeof prev>) }));
    }
  }

  // Build a payload of fields whose current state differs from the loaded
  // API value. Backend PUT handlers use `if 'fieldName' in data:` guards,
  // so omitted fields stay untouched in the DB; that's what lets a Save
  // change one field without wiping the rest, and also closes the
  // hydration-race window where Save could fire before loaded values were
  // copied into local state.
  const computeChangedFields = (): UpdateSettingsPayload => {
    if (!settings) return {};
    const payload: UpdateSettingsPayload = {};
    for (const f of FIELDS) {
      // Compare against the SAME baseline hydration seeded from. If the two
      // ever diverged, hasChanges would flip permanently true and Save
      // Changes would never go away (#234 follow-up); deriving both from
      // fieldBaseline makes that impossible.
      if (f.value !== fieldBaseline(settings, f)) {
        (payload as Record<string, SettingScalar>)[f.key] = f.value;
      }
    }
    return payload;
  };

  // The two pattern-update fields save via /settings/reviewer, not the main
  // ad-detection PUT, so they are diffed separately from computeChangedFields.
  const reviewerPatternsChanged = () => {
    if (!reviewerSettings) return false;
    return reviewer.updatePatterns !== reviewerSettings.updatePatternsFromReviewerAdjustments
      || reviewer.minTrimThreshold !== reviewerSettings.minTrimThreshold;
  };

  // Recomputed every render: ~65 scalar compares over the registry, cheap
  // enough to skip memoization. This removes the old useMemo whose
  // hand-maintained dependency list was a fourth per-field registration.
  const hasChanges = !!settings && (
    Object.keys(computeChangedFields()).length > 0
    || reviewerPatternsChanged()
    || (podcastIndexApiKey !== '' && podcastIndexApiSecret !== '')
  );

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
      return payload;
    },
    onSuccess: (payload) => {
      setPodcastIndexApiKey('');
      setPodcastIndexApiSecret('');
      // Force a re-seed only when the save actually needs one: a cleared
      // ('') string field comes back from the server as the restored default
      // text, so without the re-seed the box stays empty and hasChanges
      // never settles (#513). Any other save round-trips its own values, and
      // skipping the forced re-seed preserves edits typed while the PUT was
      // in flight. onSuccess (not onSettled) so a failed save cannot revert
      // what the user just typed while the error banner is showing.
      if (Object.values(payload).some((v) => v === '')) {
        setRehydratePending(true);
      }
    },
    // onSettled (not onSuccess) so a partial failure across the two writes
    // still refetches server truth instead of leaving stale query data next
    // to a write that did land.
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

  const refreshArtworkMutation = useMutation({
    mutationFn: refreshAllArtwork,
  });

  const resetMutation = useMutation({
    mutationFn: resetSettings,
    onSuccess: () => {
      setRehydratePending(true);
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
  });

  const resetPromptsMutation = useMutation({
    mutationFn: resetPrompts,
    onSuccess: () => {
      setRehydratePending(true);
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
        <div className="flex items-center gap-4 shrink-0">
          <a
            href="https://github.com/ttlequals0/MinusPod/blob/main/docs/README.md"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.247m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.247" />
            </svg>
            Docs
          </a>
          <a
            href="/api/v1/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            API Docs
          </a>
        </div>
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

      {/* Settings search: filters the configurable sections below by matching a
          section's title or any of its setting labels (client-side, no backend). */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <input
          type="text"
          value={settingsQuery}
          onChange={(e) => runSettingsSearch(e.target.value)}
          placeholder="Search settings..."
          aria-label="Search settings"
          className="w-full rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground pl-9 pr-9 py-2 focus:outline-hidden focus:ring-2 focus:ring-ring"
        />
        {settingsQuery && (
          <button
            type="button"
            onClick={() => runSettingsSearch('')}
            aria-label="Clear settings search"
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-muted-foreground hover:text-foreground touch-manipulation"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      <SettingsSearchContext.Provider value={settingsMatchKeys}>
      <div ref={searchRegionRef} className="space-y-4">

      {settingsMatchKeys !== null && settingsMatchKeys.size === 0 && (
        <p className="text-sm text-muted-foreground px-1">
          No settings match "{settingsQuery.trim()}".
        </p>
      )}

      <SettingsGroupHeader title="Appearance" />

      <AppearanceSection />

      <SettingsGroupHeader title="Podcast Discovery" />

      <div id="podcast-index">
        <PodcastIndexSection
          searchProvider={podcastSearchProvider}
          onSearchProviderChange={setPodcastSearchProvider}
          podcastIndexApiKeyConfigured={settings?.podcastIndexApiKeyConfigured}
          podcastIndexApiKey={podcastIndexApiKey}
          podcastIndexApiSecret={podcastIndexApiSecret}
          onApiKeyChange={setPodcastIndexApiKey}
          onApiSecretChange={setPodcastIndexApiSecret}
          onConnectionTest={testPodcastIndex}
        />
      </div>

      <SettingsGroupHeader title="AI & Processing" />

      {providersError && (
        <p className="text-sm text-destructive mb-2">Could not load provider status: {providersError}</p>
      )}

      <GlobalDefaultsSection
        autoProcessEnabled={autoProcessEnabled}
        onAutoProcessEnabledChange={setAutoProcessEnabled}
        rssRefreshIntervalMinutes={rssRefreshIntervalMinutes}
        onRssRefreshIntervalMinutesChange={setRssRefreshIntervalMinutes}
        maxFeedEpisodes={maxFeedEpisodes}
        onMaxFeedEpisodesChange={setMaxFeedEpisodes}
        onlyExposeProcessedDefault={onlyExposeProcessedDefault}
        onOnlyExposeProcessedDefaultChange={setOnlyExposeProcessedDefault}
      />

      <LLMProviderSection
        llmProvider={llmProvider}
        openaiBaseUrl={openaiBaseUrl}
        pricingSourceMode={pricingSourceMode}
        onProviderChange={(p) => {
          setLlmProvider(p);
          setSelectedModel('');
          setVerificationModel('');
          setChaptersModel('');
        }}
        onBaseUrlChange={setOpenaiBaseUrl}
        onPricingSourceModeChange={setPricingSourceMode}
        providersState={providersState}
        onProviderKeySave={handleProviderKeySave}
        onProviderKeyClear={handleProviderKeyClear}
        onProviderKeyTest={handleProviderKeyTest}
        onConnectionTest={testLlmConnection}
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
        onConnectionTest={testWhisperConnection}
        whisperLanguage={whisperLanguage}
        onWhisperLanguageChange={setWhisperLanguage}
        whisperComputeType={whisperComputeType}
        onWhisperComputeTypeChange={setWhisperComputeType}
        transcribeMaxChunkSeconds={transcribeMaxChunkSeconds}
        onTranscribeMaxChunkSecondsChange={setTranscribeMaxChunkSeconds}
        transcribeConcurrentChunks={transcribeConcurrentChunks}
        onTranscribeConcurrentChunksChange={setTranscribeConcurrentChunks}
        transcribeChunkOverlapSeconds={transcribeChunkOverlapSeconds}
        onTranscribeChunkOverlapSecondsChange={setTranscribeChunkOverlapSeconds}
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
        minContentBetweenAdsSeconds={minContentBetweenAdsSeconds}
        onMinContentBetweenAdsSecondsChange={setMinContentBetweenAdsSeconds}
        verificationMissHoldMinConfidence={verificationMissHoldMinConfidence}
        onVerificationMissHoldMinConfidenceChange={setVerificationMissHoldMinConfidence}
        verificationMissAutocutMinConfidence={verificationMissAutocutMinConfidence}
        onVerificationMissAutocutMinConfidenceChange={setVerificationMissAutocutMinConfidence}
        learningMinConfidence={learningMinConfidence}
        onLearningMinConfidenceChange={setLearningMinConfidence}
        learningMinConfidenceLong={learningMinConfidenceLong}
        onLearningMinConfidenceLongChange={setLearningMinConfidenceLong}
        differentialMeasuredCorrMax={differentialMeasuredCorrMax}
        onDifferentialMeasuredCorrMaxChange={setDifferentialMeasuredCorrMax}
        differentialHoldMinSeconds={differentialHoldMinSeconds}
        onDifferentialHoldMinSecondsChange={setDifferentialHoldMinSeconds}
      />

      <PromptsSection
        systemPrompt={systemPrompt}
        verificationPrompt={verificationPrompt}
        systemPromptOverride={systemPromptOverride}
        verificationPromptOverride={verificationPromptOverride}
        onSystemPromptChange={setSystemPrompt}
        onVerificationPromptChange={setVerificationPrompt}
        onSystemPromptOverrideChange={setSystemPromptOverride}
        onVerificationPromptOverrideChange={setVerificationPromptOverride}
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
        audioNormalizeEnabled={audioNormalizeEnabled}
        onAudioNormalizeEnabledChange={setAudioNormalizeEnabled}
        audioNormalizeIntensity={audioNormalizeIntensity}
        onAudioNormalizeIntensityChange={setAudioNormalizeIntensity}
        maxAudioDownloadMb={maxAudioDownloadMb}
        onMaxAudioDownloadMbChange={setMaxAudioDownloadMb}
      />

      <Podcasting20Section
        vttTranscriptsEnabled={vttTranscriptsEnabled}
        chaptersEnabled={chaptersEnabled}
        podpingEnabled={podpingEnabled}
        onVttTranscriptsEnabledChange={setVttTranscriptsEnabled}
        onChaptersEnabledChange={setChaptersEnabled}
        onPodpingEnabledChange={setPodpingEnabled}
      />

      <CoverArtSection
        artworkWatermarkEnabled={artworkWatermarkEnabled}
        onArtworkWatermarkEnabledChange={setArtworkWatermarkEnabled}
        maxArtworkBytes={maxArtworkBytes}
        onMaxArtworkBytesChange={setMaxArtworkBytes}
        onRefreshArtwork={() => refreshArtworkMutation.mutate()}
        refreshArtworkPending={refreshArtworkMutation.isPending}
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
        maxRssBytes={maxRssBytes}
        onMaxRssBytesChange={setMaxRssBytes}
      />

      <OfflineQueueSection />

      <DatabaseBackupSection />

      <NotificationsSection />

      <AuthenticatedFeedsSection />

      <SecuritySection
        isPasswordSet={isPasswordSet}
        logout={logout}
        refreshStatus={refreshStatus}
        cryptoReady={providersState?.cryptoReady ?? false}
        plaintextSecretsCount={status?.security?.plaintextSecretsCount ?? 0}
      />

      </div>
      </SettingsSearchContext.Provider>

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
            <ConfirmResetButton
              label="Reset All"
              isPending={resetMutation.isPending}
              onConfirm={() => resetMutation.mutate()}
            />
            <button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className={`px-6 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm font-medium`}
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

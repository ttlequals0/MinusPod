import { useState, useEffect, useRef } from 'react';
import { useSyncFromQuery } from '../hooks/useSyncFromQuery';
import { useLocation } from 'react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, resetPrompt, getWhisperModels, getSystemStatus, runCleanup, getRetention, updateRetention, getProcessingTimeouts, updateProcessingTimeouts, getAudioSettings, updateAudioSettings } from '../api/settings';
import type { PromptName } from '../api/settings';
import { useModelCatalog } from '../hooks/useModelCatalog';
import { useModelsRefresh } from '../hooks/useModelsRefresh';
import { getReviewerSettings, updateReviewerSettings } from '../api/community';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { SkeletonPageHeader, SkeletonRows } from '../components/Skeleton';
import type { AffectedRunsAction, BadgePosition, EpisodeLogLevel, LowAdYieldAction, LlmProvider, ModelPricingOverride, ProviderSlot, SystemStatus, WhisperBackend, WhisperApiConfig, UpdateSettingsPayload, Settings as SettingsShape } from '../api/types';
import { LLM_PROVIDERS, SLOT_PRIMARY, SLOT_SECONDARY } from '../api/types';

import SystemStatusSection from './settings/SystemStatusSection';
import StorageRetentionSection from './settings/StorageRetentionSection';
import DataManagementSection from './settings/DataManagementSection';
import DatabaseStatsSection from './settings/DatabaseStatsSection';
import NotificationsSection from './settings/NotificationsSection';
import AuthenticatedFeedsSection from './settings/AuthenticatedFeedsSection';
import SecuritySection from './settings/SecuritySection';
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
  testSecondaryProviderConnection,
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
import TranscriptNormalizationSection from './settings/TranscriptNormalizationSection';
import SeedSponsorsSection from './settings/SeedSponsorsSection';
import GlobalDefaultsSection from './settings/GlobalDefaultsSection';
import SegmentActionsSection from './settings/SegmentActionsSection';
import Podcasting20Section from './settings/Podcasting20Section';
import PromptsSection from './settings/PromptsSection';
import ExperimentsSection from './settings/ExperimentsSection';
import AdReviewerSection from './settings/AdReviewerSection';
import AudioCueDetectionSection from './settings/AudioCueDetectionSection';
import PositionalPriorSection from './settings/PositionalPriorSection';
import CommunityPatternsSection from './settings/CommunityPatternsSection';
import DatabaseBackupSection from './settings/DatabaseBackupSection';
import OutboundRequestsSection from './settings/OutboundRequestsSection';
import { Search, X } from 'lucide-react';
import { SettingsSearchContext, useSettingsSearch } from '../context/SettingsSearchContext';
import { SettingsBulkCollapseProvider, type SettingsBulkCollapseSignal } from '../context/SettingsBulkCollapseContext';
import { reconcileStageSlotsForSecondaryToggle } from './settings/settingsUtils';
import { btnPrimary } from '../components/buttonStyles';
import { focusRing } from '../components/fieldStyles';

export function systemStatusRefetchInterval(status?: SystemStatus): number {
  const check = status?.podping?.check;
  return check?.status === 'pending' || check?.status === 'running' ? 2_000 : 30_000;
}

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

type StageKey = 'detection' | 'verification' | 'chapters' | 'review';

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
  const [chapterPrompt, setChapterPrompt] = useState('');
  const [systemPromptOverride, setSystemPromptOverride] = useState('');
  const [verificationPromptOverride, setVerificationPromptOverride] = useState('');
  const [chapterPromptOverride, setChapterPromptOverride] = useState('');
  // Form state holds no hardcoded defaults: every field is hydrated from the
  // loaded settings (or the backend-provided `settings.defaults.*`) before the
  // form renders (the page returns a loader while `settingsLoading`, and the
  // hydration block below runs in the render phase). These initializers are
  // neutral placeholders that are never displayed.
  const [reviewer, setReviewer] = useState({
    enabled: false,
    provider: '',
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
  // Expand all / Collapse all: bumps `seq` on each click so every
  // CollapsibleSection under the provider snaps to `open`, even on a repeated
  // click with the same value. Disabled while a search is active since search
  // already overrides expansion.
  const [bulkCollapseSignal, setBulkCollapseSignal] = useState<SettingsBulkCollapseSignal | null>(null);
  const triggerBulkCollapse = (open: boolean) => {
    setBulkCollapseSignal((prev) => ({ seq: (prev?.seq ?? 0) + 1, open }));
  };
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
  // Per-phase provider overrides; '' inherits (see AIModelsSection's
  // provider selects for the exact fallback per stage).
  const [detectionProvider, setDetectionProvider] = useState('');
  const [verificationProvider, setVerificationProvider] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(false);
  const [maxFeedEpisodes, setMaxFeedEpisodes] = useState(0);
  const [podpingEnabled, setPodpingEnabled] = useState(false);
  const [rssRefreshIntervalMinutes, setRssRefreshIntervalMinutes] = useState(15);
  const [onlyExposeProcessedDefault, setOnlyExposeProcessedDefault] = useState(false);
  const [artworkWatermarkEnabled, setArtworkWatermarkEnabled] = useState(false);
  const [artworkBadgePosition, setArtworkBadgePosition] = useState<BadgePosition>('bottom-right');
  const [lowAdYieldAction, setLowAdYieldAction] = useState<LowAdYieldAction>('nothing');
  const [episodeLogRetentionDays, setEpisodeLogRetentionDays] = useState(30);
  const [episodeLogLevel, setEpisodeLogLevel] = useState<EpisodeLogLevel>('debug');
  const [audioBitrate, setAudioBitrate] = useState('');
  const [audioNormalizeEnabled, setAudioNormalizeEnabled] = useState(false);
  const [audioNormalizeIntensity, setAudioNormalizeIntensity] = useState('normal');
  const [skipFlacCompression, setSkipFlacCompression] = useState(false);
  const [maxArtworkBytes, setMaxArtworkBytes] = useState(26214400);
  const [maxRssBytes, setMaxRssBytes] = useState(209715200);
  const [maxAudioDownloadMb, setMaxAudioDownloadMb] = useState(500);
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(false);
  const [chaptersEnabled, setChaptersEnabled] = useState(false);
  const [chaptersInNotes, setChaptersInNotes] = useState(false);
  const [adChaptersEnabled, setAdChaptersEnabled] = useState(false);
  const [adChaptersIncludeHeld, setAdChaptersIncludeHeld] = useState(false);
  const [adChapterTitleFormat, setAdChapterTitleFormat] = useState('Ad: {label}');
  const [adChapterHeldTitleFormat, setAdChapterHeldTitleFormat] = useState('Possible ad: {label}');
  const [adChapterResumeTitle, setAdChapterResumeTitle] = useState('Show');
  const [adChapterMinConfidence, setAdChapterMinConfidence] = useState(0.9);
  const [chaptersModel, setChaptersModel] = useState('');
  const [chaptersProvider, setChaptersProvider] = useState('');
  const [minCutConfidence, setMinCutConfidence] = useState(0);
  const [minContentBetweenAdsSeconds, setMinContentBetweenAdsSeconds] = useState(12);
  const [adDetectionExcludeStartSeconds, setAdDetectionExcludeStartSeconds] = useState(0);
  const [maxAdDurationSeconds, setMaxAdDurationSeconds] = useState(300);
  const [maxAdDurationConfirmedSeconds, setMaxAdDurationConfirmedSeconds] = useState(900);
  const [verificationMissHoldMinConfidence, setVerificationMissHoldMinConfidence] = useState(0.6);
  const [verificationMissAutocutMinConfidence, setVerificationMissAutocutMinConfidence] = useState(0);
  const [learningMinConfidence, setLearningMinConfidence] = useState(0.85);
  const [learningMinConfidenceLong, setLearningMinConfidenceLong] = useState(0.92);
  const [learningMinPatternDuration, setLearningMinPatternDuration] = useState(15);
  const [learningMaxPatternDuration, setLearningMaxPatternDuration] = useState(120);
  const [differentialMeasuredCorrMax, setDifferentialMeasuredCorrMax] = useState(0.6);
  const [differentialHoldMinSeconds, setDifferentialHoldMinSeconds] = useState(10);
  const [daiDifferentialOverridesKeep, setDaiDifferentialOverridesKeep] = useState(true);
  // Neutral placeholder (cast); replaced by hydration before the form renders.
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('' as LlmProvider);
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('');
  // Optional second provider config; off by default (single-provider
  // installs never see these fields diverge from their neutral placeholders).
  const [secondaryProviderEnabled, setSecondaryProviderEnabled] = useState(false);
  // Always the saved value: a display-only fallback would differ from the
  // baseline hydration seeds from, leaving the form permanently dirty. The
  // type select shows a placeholder while this is unset.
  const [secondaryProvider, setSecondaryProvider] = useState<LlmProvider | ''>('');
  const [secondaryProviderBaseUrl, setSecondaryProviderBaseUrl] = useState('');
  // What happens to runs still bound to the old account when a slot's
  // endpoint or provider type changes. Requeue keeps the work.
  const [affectedRunsAction, setAffectedRunsAction] = useState<AffectedRunsAction>('requeue');
  // True once the user edits or clears the secondary base URL, so an inline key
  // save can send an intentional clear ('') while a pre-hydration '' is skipped.
  const [secondaryBaseUrlDirty, setSecondaryBaseUrlDirty] = useState(false);
  // Manual per-provider request-rate limits (#747); 0 = unlimited.
  const [providerRequestsPerMin, setProviderRequestsPerMin] = useState(0);
  const [providerRequestsPerDay, setProviderRequestsPerDay] = useState(0);
  const [secondaryProviderRequestsPerMin, setSecondaryProviderRequestsPerMin] = useState(0);
  const [secondaryProviderRequestsPerDay, setSecondaryProviderRequestsPerDay] = useState(0);
  const [providerTokensPerMin, setProviderTokensPerMin] = useState(0);
  const [secondaryProviderTokensPerMin, setSecondaryProviderTokensPerMin] = useState(0);
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
  const [whisperApiTimeoutSeconds, setWhisperApiTimeoutSeconds] = useState(600);
  const [whisperPoolEnabled, setWhisperPoolEnabled] = useState(false);
  const [whisperPoolMaxRequests, setWhisperPoolMaxRequests] = useState(4);
  const [whisperPoolMaxEpisodes, setWhisperPoolMaxEpisodes] = useState(1);
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
    // A new key can lift a rate-limit hold server-side; refetch so the
    // paused banner does not sit there stale for the 30s staleTime.
    queryClient.invalidateQueries({ queryKey: ['rateLimitHold'] });
  };
  const handleProviderKeyClear = async (provider: ProviderName) => {
    await clearProvider(provider);
    await reloadProviders();
    queryClient.invalidateQueries({ queryKey: ['rateLimitHold'] });
  };
  const handleProviderKeyTest = (provider: ProviderName) => testProvider(provider);

  // The secondary slot's key has no dedicated /settings/providers/secondary
  // REST surface (unlike the primary keys): it saves/clears through the
  // main settings PUT and its only test is the end-to-end connection probe.
  const handleSecondaryProviderKeySave = async (apiKey: string) => {
    // Co-persist the slot's type and base URL with the key (#234) so the
    // connection probe tests the right destination. The base URL is sent only
    // once touched, so a deliberate clear ('') commits but a pre-hydration ''
    // does not (#235).
    const body: {
      secondaryProviderApiKey: string;
      secondaryProvider?: LlmProvider;
      secondaryProviderBaseUrl?: string;
    } = { secondaryProviderApiKey: apiKey };
    if (secondaryProvider) body.secondaryProvider = secondaryProvider;
    if (secondaryBaseUrlDirty) body.secondaryProviderBaseUrl = secondaryProviderBaseUrl;
    await updateSettings(body);
    setSecondaryBaseUrlDirty(false);
    await reloadSettingsAfterSecondaryKeyChange();
  };
  const handleSecondaryProviderKeyClear = async () => {
    await updateSettings({ secondaryProviderApiKey: '' });
    await reloadSettingsAfterSecondaryKeyChange();
  };
  const reloadSettingsAfterSecondaryKeyChange = () => {
    queryClient.invalidateQueries({ queryKey: ['settings'] });
    // A secondary-key change only alters the secondary slot's catalog; refetch
    // just those model queries, not all four slots.
    queryClient.invalidateQueries({
      queryKey: ['models'],
      predicate: (q) => q.queryKey[2] === SLOT_SECONDARY,
    });
    return queryClient.invalidateQueries({ queryKey: ['rateLimitHold'] });
  };
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

  // Per-phase model catalogs. detection/verification/chaptersProvider and
  // reviewer.provider store a SLOT ('primary'/'secondary', plus
  // same_as_detection/same_as_pass), not a provider type, so each is
  // resolved to a type here before it can be used to fetch a model catalog.
  // Mirrors llm_route.py's inheritance and secondary fallback.
  const resolveStageSlots = (secondaryUsable: boolean): Record<StageKey, ProviderSlot> => {
    const detection: ProviderSlot = detectionProvider === SLOT_SECONDARY && secondaryUsable
      ? SLOT_SECONDARY
      : SLOT_PRIMARY;
    const inherited = (configured: string): ProviderSlot => (configured === SLOT_SECONDARY
      ? (secondaryUsable ? SLOT_SECONDARY : SLOT_PRIMARY)
      : configured === SLOT_PRIMARY
        ? SLOT_PRIMARY
        : detection);
    return {
      detection,
      verification: inherited(verificationProvider),
      chapters: inherited(chaptersProvider),
      review: reviewer.provider === SLOT_SECONDARY && secondaryUsable
        ? SLOT_SECONDARY
        : SLOT_PRIMARY,
    };
  };
  // llm_route.py routes a stage to the secondary only when the slot is on and
  // has a saved type; without a type it falls back to the primary, so the
  // catalogs have to show the primary's models too.
  const stageSlots = resolveStageSlots(secondaryProviderEnabled && !!secondaryProvider);
  const detectionSlot = stageSlots.detection;
  const verificationSlot = stageSlots.verification;
  const chaptersSlot = stageSlots.chapters;
  const reviewSlot = stageSlots.review;
  const effectiveDetectionProvider = detectionSlot === SLOT_SECONDARY ? secondaryProvider : llmProvider;
  const effectiveVerificationProvider = verificationSlot === SLOT_SECONDARY ? secondaryProvider : llmProvider;
  const effectiveChaptersProvider = chaptersSlot === SLOT_SECONDARY ? secondaryProvider : llmProvider;
  const effectiveReviewProvider = reviewer.provider === SLOT_SECONDARY || reviewer.provider === SLOT_PRIMARY
    ? (reviewSlot === SLOT_SECONDARY ? secondaryProvider : llmProvider)
    // same_as_pass (or an unset/legacy value): inherits the pass's own
    // provider, so no separate catalog is fetched. Callers fall back
    // to the detection catalog.
    : null;

  const handleSecondaryProviderEnabledChange = (enabled: boolean) => {
    setSecondaryProviderEnabled(enabled);
    if (enabled) {
      // Seed a concrete type so the type select and its dependent controls
      // never render blank; the user can change it before saving.
      if (!secondaryProvider) setSecondaryProvider(LLM_PROVIDERS.ANTHROPIC);
      return;
    }
    const reverted = reconcileStageSlotsForSecondaryToggle(false, {
      detectionProvider, verificationProvider, chaptersProvider,
      reviewProvider: reviewer.provider,
    });
    if (reverted.detectionProvider !== detectionProvider) setDetectionProvider(reverted.detectionProvider);
    if (reverted.verificationProvider !== verificationProvider) setVerificationProvider(reverted.verificationProvider);
    if (reverted.chaptersProvider !== chaptersProvider) setChaptersProvider(reverted.chaptersProvider);
    if (reverted.reviewProvider !== reviewer.provider) {
      setReviewer((prev) => ({ ...prev, provider: reverted.reviewProvider }));
    }
  };

  const handleSecondaryProviderChange = (provider: LlmProvider) => {
    setSecondaryProvider(provider);
    // Mirrors llmProvider's own onChange below: a saved model only survives
    // a type switch for a stage this new type doesn't actually serve. The new
    // type makes the slot usable, so resolve the stages against that.
    const next = resolveStageSlots(true);
    if (next.detection === SLOT_SECONDARY) setSelectedModel('');
    if (next.verification === SLOT_SECONDARY) setVerificationModel('');
    if (next.chapters === SLOT_SECONDARY) setChaptersModel('');
    // The base URL belongs to the old endpoint; clear it (and mark it touched)
    // so an inline key save commits the clear, not a stale URL (#235).
    setSecondaryProviderBaseUrl('');
    setSecondaryBaseUrlDirty(true);
  };

  const catalogsEnabled = !settingsLoading;
  const detectionCatalog = useModelCatalog(effectiveDetectionProvider, detectionSlot, catalogsEnabled);
  const verificationCatalog = useModelCatalog(effectiveVerificationProvider, verificationSlot, catalogsEnabled);
  const chaptersCatalog = useModelCatalog(effectiveChaptersProvider, chaptersSlot, catalogsEnabled);
  const reviewFetch = useModelCatalog(effectiveReviewProvider ?? '', reviewSlot, catalogsEnabled);
  // same_as_pass fetches no catalog of its own; the (disabled) select still
  // lists detection's models so a stored value renders.
  const reviewCatalog = effectiveReviewProvider
    ? reviewFetch
    : { ...reviewFetch, models: detectionCatalog.models };

  const { data: whisperModels } = useQuery({
    queryKey: ['whisperModels'],
    queryFn: getWhisperModels,
  });

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['status'],
    queryFn: getSystemStatus,
    refetchInterval: (query) => systemStatusRefetchInterval(
      query.state.data as SystemStatus | undefined,
    ),
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
    { key: 'chapterPrompt', kind: 'str', value: chapterPrompt, set: setChapterPrompt },
    { key: 'systemPromptOverride', kind: 'str', value: systemPromptOverride, set: setSystemPromptOverride },
    { key: 'verificationPromptOverride', kind: 'str', value: verificationPromptOverride, set: setVerificationPromptOverride },
    { key: 'chapterPromptOverride', kind: 'str', value: chapterPromptOverride, set: setChapterPromptOverride },
    // Ad reviewer (nested `reviewer` state; updatePatterns/minTrimThreshold
    // save via /settings/reviewer and are diffed by reviewerPatternsChanged).
    { key: 'reviewPrompt', kind: 'str', value: reviewer.reviewPrompt, obj: 'reviewer', prop: 'reviewPrompt' },
    { key: 'resurrectPrompt', kind: 'str', value: reviewer.resurrectPrompt, obj: 'reviewer', prop: 'resurrectPrompt' },
    { key: 'reviewPromptOverride', kind: 'str', value: reviewer.reviewPromptOverride, obj: 'reviewer', prop: 'reviewPromptOverride' },
    { key: 'resurrectPromptOverride', kind: 'str', value: reviewer.resurrectPromptOverride, obj: 'reviewer', prop: 'resurrectPromptOverride' },
    { key: 'enableAdReview', kind: 'val', useDefault: true, value: reviewer.enabled, obj: 'reviewer', prop: 'enabled' },
    { key: 'reviewModel', kind: 'str', useDefault: true, value: reviewer.model, obj: 'reviewer', prop: 'model' },
    { key: 'reviewProvider', kind: 'str', useDefault: true, value: reviewer.provider, obj: 'reviewer', prop: 'provider' },
    { key: 'reviewMaxBoundaryShift', kind: 'val', useDefault: true, value: reviewer.maxShift, obj: 'reviewer', prop: 'maxShift' },
    { key: 'adReviewerParallelAds', kind: 'val', useDefault: true, value: reviewer.parallelAds, obj: 'reviewer', prop: 'parallelAds' },
    // Models
    { key: 'claudeModel', kind: 'str', value: selectedModel, set: setSelectedModel },
    { key: 'verificationModel', kind: 'str', value: verificationModel, set: setVerificationModel },
    { key: 'chaptersModel', kind: 'str', value: chaptersModel, set: setChaptersModel },
    // Per-phase provider overrides: SLOT values, not provider types (see
    // llm_route.py). Detection's own unset baseline is 'primary';
    // verification/chapters' is 'same_as_detection'.
    { key: 'detectionProvider', kind: 'str', literal: SLOT_PRIMARY, value: detectionProvider, set: setDetectionProvider },
    { key: 'verificationProvider', kind: 'str', literal: 'same_as_detection', value: verificationProvider, set: setVerificationProvider },
    { key: 'chaptersProvider', kind: 'str', literal: 'same_as_detection', value: chaptersProvider, set: setChaptersProvider },
    { key: 'whisperModel', kind: 'str', useDefault: true, value: whisperModel, set: setWhisperModel },
    // Providers
    { key: 'llmProvider', kind: 'str', useDefault: true, value: llmProvider, set: (v) => setLlmProvider(v as LlmProvider) },
    { key: 'podcastSearchProvider', kind: 'str', value: podcastSearchProvider, set: setPodcastSearchProvider },
    { key: 'openaiBaseUrl', kind: 'str', useDefault: true, value: openaiBaseUrl, set: setOpenaiBaseUrl },
    // Secondary provider (off by default; the key itself saves separately,
    // like the primary keys, not through this batch).
    { key: 'secondaryProviderEnabled', kind: 'val', literal: false, value: secondaryProviderEnabled, set: setSecondaryProviderEnabled },
    { key: 'secondaryProvider', kind: 'str', value: secondaryProvider, set: (v) => setSecondaryProvider(v as LlmProvider | '') },
    { key: 'secondaryProviderBaseUrl', kind: 'str', value: secondaryProviderBaseUrl, set: setSecondaryProviderBaseUrl },
    { key: 'providerRequestsPerMin', kind: 'val', useDefault: true, literal: 0, value: providerRequestsPerMin, set: setProviderRequestsPerMin },
    { key: 'providerRequestsPerDay', kind: 'val', useDefault: true, literal: 0, value: providerRequestsPerDay, set: setProviderRequestsPerDay },
    { key: 'secondaryProviderRequestsPerMin', kind: 'val', useDefault: true, literal: 0, value: secondaryProviderRequestsPerMin, set: setSecondaryProviderRequestsPerMin },
    { key: 'secondaryProviderRequestsPerDay', kind: 'val', useDefault: true, literal: 0, value: secondaryProviderRequestsPerDay, set: setSecondaryProviderRequestsPerDay },
    { key: 'providerTokensPerMin', kind: 'val', useDefault: true, literal: 0, value: providerTokensPerMin, set: setProviderTokensPerMin },
    { key: 'secondaryProviderTokensPerMin', kind: 'val', useDefault: true, literal: 0, value: secondaryProviderTokensPerMin, set: setSecondaryProviderTokensPerMin },
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
    { key: 'whisperApiTimeoutSeconds', kind: 'val', useDefault: true, literal: 600, value: whisperApiTimeoutSeconds, set: setWhisperApiTimeoutSeconds },
    { key: 'whisperPoolEnabled', kind: 'val', useDefault: true, literal: false, value: whisperPoolEnabled, set: setWhisperPoolEnabled },
    { key: 'whisperPoolMaxRequests', kind: 'val', useDefault: true, literal: 4, value: whisperPoolMaxRequests, set: setWhisperPoolMaxRequests },
    { key: 'whisperPoolMaxEpisodes', kind: 'val', useDefault: true, literal: 1, value: whisperPoolMaxEpisodes, set: setWhisperPoolMaxEpisodes },
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
    { key: 'artworkBadgePosition', kind: 'str', useDefault: true, value: artworkBadgePosition, set: (v) => setArtworkBadgePosition(v as BadgePosition) },
    { key: 'lowAdYieldAction', kind: 'str', useDefault: true, value: lowAdYieldAction, set: (v) => setLowAdYieldAction(v as LowAdYieldAction) },
    { key: 'episodeLogRetentionDays', kind: 'val', useDefault: true, literal: 30, value: episodeLogRetentionDays, set: setEpisodeLogRetentionDays },
    { key: 'episodeLogLevel', kind: 'str', useDefault: true, value: episodeLogLevel, set: (v) => setEpisodeLogLevel(v as EpisodeLogLevel) },
    { key: 'vttTranscriptsEnabled', kind: 'val', useDefault: true, value: vttTranscriptsEnabled, set: setVttTranscriptsEnabled },
    { key: 'chaptersEnabled', kind: 'val', useDefault: true, value: chaptersEnabled, set: setChaptersEnabled },
    { key: 'chaptersInNotes', kind: 'val', useDefault: true, value: chaptersInNotes, set: setChaptersInNotes },
    { key: 'adChaptersEnabled', kind: 'val', useDefault: true, value: adChaptersEnabled, set: setAdChaptersEnabled },
    { key: 'adChaptersIncludeHeld', kind: 'val', useDefault: true, value: adChaptersIncludeHeld, set: setAdChaptersIncludeHeld },
    { key: 'adChapterTitleFormat', kind: 'str', useDefault: true, value: adChapterTitleFormat, set: setAdChapterTitleFormat },
    { key: 'adChapterHeldTitleFormat', kind: 'str', useDefault: true, value: adChapterHeldTitleFormat, set: setAdChapterHeldTitleFormat },
    { key: 'adChapterResumeTitle', kind: 'str', useDefault: true, value: adChapterResumeTitle, set: setAdChapterResumeTitle },
    { key: 'adChapterMinConfidence', kind: 'val', useDefault: true, value: adChapterMinConfidence, set: setAdChapterMinConfidence },
    { key: 'maxFeedEpisodes', kind: 'val', useDefault: true, value: maxFeedEpisodes, set: setMaxFeedEpisodes },
    { key: 'podpingEnabled', kind: 'val', useDefault: true, value: podpingEnabled, set: setPodpingEnabled },
    { key: 'rssRefreshIntervalMinutes', kind: 'val', useDefault: true, literal: 15, value: rssRefreshIntervalMinutes, set: setRssRefreshIntervalMinutes },
    // Ad detection
    { key: 'minCutConfidence', kind: 'val', useDefault: true, value: minCutConfidence, set: setMinCutConfidence },
    { key: 'minContentBetweenAdsSeconds', kind: 'val', useDefault: true, literal: 12, value: minContentBetweenAdsSeconds, set: setMinContentBetweenAdsSeconds },
    { key: 'adDetectionExcludeStartSeconds', kind: 'val', useDefault: true, literal: 0, value: adDetectionExcludeStartSeconds, set: setAdDetectionExcludeStartSeconds },
    { key: 'maxAdDurationSeconds', kind: 'val', useDefault: true, literal: 300, value: maxAdDurationSeconds, set: setMaxAdDurationSeconds },
    { key: 'maxAdDurationConfirmedSeconds', kind: 'val', useDefault: true, literal: 900, value: maxAdDurationConfirmedSeconds, set: setMaxAdDurationConfirmedSeconds },
    { key: 'positionalPriorEnabled', kind: 'val', useDefault: true, value: positionalPriorEnabled, set: setPositionalPriorEnabled },
    { key: 'verificationMissHoldMinConfidence', kind: 'val', useDefault: true, literal: 0.6, value: verificationMissHoldMinConfidence, set: setVerificationMissHoldMinConfidence },
    { key: 'verificationMissAutocutMinConfidence', kind: 'val', useDefault: true, literal: 0, value: verificationMissAutocutMinConfidence, set: setVerificationMissAutocutMinConfidence },
    { key: 'learningMinConfidence', kind: 'val', useDefault: true, literal: 0.85, value: learningMinConfidence, set: setLearningMinConfidence },
    { key: 'learningMinConfidenceLong', kind: 'val', useDefault: true, literal: 0.92, value: learningMinConfidenceLong, set: setLearningMinConfidenceLong },
    { key: 'learningMinPatternDuration', kind: 'val', useDefault: true, literal: 15, value: learningMinPatternDuration, set: setLearningMinPatternDuration },
    { key: 'learningMaxPatternDuration', kind: 'val', useDefault: true, literal: 120, value: learningMaxPatternDuration, set: setLearningMaxPatternDuration },
    { key: 'differentialMeasuredCorrMax', kind: 'val', useDefault: true, literal: 0.6, value: differentialMeasuredCorrMax, set: setDifferentialMeasuredCorrMax },
    { key: 'differentialHoldMinSeconds', kind: 'val', useDefault: true, literal: 10, value: differentialHoldMinSeconds, set: setDifferentialHoldMinSeconds },
    { key: 'daiDifferentialOverridesKeep', kind: 'val', useDefault: true, literal: true, value: daiDifferentialOverridesKeep, set: setDaiDifferentialOverridesKeep },
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

  // An endpoint or provider-type change moves in-flight work to a different
  // account, so the save carries the operator's decision about that work.
  const changedFields = computeChangedFields();
  const primaryAccountChanged = 'llmProvider' in changedFields || 'openaiBaseUrl' in changedFields;
  const secondaryAccountChanged =
    'secondaryProvider' in changedFields || 'secondaryProviderBaseUrl' in changedFields;

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!settings) throw new Error('Settings not loaded yet');
      const payload = computeChangedFields();
      if (podcastIndexApiKey) payload.podcastIndexApiKey = podcastIndexApiKey;
      if (podcastIndexApiSecret) payload.podcastIndexApiSecret = podcastIndexApiSecret;
      // Only when the preflight answered: a build without that endpoint
      // cannot act on the field, so it is not sent one.
      if ((primaryAccountChanged || secondaryAccountChanged)
          && queryClient.getQueryData(['affected-runs', primaryAccountChanged ? SLOT_PRIMARY : SLOT_SECONDARY])) {
        payload.affectedRunsAction = affectedRunsAction;
      }

      const tasks: Promise<unknown>[] = [];
      // Skip a PUT with an empty payload (e.g. only the reviewer-pattern
      // fields are dirty) to avoid a no-op request.
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
      queryClient.invalidateQueries({ queryKey: ['whisperCapacity'] });
      // A provider or base URL change lifts a rate-limit hold server-side.
      queryClient.invalidateQueries({ queryKey: ['rateLimitHold'] });
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

  // Chapter density fields save from the Transcripts & Chapters card; a
  // separate mutation keeps its Saving/Saved state out of the LLM Tunables
  // section (and vice versa).
  const chapterGeometryMutation = useMutation({
    mutationFn: (payload: UpdateSettingsPayload) => updateSettings(payload),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const stagesRefresh = useModelsRefresh([
    { provider: effectiveDetectionProvider, slot: detectionSlot },
    { provider: effectiveVerificationProvider, slot: verificationSlot },
    { provider: effectiveChaptersProvider, slot: chaptersSlot },
  ]);
  const reviewRefresh = useModelsRefresh([effectiveReviewProvider
    ? { provider: effectiveReviewProvider, slot: reviewSlot }
    // same_as_pass borrows detection's catalog, so that is the key to
    // rebuild and invalidate; the review slot's own key has no subscriber.
    : { provider: effectiveDetectionProvider, slot: detectionSlot }]);

  const modelPricingMutation = useMutation({
    mutationFn: ({ modelId, override }: {
      modelId: string;
      override: ModelPricingOverride | null;
    }) => updateSettings({ modelPricingOverrides: { [modelId]: override } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
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

  // Per-prompt reset (issue #626), same re-seed contract as resetPromptsMutation above.
  const resetPromptMutation = useMutation({
    mutationFn: (name: PromptName) => resetPrompt(name),
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
    return (
      <div className="max-w-3xl mx-auto pb-20">
        <SkeletonPageHeader />
        <SkeletonRows count={6} />
      </div>
    );
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
            className={`text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap ${focusRing}`}
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
            className={`text-sm text-primary hover:underline flex items-center gap-1 whitespace-nowrap ${focusRing}`}
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
            className={`absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-muted-foreground hover:text-foreground touch-manipulation ${focusRing}`}
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      <div className="flex justify-end gap-3">
        <button
          type="button"
          onClick={() => triggerBulkCollapse(true)}
          disabled={settingsMatchKeys !== null}
          className={`text-sm text-primary hover:underline ${settingsMatchKeys !== null ? 'opacity-50 pointer-events-none' : ''} ${focusRing}`}
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={() => triggerBulkCollapse(false)}
          disabled={settingsMatchKeys !== null}
          className={`text-sm text-primary hover:underline ${settingsMatchKeys !== null ? 'opacity-50 pointer-events-none' : ''} ${focusRing}`}
        >
          Collapse all
        </button>
      </div>

      <SettingsBulkCollapseProvider value={bulkCollapseSignal}>
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
        podpingEnabled={podpingEnabled}
        onPodpingEnabledChange={setPodpingEnabled}
        maxFeedEpisodes={maxFeedEpisodes}
        onMaxFeedEpisodesChange={setMaxFeedEpisodes}
        onlyExposeProcessedDefault={onlyExposeProcessedDefault}
        onOnlyExposeProcessedDefaultChange={setOnlyExposeProcessedDefault}
        lowAdYieldAction={lowAdYieldAction}
        onLowAdYieldActionChange={setLowAdYieldAction}
        episodeLogRetentionDays={episodeLogRetentionDays}
        onEpisodeLogRetentionDaysChange={setEpisodeLogRetentionDays}
        episodeLogLevel={episodeLogLevel}
        onEpisodeLogLevelChange={setEpisodeLogLevel}
        textRecurrenceHints={settings?.textRecurrenceHints?.value ?? settings?.defaults?.textRecurrenceHints ?? false}
        onTextRecurrenceHintsChange={(v) => tunableMutation.mutate({ textRecurrenceHints: v })}
        skipSecondPass={settings?.skipSecondPass?.value ?? settings?.defaults?.skipSecondPass ?? false}
        onSkipSecondPassChange={(v) => tunableMutation.mutate({ skipSecondPass: v })}
        differentialFetchMode={(settings?.differentialFetchMode?.value ?? settings?.defaults?.differentialFetchMode ?? 'auto') as 'auto' | 'on' | 'off'}
        onDifferentialFetchModeChange={(v) => tunableMutation.mutate({ differentialFetchMode: v })}
      />

      <SegmentActionsSection
        segmentCategoryActions={settings?.segmentCategoryActions?.value ?? settings?.defaults?.segmentCategoryActions ?? {}}
        onSegmentCategoryActionChange={(category, action) =>
          tunableMutation.mutate({ segmentCategoryActions: { [category]: action } })}
        detectShowSegments={settings?.detectShowSegments?.value ?? settings?.defaults?.detectShowSegments ?? false}
        onDetectShowSegmentsChange={(v) => tunableMutation.mutate({ detectShowSegments: v })}
      />

      <LLMProviderSection
        llmProvider={llmProvider}
        openaiBaseUrl={openaiBaseUrl}
        pricingSourceMode={pricingSourceMode}
        onProviderChange={(p) => {
          setLlmProvider(p);
          // Only clear a stage's model if this switch actually changes its
          // effective provider, i.e. it currently resolves to primary.
          if (detectionSlot === SLOT_PRIMARY) setSelectedModel('');
          if (verificationSlot === SLOT_PRIMARY) setVerificationModel('');
          if (chaptersSlot === SLOT_PRIMARY) setChaptersModel('');
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
        llmJsonSchemaEnabled={settings?.llmJsonSchemaEnabled?.value ?? settings?.defaults?.llmJsonSchemaEnabled ?? false}
        onLlmJsonSchemaEnabledChange={(v) => tunableMutation.mutate({ llmJsonSchemaEnabled: v })}
        secondaryProviderEnabled={secondaryProviderEnabled}
        onSecondaryProviderEnabledChange={handleSecondaryProviderEnabledChange}
        secondaryProvider={secondaryProvider}
        onSecondaryProviderChange={handleSecondaryProviderChange}
        secondaryProviderBaseUrl={secondaryProviderBaseUrl}
        onSecondaryProviderBaseUrlChange={(v) => { setSecondaryProviderBaseUrl(v); setSecondaryBaseUrlDirty(true); }}
        secondaryProviderApiKeyConfigured={settings?.secondaryProviderApiKeyConfigured ?? false}
        onSecondaryProviderKeySave={handleSecondaryProviderKeySave}
        onSecondaryProviderKeyClear={handleSecondaryProviderKeyClear}
        onSecondaryConnectionTest={testSecondaryProviderConnection}
        providerRequestsPerMin={providerRequestsPerMin}
        onProviderRequestsPerMinChange={setProviderRequestsPerMin}
        providerRequestsPerDay={providerRequestsPerDay}
        onProviderRequestsPerDayChange={setProviderRequestsPerDay}
        secondaryProviderRequestsPerMin={secondaryProviderRequestsPerMin}
        onSecondaryProviderRequestsPerMinChange={setSecondaryProviderRequestsPerMin}
        secondaryProviderRequestsPerDay={secondaryProviderRequestsPerDay}
        onSecondaryProviderRequestsPerDayChange={setSecondaryProviderRequestsPerDay}
        providerTokensPerMin={providerTokensPerMin}
        onProviderTokensPerMinChange={setProviderTokensPerMin}
        primaryAccountChanged={primaryAccountChanged}
        secondaryAccountChanged={secondaryAccountChanged}
        affectedRunsAction={affectedRunsAction}
        onAffectedRunsActionChange={setAffectedRunsAction}
        secondaryProviderTokensPerMin={secondaryProviderTokensPerMin}
        onSecondaryProviderTokensPerMinChange={setSecondaryProviderTokensPerMin}
      />

      <AIModelsSection
        detectionCatalog={detectionCatalog}
        verificationCatalog={verificationCatalog}
        chaptersCatalog={chaptersCatalog}
        modelsRefresh={stagesRefresh}
        selectedModel={selectedModel}
        verificationModel={verificationModel}
        chaptersModel={chaptersModel}
        onSelectedModelChange={setSelectedModel}
        onVerificationModelChange={setVerificationModel}
        onChaptersModelChange={setChaptersModel}
        detectionProvider={detectionProvider}
        verificationProvider={verificationProvider}
        chaptersProvider={chaptersProvider}
        onDetectionProviderChange={setDetectionProvider}
        onVerificationProviderChange={setVerificationProvider}
        onChaptersProviderChange={setChaptersProvider}
        secondaryProviderEnabled={secondaryProviderEnabled}
        modelPricingOverrides={settings?.modelPricingOverrides?.value ?? {}}
        additionalModelIds={[
          reviewer.model && reviewer.model !== 'same_as_pass' ? reviewer.model : '',
        ]}
        onPricingOverrideUpdate={(modelId, override) =>
          modelPricingMutation.mutateAsync({ modelId, override })}
        pricingOverrideSavingModel={
          modelPricingMutation.isPending ? modelPricingMutation.variables?.modelId ?? null : null
        }
      />

      {settings?.stageTunables && settings?.stageTunableDefaults && (
        <StageTunablesSection
          tunables={settings.stageTunables}
          defaults={settings.stageTunableDefaults}
          llmProvider={llmProvider}
          // StageTunablesSection expects a provider TYPE (or '' to inherit),
          // not the SLOT stored in these settings. Resolve each stage's
          // slot to its actual type first (see the effective*Provider block
          // above, which mirrors llm_route.py's inheritance).
          detectionProvider={effectiveDetectionProvider}
          verificationProvider={effectiveVerificationProvider}
          chaptersProvider={effectiveChaptersProvider}
          reviewProvider={effectiveReviewProvider ?? ''}
          onSave={(payload) => stageTunablesMutation.mutate(payload)}
          saveIsPending={stageTunablesMutation.isPending}
          saveIsSuccess={stageTunablesMutation.isSuccess}
          saveError={stageTunablesMutation.error ? (stageTunablesMutation.error as Error).message : null}
          parallelWindows={settings.adDetectionParallelWindows?.value ?? settings.defaults?.adDetectionParallelWindows ?? 4}
          parallelWindowsDefault={settings.defaults?.adDetectionParallelWindows ?? 4}
          omitTemperature={settings.omitTemperature?.value ?? settings.defaults?.omitTemperature ?? false}
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
        whisperApiTimeoutSeconds={whisperApiTimeoutSeconds}
        onWhisperApiTimeoutSecondsChange={setWhisperApiTimeoutSeconds}
        onTranscribeChunkOverlapSecondsChange={setTranscribeChunkOverlapSeconds}
        skipFlacCompression={skipFlacCompression}
        onSkipFlacCompressionChange={setSkipFlacCompression}
        whisperPoolEnabled={whisperPoolEnabled}
        onWhisperPoolEnabledChange={setWhisperPoolEnabled}
        whisperPoolMaxRequests={whisperPoolMaxRequests}
        onWhisperPoolMaxRequestsChange={setWhisperPoolMaxRequests}
        whisperPoolMaxEpisodes={whisperPoolMaxEpisodes}
        onWhisperPoolMaxEpisodesChange={setWhisperPoolMaxEpisodes}
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

      <TranscriptNormalizationSection />

      <AdDetectionSection
        minCutConfidence={minCutConfidence}
        onMinCutConfidenceChange={setMinCutConfidence}
        minContentBetweenAdsSeconds={minContentBetweenAdsSeconds}
        onMinContentBetweenAdsSecondsChange={setMinContentBetweenAdsSeconds}
        adDetectionExcludeStartSeconds={adDetectionExcludeStartSeconds}
        onAdDetectionExcludeStartSecondsChange={setAdDetectionExcludeStartSeconds}
        maxAdDurationSeconds={maxAdDurationSeconds}
        onMaxAdDurationSecondsChange={setMaxAdDurationSeconds}
        maxAdDurationConfirmedSeconds={maxAdDurationConfirmedSeconds}
        onMaxAdDurationConfirmedSecondsChange={setMaxAdDurationConfirmedSeconds}
        verificationMissHoldMinConfidence={verificationMissHoldMinConfidence}
        onVerificationMissHoldMinConfidenceChange={setVerificationMissHoldMinConfidence}
        verificationMissAutocutMinConfidence={verificationMissAutocutMinConfidence}
        onVerificationMissAutocutMinConfidenceChange={setVerificationMissAutocutMinConfidence}
        learningMinConfidence={learningMinConfidence}
        onLearningMinConfidenceChange={setLearningMinConfidence}
        learningMinConfidenceLong={learningMinConfidenceLong}
        onLearningMinPatternDurationChange={setLearningMinPatternDuration}
        learningMinPatternDuration={learningMinPatternDuration}
        onLearningMaxPatternDurationChange={setLearningMaxPatternDuration}
        learningMaxPatternDuration={learningMaxPatternDuration}
        onLearningMinConfidenceLongChange={setLearningMinConfidenceLong}
        differentialMeasuredCorrMax={differentialMeasuredCorrMax}
        onDifferentialMeasuredCorrMaxChange={setDifferentialMeasuredCorrMax}
        differentialHoldMinSeconds={differentialHoldMinSeconds}
        daiDifferentialOverridesKeep={daiDifferentialOverridesKeep}
        onDaiDifferentialOverridesKeepChange={setDaiDifferentialOverridesKeep}
        onDifferentialHoldMinSecondsChange={setDifferentialHoldMinSeconds}
      />

      <AdReviewerSection
        reviewer={reviewer}
        onChange={setReviewer}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
        secondaryProviderEnabled={secondaryProviderEnabled}
        catalog={reviewCatalog}
        modelsRefresh={reviewRefresh}
        reviewPromptIsDefault={settings?.reviewPrompt.isDefault}
        resurrectPromptIsDefault={settings?.resurrectPrompt.isDefault}
        onResetReviewPrompt={() => resetPromptMutation.mutate('review')}
        onResetResurrectPrompt={() => resetPromptMutation.mutate('resurrect')}
      />

      <SeedSponsorsSection
        detection={settings?.seedSponsorsDetection?.value ?? settings?.defaults?.seedSponsorsDetection ?? true}
        verification={settings?.seedSponsorsVerification?.value ?? settings?.defaults?.seedSponsorsVerification ?? true}
        reviewer={settings?.seedSponsorsReviewer?.value ?? settings?.defaults?.seedSponsorsReviewer ?? true}
        resurrect={settings?.seedSponsorsResurrect?.value ?? settings?.defaults?.seedSponsorsResurrect ?? true}
        onChange={(key, v) => tunableMutation.mutate({ [key]: v })}
      />

      <PromptsSection
        systemPrompt={systemPrompt}
        verificationPrompt={verificationPrompt}
        chapterPrompt={chapterPrompt}
        systemPromptOverride={systemPromptOverride}
        verificationPromptOverride={verificationPromptOverride}
        chapterPromptOverride={chapterPromptOverride}
        onSystemPromptChange={setSystemPrompt}
        onVerificationPromptChange={setVerificationPrompt}
        onChapterPromptChange={setChapterPrompt}
        onSystemPromptOverrideChange={setSystemPromptOverride}
        onVerificationPromptOverrideChange={setVerificationPromptOverride}
        onChapterPromptOverrideChange={setChapterPromptOverride}
        onResetPrompts={() => resetPromptsMutation.mutate()}
        resetIsPending={resetPromptsMutation.isPending}
        systemPromptIsDefault={settings?.systemPrompt.isDefault}
        verificationPromptIsDefault={settings?.verificationPrompt.isDefault}
        chapterPromptIsDefault={settings?.chapterPrompt.isDefault}
        onResetSystemPrompt={() => resetPromptMutation.mutate('system')}
        onResetVerificationPrompt={() => resetPromptMutation.mutate('verification')}
        onResetChapterPrompt={() => resetPromptMutation.mutate('chapter')}
      />

      <CommunityPatternsSection />

      <SettingsGroupHeader title="Experiments" />

      <ExperimentsSection
        addressingMode={settings?.adAddressingMode?.value ?? settings?.defaults?.adAddressingMode ?? 'timestamps'}
        onAddressingModeChange={(v) => tunableMutation.mutate({ adAddressingMode: v })}
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
        chaptersInNotes={chaptersInNotes}
        onVttTranscriptsEnabledChange={setVttTranscriptsEnabled}
        onChaptersEnabledChange={setChaptersEnabled}
        onChaptersInNotesChange={setChaptersInNotes}
        chaptersMode={(settings?.chaptersMode?.value ?? settings?.defaults?.chaptersMode ?? 'auto') as 'auto' | 'generate' | 'off'}
        onChaptersModeChange={(v) => tunableMutation.mutate({ chaptersMode: v })}
        adChapters={{
          chaptersEnabled,
          enabled: adChaptersEnabled,
          categories: settings?.adChapterCategories?.value
            ?? settings?.defaults?.adChapterCategories ?? {},
          includeHeld: adChaptersIncludeHeld,
          titleFormat: adChapterTitleFormat,
          heldTitleFormat: adChapterHeldTitleFormat,
          resumeTitle: adChapterResumeTitle,
          minConfidence: adChapterMinConfidence,
          onEnabledChange: setAdChaptersEnabled,
          onCategoryChange: (category, checked) =>
            tunableMutation.mutate({ adChapterCategories: { [category]: checked } }),
          onIncludeHeldChange: setAdChaptersIncludeHeld,
          onTitleFormatChange: setAdChapterTitleFormat,
          onHeldTitleFormatChange: setAdChapterHeldTitleFormat,
          onResumeTitleChange: setAdChapterResumeTitle,
          onMinConfidenceChange: setAdChapterMinConfidence,
        }}
        geometry={
          settings?.stageTunables && settings?.stageTunableDefaults
            ? {
                tunables: settings.stageTunables,
                defaults: settings.stageTunableDefaults,
                onSave: (payload) => chapterGeometryMutation.mutate(payload),
                saveIsPending: chapterGeometryMutation.isPending,
                saveIsSuccess: chapterGeometryMutation.isSuccess,
                saveError: chapterGeometryMutation.error
                  ? (chapterGeometryMutation.error as Error).message
                  : null,
              }
            : undefined
        }
      />

      <CoverArtSection
        artworkWatermarkEnabled={artworkWatermarkEnabled}
        onArtworkWatermarkEnabledChange={setArtworkWatermarkEnabled}
        artworkBadgePosition={artworkBadgePosition}
        onArtworkBadgePositionChange={setArtworkBadgePosition}
        maxArtworkBytes={maxArtworkBytes}
        onMaxArtworkBytesChange={setMaxArtworkBytes}
        onRefreshArtwork={() => refreshArtworkMutation.mutate()}
        refreshArtworkPending={refreshArtworkMutation.isPending}
      />

      <SettingsGroupHeader title="Data & Security" />

      <OutboundRequestsSection />

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

      <DatabaseStatsSection database={status?.database} />

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
      </SettingsBulkCollapseProvider>

      {/* Error display */}
      {(updateMutation.error || resetMutation.error || resetPromptsMutation.error || resetPromptMutation.error) && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
          <p>{((updateMutation.error || resetMutation.error || resetPromptsMutation.error || resetPromptMutation.error) as Error).message}</p>
        </div>
      )}

      {/* Sticky save bar */}
      {hasChanges && (
        <div
          data-viewport-inset="bottom"
          className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background/80 backdrop-blur-md"
        >
          <div className="max-w-3xl mx-auto flex items-center justify-between gap-4 px-4 py-3">
            <ConfirmResetButton
              label="Reset All"
              title="Also clears your AI model choices; you may need to pick them again."
              confirmHint="Also clears your AI model choices; you may need to pick them again."
              isPending={resetMutation.isPending}
              onConfirm={() => resetMutation.mutate()}
            />
            <button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className={`px-6 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm font-medium ${focusRing}`}
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

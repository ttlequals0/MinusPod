import type { DetectionStage } from '../utils/detectionStage';
import type { CorroborationSource } from '../utils/corroboration';
import type { SegmentCategory, SegmentAction } from '../utils/segmentCategory';

// Per-feed episode status counts (#466). Keys use the API status aliases
// (DB 'processed' arrives as 'completed'); 'deferred' is the offline queue.
// Single frontend source of truth for the status set: the key type and the
// display-order array both derive from it.
export const EPISODE_STATUS_KEYS = [
  'discovered',
  'pending',
  'processing',
  'completed',
  'failed',
  'permanently_failed',
  'deferred',
] as const;

export type EpisodeStatusKey = typeof EPISODE_STATUS_KEYS[number];

export type EpisodeStatusCounts = Record<EpisodeStatusKey, number>;

export interface Feed {
  slug: string;
  title: string;
  sourceUrl: string;
  feedUrl: string;
  description?: string;
  artworkUrl?: string;
  episodeCount: number;
  processedCount?: number;
  statusCounts?: EpisodeStatusCounts;
  lastRefreshed?: string;
  // Set while the feed's origin RSS is failing to refresh; cleared on the
  // next successful refresh. lastRefreshErrorAt is the start of the
  // current failure run.
  lastRefreshError?: string | null;
  lastRefreshErrorAt?: string | null;
  // Stamped when a Podping publish notification triggers this feed's
  // refresh; null when the feed has never been refreshed via Podping.
  lastPodpingAt?: string | null;
  // Why this feed is or is not covered by podping. Null when the listener is
  // disabled instance-wide, in which case the UI shows nothing. The UI only
  // distinguishes received from the rest; the finer states are for API use.
  podpingCoverage?: 'received' | 'declared' | 'host_active' | 'unseen' | 'declined' | null;
  // Upstream <podcast:podping> declaration. podpingUses is null when the feed
  // carries no such tag.
  podpingUses?: boolean | null;
  podpingHiveAccounts?: string[];
  // When the feed's <podcast:podping> declaration was last read from a full
  // fetch; null until one succeeds.
  podpingCheckedAt?: string | null;
  createdAt?: string;
  lastEpisodeDate?: string;
  networkId?: string;
  daiPlatform?: string;
  networkIdOverride?: string | null;
  autoProcessOverride?: boolean | null;
  languageOverride?: string | null;
  titleOverride?: string | null;
  detectionMode?: string | null;
  chaptersMode?: 'auto' | 'generate' | 'off' | null;
  // Per-feed auto-process queue priority (#625). Server always resolves to
  // one of the three values; null/absent reads as 'normal'.
  queuePriority?: 'high' | 'normal' | 'low' | null;
  cueTemplateScoreOverride?: number | null;
  cueCreateFromPairsOverride?: boolean | null;
  cuePairMinBreakOverride?: number | null;
  cuePairMaxBreakOverride?: number | null;
  cuePairMaxBreakFractionOverride?: number | null;
  cueSnapConfidenceOverride?: number | null;
  cueSnapLeadOverride?: number | null;
  cueSnapLagOverride?: number | null;
  silenceSnapEnabled?: boolean | null;
  transitionSnapEnabled?: boolean | null;
  maxAdDurationOverride?: number | null;
  maxAdDurationRejectOverride?: number | null;
  cueGatedApproval?: boolean | null;
  // Layer 3 cross-fetch differential. Null means auto: the stage runs when
  // the feed looks DAI-served; an explicit true/false overrides that.
  differentialFetchEnabled?: boolean | null;
  // What the pipeline will actually do for this feed, resolved server-side
  // from the flag above plus the DAI signals. Feed detail only.
  differentialFetchEffective?: boolean;
  // Server-side heuristic: enclosure URL chain passes through a known DAI prefix domain.
  daiLikely?: boolean;
  // The show's website from the channel-level RSS <link>; feed detail only.
  websiteUrl?: string | null;
  // Pass-through (#521): episodes are downloaded and served untouched.
  passthroughEnabled?: boolean | null;
  // Skip ad detection (#538): transcripts and chapters only, no ads cut.
  skipAdDetection?: boolean | null;
  // What the pipeline will actually do, resolved server-side from the
  // passthrough/skip/detection-mode columns (each mode shadows the later
  // ones). Absent on older backends; fall back to the raw columns.
  processingMode?: 'passthrough' | 'skip_detection' | 'keep_content' | 'standard' | 'cue_only';
  // Cue-only mode: how newly-created (unproven) cue templates are treated.
  // Null/absent behaves as 'hold_new'. Ignored outside cue_only mode.
  cueOnlySafety?: 'hold_new' | 'auto_cut' | null;
  // Cue-only mode: skip transcription entirely. Null/false transcribes as
  // usual. Only meaningful under cue_only; ignored otherwise.
  skipTranscription?: boolean | null;
  maxEpisodes?: number | null;
  onlyExposeProcessedEpisodes?: boolean | null;
  // Per-feed episode title blacklist: fnmatch glob patterns matched against
  // episode titles. A match is never queued or JIT-processed.
  titleSkipPatterns?: string[];
  // Served-RSS visibility for a title-blacklisted episode. Absent/null
  // resolves to 'serve_original'.
  titleSkipAction?: 'serve_original' | 'hide' | null;
  // Per-feed segment-action overrides (issue #565): only overridden
  // categories are present (others inherit the global map); null/absent
  // means there are no per-feed overrides at all.
  segmentCategoryActions?: Partial<Record<SegmentCategory, SegmentAction>> | null;
  // Also detect intro/outro/recap/housekeeping segments. Null inherits the
  // global detectShowSegments default.
  detectShowSegments?: boolean | null;
  // Serve MinusPod episode ids as RSS item GUIDs (#598). Null/false pass
  // upstream GUIDs through; new feeds are created with true.
  ownEpisodeGuids?: boolean | null;
  // Skip the pass-2 verification scan (#599). Null/false run it.
  skipSecondPass?: boolean | null;
}

export interface AdDistributionZone {
  center: number;  // normalized 0-1
  low: number;
  high: number;
  support: number;  // distinct episodes
  boost: number;
}

export interface AdDistribution {
  slug: string;
  episodesConsidered: number;
  medianDurationSeconds: number;
  bucketCount: number;
  buckets: number[];  // cut-start counts per normalized-position bin
  totalEvents: number;
  zones: AdDistributionZone[];
}

export interface Episode {
  id: string;
  title: string;
  description?: string;
  published: string;
  duration?: number;
  status: EpisodeStatusKey;
  ad_count?: number;
  hasOriginalAudio?: boolean;
  pendingReviewCount?: number;
  error?: string | null;
  artworkUrl?: string | null;
}

export interface EpisodeNeighbor {
  id: string;
  title: string;
}

// Cross-fetch differential result (Layer 3), stored per episode as
// dai_differential. Inner keys are snake_case as produced by fetch_and_diff.
export interface DaiDifferentialRegion {
  start_s: number;
  end_s: number;
  kind: 'differential' | 'identical' | 'unknown';
  corr: number | null;
}

export interface DaiDifferential {
  status: 'ok' | 'no_differential' | 'unreliable_reencode' | 'error';
  regions: DaiDifferentialRegion[];
  refetch_meta?: Record<string, unknown>;
  error?: string | null;
}

export interface EpisodeDetail extends Episode {
  description?: string;
  originalUrl?: string;
  processedUrl?: string;
  hasOriginalAudio?: boolean;
  originalAudioUrl?: string;
  transcript?: string;
  originalTranscriptAvailable?: boolean;
  transcriptAvailable?: boolean;
  transcriptVttAvailable?: boolean;
  transcriptVttUrl?: string;
  chaptersAvailable?: boolean;
  chaptersUrl?: string;
  adMarkers?: AdSegment[];
  rejectedAdMarkers?: AdSegment[];
  pendingReviewMarkers?: AdSegment[];
  // Segments deliberately left in the audio by a per-category keep action
  // (action_applied='keep'). Distinct from rejectedAdMarkers: a deliberate
  // configuration outcome, not a rejected detection.
  keptMarkers?: AdSegment[];
  corrections?: EpisodeCorrection[];
  cueDetections?: CueDetection[];
  originalDuration?: number;
  newDuration?: number;
  timeSaved?: number;
  fileSize?: number;
  adsRemovedFirstPass?: number;
  adsRemovedVerification?: number;
  firstPassPrompt?: string;
  firstPassResponse?: string;
  verificationPrompt?: string;
  verificationResponse?: string;
  inputTokens?: number;
  outputTokens?: number;
  llmCost?: number;
  daiDifferential?: DaiDifferential;
  // Feed-declared duration (itunes:duration) in seconds; null when the feed
  // does not declare one or the episode was discovered before 2.53.0.
  rssDuration?: number | null;
  // One entry per processing run, oldest first (#519).
  processingRuns?: EpisodeProcessingRun[];
  // Set when this episode removed far less ad time than the feed's recent
  // average -- a lightly-filled DAI copy or a detection miss worth a look.
  lowAdYield?: {
    removedSeconds: number;
    feedAverageSeconds: number;
    sampleSize: number;
  } | null;
  // Set when pass-1 LLM detection failed and the episode was published on
  // pattern/cross-fetch markers alone (degraded continue). Window counts
  // are null when not cheaply available from the run's stats blob.
  partialDetection?: { reason: string; windowsFailed: number | null; windowsTotal: number | null } | null;
  // Adjacent episodes in the same feed (newest-first order): `previous` is the
  // newer episode, `next` the older one. Either is null at a feed boundary.
  navigation?: { previous: EpisodeNeighbor | null; next: EpisodeNeighbor | null };
}

// Per-run pipeline stats blob (#519). Null-heavy by design: runs recorded
// before 2.53.0 and recut runs have no blob at all, and a failed run keeps
// whatever was gathered before the failure.
export interface ProcessingRunStats {
  mode?: string;
  // Skip ad detection (#538): the run made no detection LLM calls and cut
  // nothing; stageHits/detected/verificationAdsCut are absent by design.
  detectionSkipped?: boolean | null;
  // Skip verification (#599): pass 1 ran and cut, pass 2 did not, so
  // verificationAdsCut is absent by design rather than 0.
  verificationSkipped?: boolean | null;
  // Cue-only mode: the run cut only from cue templates, no LLM call.
  cueOnly?: boolean;
  // Cue-only mode with transcription skipped: no transcript, chapters, or subtitles.
  transcriptionSkipped?: boolean;
  downloadedDuration?: number | null;
  transcriptSegments?: number;
  windows?: { total: number; failed: number } | null;
  stageHits?: {
    fingerprint: number;
    textPattern: number;
    differential: number;
    llm: number;
  } | null;
  detected?: number;
  markers?: { cut: number; held: number; notCut: number } | null;
  verificationAdsCut?: number | null;
  secondsRemoved?: number | null;
}

export interface EpisodeProcessingRun {
  runNumber: number;
  processedAt: string;
  status: 'completed' | 'failed';
  adsDetected: number;
  processingDurationSeconds: number | null;
  errorMessage: string | null;
  inputTokens: number;
  outputTokens: number;
  llmCost: number;
  stats: ProcessingRunStats | null;
}

// Per-cue detection telemetry (#350 follow-up). One row per template cue the
// matcher surfaced, with how detection used it and the user's review verdict.
// Advisory only -- a verdict never changes the cut list.
export interface CueDetection {
  id: number;
  template_id?: number | null;
  label?: string | null;
  cue_type?: string | null;
  role?: string | null;
  source: string;
  start_s: number;
  end_s: number;
  match_score?: number | null;
  confidence?: number | null;
  outcome: 'snap' | 'pair' | 'none' | 'below_threshold';
  verdict: 'pending' | 'confirmed' | 'rejected';
  // Signed distance to the nearest pre-snap LLM ad edge on the cue's eligible
  // side; null for advisory (non_ad) and below_threshold rows (#350 Phase 6).
  edge_distance_s?: number | null;
  // Why an outcome='none' cue did nothing; null otherwise.
  unused_reason?: string | null;
}

export interface AdValidation {
  decision: 'ACCEPT' | 'REVIEW' | 'REJECT';
  adjusted_confidence: number;
  original_confidence?: number;
  flags: string[];
  corrections?: string[];
}

export interface EpisodeCorrection {
  id: number;
  correction_type: 'confirm' | 'false_positive' | 'boundary_adjustment';
  original_bounds: { start: number; end: number };
  corrected_bounds?: { start: number; end: number };
  created_at: string;
}

export interface AdSegment {
  start: number;
  end: number;
  confidence: number;
  reason?: string;
  sponsor?: string;
  detection_stage?: DetectionStage;
  // Audio evidence that backed this marker (validator clamp bypass / veto exemption).
  corroborated_by?: CorroborationSource;
  // Present when an audio cue snapped this ad's start/end edge (#350).
  cue_snap?: { start?: Record<string, unknown>; end?: Record<string, unknown> };
  // Present when a silence span snapped this ad's start/end edge (Phase B).
  silence_snap?: { start?: Record<string, unknown>; end?: Record<string, unknown> };
  validation?: AdValidation;
  // Ad reviewer (issue #197) -- populated only when the reviewer ran on this ad.
  reviewer_verdict?: 'confirmed' | 'adjust' | 'reject' | 'resurrect' | 'failure';
  reviewer_original_start?: number;
  reviewer_original_end?: number;
  reviewer_reasoning?: string;
  reviewer_confidence?: number;
  reviewer_model?: string;
  source?: 'reviewer' | 'validator';
  // Phase C held-for-review fields.
  held_for_review?: boolean;
  hold_reason?:
    | 'max_duration'
    | 'no_cue_evidence'
    | 'uncorroborated_tail'
    | 'reviewer_contradiction'
    | 'no_splice_evidence'
    | 'verification_miss'
    | 'differential_uncorroborated'
    | 'cue_template_unproven'
    | 'cue_low_confidence';
  // Set when a confirm correction matched this held marker (issue #509);
  // approved holds wait for a recut to apply.
  approved?: boolean;
  // Reviewer's proposed trim on a contradiction-held marker: the sub-span it
  // identified as the actual ad. Enables approving the trimmed span.
  reviewer_proposed_start?: number;
  reviewer_proposed_end?: number;
  // What kind of content this span is (issue #565). Unset when no stage
  // classified it; the UI shows Uncategorized.
  category?: SegmentCategory;
  // What the resolved segment-action map did with this marker's category.
  // Null when the marker predates the feature or the action never resolved.
  actionApplied?: SegmentAction | null;
}

export interface SettingValue {
  value: string;
  isDefault: boolean;
}

export interface SettingValueBoolean {
  value: boolean;
  isDefault: boolean;
}

export interface SettingValueNumber {
  value: number;
  isDefault: boolean;
}

export type LlmProvider = 'anthropic' | 'openai-compatible' | 'ollama' | 'openrouter';
// Corner the MinusPod cover-art badge renders in (issue #600).
export type BadgePosition = 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
export type WhisperBackend = 'local' | 'openai-api';

export interface WhisperApiConfig {
  baseUrl: string;
  model: string;
}

export const LLM_PROVIDERS = {
  ANTHROPIC: 'anthropic' as const,
  OPENAI_COMPATIBLE: 'openai-compatible' as const,
  OLLAMA: 'ollama' as const,
  OPENROUTER: 'openrouter' as const,
};

export const WHISPER_BACKENDS = {
  LOCAL: 'local' as const,
  OPENAI_API: 'openai-api' as const,
};

export interface Settings {
  systemPrompt: SettingValue;
  verificationPrompt: SettingValue;
  reviewPrompt: SettingValue;
  resurrectPrompt: SettingValue;
  chapterPrompt: SettingValue;
  systemPromptOverride: SettingValue;
  verificationPromptOverride: SettingValue;
  reviewPromptOverride: SettingValue;
  resurrectPromptOverride: SettingValue;
  chapterPromptOverride: SettingValue;
  enableAdReview: SettingValueBoolean;
  reviewModel: SettingValue;
  reviewMaxBoundaryShift: SettingValueNumber;
  claudeModel: SettingValue;
  verificationModel: SettingValue;
  whisperModel: SettingValue;
  autoProcessEnabled: SettingValueBoolean;
  maxFeedEpisodes: SettingValueNumber;
  podpingEnabled: SettingValueBoolean;
  rssRefreshIntervalMinutes: SettingValueNumber;
  segmentCategoryActions: { value: Record<SegmentCategory, SegmentAction>; isDefault: boolean };
  onlyExposeProcessedDefault: SettingValueBoolean;
  detectShowSegments: SettingValueBoolean;
  processNewEpisodesFirst: SettingValueBoolean;
  artworkWatermarkEnabled: SettingValueBoolean;
  artworkBadgePosition: SettingValue;
  feedAuthEnabled: SettingValueBoolean;
  feedAuthKey: string | null;
  opmlModifiedUrl: string | null;
  opmlOriginalUrl: string | null;
  audioBitrate: SettingValue;
  audioNormalizeEnabled: SettingValueBoolean;
  audioNormalizeIntensity: SettingValue;
  skipFlacCompression: SettingValueBoolean;
  maxArtworkBytes: SettingValueNumber;
  maxRssBytes: SettingValueNumber;
  maxAudioDownloadMb: SettingValueNumber;
  adDetectionParallelWindows: SettingValueNumber;
  adReviewerParallelAds: SettingValueNumber;
  transcribeMaxChunkSeconds: SettingValueNumber;
  transcribeConcurrentChunks: SettingValueNumber;
  transcribeChunkOverlapSeconds: SettingValueNumber;
  whisperApiTimeoutSeconds: SettingValueNumber;
  audioCueDetectionEnabled: SettingValueBoolean;
  audioCueFreqMinHz: SettingValueNumber;
  audioCueFreqMaxHz: SettingValueNumber;
  audioCueProminenceDb: SettingValueNumber;
  audioCueMinConfidence: SettingValueNumber;
  audioCueCreateFromPairs: SettingValueBoolean;
  audioCueTemplateScore: SettingValueNumber;
  audioCueFormantAttenDb: SettingValueNumber;
  audioCueSnapConfidence: SettingValueNumber;
  audioCueSnapLeadSeconds: SettingValueNumber;
  audioCueSnapLagSeconds: SettingValueNumber;
  audioCueCaptureMinSeconds: SettingValueNumber;
  audioCueCaptureMaxSeconds: SettingValueNumber;
  audioCueCaptureMaxIntroSeconds: SettingValueNumber;
  audioCueCaptureMaxOutroSeconds: SettingValueNumber;
  audioCuePairConfidence: SettingValueNumber;
  audioCuePairMinBreakSeconds: SettingValueNumber;
  audioCuePairMaxBreakSeconds: SettingValueNumber;
  audioCuePairMaxBreakFraction: SettingValueNumber;
  silenceSnapNoiseDb: SettingValueNumber;
  silenceSnapMinDurationSeconds: SettingValueNumber;
  silenceSnapMaxDistanceSeconds: SettingValueNumber;
  minContentBetweenAdsSeconds: SettingValueNumber;
  maxAdDurationSeconds: SettingValueNumber;
  maxAdDurationConfirmedSeconds: SettingValueNumber;
  positionalPriorEnabled: SettingValueBoolean;
  verificationMissHoldMinConfidence: SettingValueNumber;
  verificationMissAutocutMinConfidence: SettingValueNumber;
  learningMinConfidence: SettingValueNumber;
  learningMinConfidenceLong: SettingValueNumber;
  differentialMeasuredCorrMax: SettingValueNumber;
  differentialHoldMinSeconds: SettingValueNumber;
  vttTranscriptsEnabled: SettingValueBoolean;
  chaptersEnabled: SettingValueBoolean;
  chaptersModel: SettingValue;
  minCutConfidence: SettingValueNumber;
  whisperBackend: SettingValue;
  whisperApiBaseUrl: SettingValue;
  whisperApiModel: SettingValue;
  whisperLanguage: SettingValue;
  whisperComputeType: SettingValue;
  llmProvider: SettingValue;
  omitTemperature: SettingValueBoolean;
  openaiBaseUrl: SettingValue;
  pricingSourceMode: SettingValue;
  apiKeyConfigured: boolean;
  podcastIndexApiKeyConfigured: boolean;
  podcastSearchProvider: SettingValue;
  openrouterBaseUrl: string;
  retentionDays: number;
  stageTunables: StageTunables;
  stageTunableDefaults: Record<keyof StageTunables, number | string | null>;
  defaults: {
    systemPrompt: string;
    verificationPrompt: string;
    reviewPrompt: string;
    resurrectPrompt: string;
    chapterPrompt: string;
    enableAdReview: boolean;
    reviewModel: string;
    reviewMaxBoundaryShift: number;
    claudeModel: string;
    verificationModel: string;
    whisperModel: string;
    autoProcessEnabled: boolean;
    maxFeedEpisodes: number;
    podpingEnabled: boolean;
    rssRefreshIntervalMinutes: number;
    segmentCategoryActions: Record<SegmentCategory, SegmentAction>;
    onlyExposeProcessedDefault: boolean;
    detectShowSegments: boolean;
    processNewEpisodesFirst: boolean;
    artworkWatermarkEnabled: boolean;
    artworkBadgePosition: string;
    feedAuthEnabled: boolean;
    vttTranscriptsEnabled: boolean;
    chaptersEnabled: boolean;
    chaptersModel: string;
    minCutConfidence: number;
    llmProvider: LlmProvider;
    omitTemperature: boolean;
    openaiBaseUrl: string;
    pricingSourceMode: string;
    openrouterBaseUrl: string;
    whisperBackend: WhisperBackend;
    whisperApiBaseUrl: string;
    whisperApiModel: string;
    whisperLanguage: string;
    whisperComputeType: string;
    audioBitrate: string;
    audioNormalizeEnabled: boolean;
    audioNormalizeIntensity: string;
    skipFlacCompression: boolean;
    maxArtworkBytes: number;
    maxRssBytes: number;
    maxAudioDownloadMb: number;
    adDetectionParallelWindows: number;
    adReviewerParallelAds: number;
    transcribeMaxChunkSeconds: number;
    transcribeConcurrentChunks: number;
    transcribeChunkOverlapSeconds: number;
    whisperApiTimeoutSeconds: number;
    audioCueDetectionEnabled: boolean;
    audioCueFreqMinHz: number;
    audioCueFreqMaxHz: number;
    audioCueProminenceDb: number;
    audioCueMinConfidence: number;
    audioCueCreateFromPairs: boolean;
    audioCueTemplateScore: number;
    audioCueFormantAttenDb: number;
    audioCueSnapConfidence: number;
    audioCueSnapLeadSeconds: number;
    audioCueSnapLagSeconds: number;
    audioCueCaptureMinSeconds: number;
    audioCueCaptureMaxSeconds: number;
    audioCueCaptureMaxIntroSeconds: number;
    audioCueCaptureMaxOutroSeconds: number;
    audioCuePairConfidence: number;
    audioCuePairMinBreakSeconds: number;
    audioCuePairMaxBreakSeconds: number;
    audioCuePairMaxBreakFraction: number;
    silenceSnapNoiseDb: number;
    silenceSnapMinDurationSeconds: number;
    silenceSnapMaxDistanceSeconds: number;
    minContentBetweenAdsSeconds: number;
    maxAdDurationSeconds: number;
    maxAdDurationConfirmedSeconds: number;
    positionalPriorEnabled: boolean;
    verificationMissHoldMinConfidence: number;
    verificationMissAutocutMinConfidence: number;
    learningMinConfidence: number;
    learningMinConfidenceLong: number;
    differentialMeasuredCorrMax: number;
    differentialHoldMinSeconds: number;
  };
}

export interface UpdateSettingsPayload {
  systemPrompt?: string;
  verificationPrompt?: string;
  reviewPrompt?: string;
  resurrectPrompt?: string;
  chapterPrompt?: string;
  systemPromptOverride?: string;
  verificationPromptOverride?: string;
  reviewPromptOverride?: string;
  resurrectPromptOverride?: string;
  chapterPromptOverride?: string;
  enableAdReview?: boolean;
  reviewModel?: string;
  reviewMaxBoundaryShift?: number;
  claudeModel?: string;
  verificationModel?: string;
  whisperModel?: string;
  autoProcessEnabled?: boolean;
  maxFeedEpisodes?: number;
  podpingEnabled?: boolean;
  rssRefreshIntervalMinutes?: number;
  // Partial map: only the categories being changed need to be present. The
  // backend merges this over the stored global map (unlike the per-feed
  // PATCH, which replaces the stored map outright).
  segmentCategoryActions?: Partial<Record<SegmentCategory, SegmentAction>>;
  onlyExposeProcessedDefault?: boolean;
  detectShowSegments?: boolean;
  processNewEpisodesFirst?: boolean;
  artworkWatermarkEnabled?: boolean;
  artworkBadgePosition?: string;
  feedAuthEnabled?: boolean;
  audioBitrate?: string;
  audioNormalizeEnabled?: boolean;
  audioNormalizeIntensity?: string;
  skipFlacCompression?: boolean;
  maxArtworkBytes?: number;
  maxRssBytes?: number;
  maxAudioDownloadMb?: number;
  adDetectionParallelWindows?: number;
  adReviewerParallelAds?: number;
  transcribeMaxChunkSeconds?: number;
  transcribeConcurrentChunks?: number;
  transcribeChunkOverlapSeconds?: number;
  whisperApiTimeoutSeconds?: number;
  audioCueDetectionEnabled?: boolean;
  audioCueFreqMinHz?: number;
  audioCueFreqMaxHz?: number;
  audioCueProminenceDb?: number;
  audioCueMinConfidence?: number;
  audioCueCreateFromPairs?: boolean;
  audioCueTemplateScore?: number;
  audioCueFormantAttenDb?: number;
  audioCueSnapConfidence?: number;
  audioCueSnapLeadSeconds?: number;
  audioCueSnapLagSeconds?: number;
  audioCueCaptureMinSeconds?: number;
  audioCueCaptureMaxSeconds?: number;
  audioCueCaptureMaxIntroSeconds?: number;
  audioCueCaptureMaxOutroSeconds?: number;
  audioCuePairConfidence?: number;
  audioCuePairMinBreakSeconds?: number;
  audioCuePairMaxBreakSeconds?: number;
  audioCuePairMaxBreakFraction?: number;
  silenceSnapNoiseDb?: number;
  silenceSnapMinDurationSeconds?: number;
  silenceSnapMaxDistanceSeconds?: number;
  minContentBetweenAdsSeconds?: number;
  maxAdDurationSeconds?: number;
  maxAdDurationConfirmedSeconds?: number;
  positionalPriorEnabled?: boolean;
  verificationMissHoldMinConfidence?: number;
  verificationMissAutocutMinConfidence?: number;
  learningMinConfidence?: number;
  learningMinConfidenceLong?: number;
  differentialMeasuredCorrMax?: number;
  differentialHoldMinSeconds?: number;
  vttTranscriptsEnabled?: boolean;
  chaptersEnabled?: boolean;
  chaptersModel?: string;
  minCutConfidence?: number;
  llmProvider?: LlmProvider;
  openaiBaseUrl?: string;
  pricingSourceMode?: string;
  whisperBackend?: WhisperBackend;
  whisperApiBaseUrl?: string;
  whisperApiKey?: string;
  whisperApiModel?: string;
  whisperLanguage?: string;
  whisperComputeType?: string;
  podcastIndexApiKey?: string;
  podcastIndexApiSecret?: string;
  podcastSearchProvider?: string;
  // Per-stage LLM tunables. Null clears the stored value (returns to default).
  detectionTemperature?: number | null;
  detectionMaxTokens?: number | null;
  detectionReasoningBudget?: number | null;
  detectionReasoningLevel?: ReasoningLevel | null;
  verificationTemperature?: number | null;
  verificationMaxTokens?: number | null;
  verificationReasoningBudget?: number | null;
  verificationReasoningLevel?: ReasoningLevel | null;
  reviewerTemperature?: number | null;
  reviewerMaxTokens?: number | null;
  reviewerReasoningBudget?: number | null;
  reviewerReasoningLevel?: ReasoningLevel | null;
  chapterBoundaryTemperature?: number | null;
  chapterBoundaryMaxTokens?: number | null;
  chapterBoundaryReasoningBudget?: number | null;
  chapterBoundaryReasoningLevel?: ReasoningLevel | null;
  chapterTargetSeconds?: number | null;
  chapterWindowSeconds?: number | null;
  chapterMaxBoundaries?: number | null;
  chapterMinDurationSeconds?: number | null;
  chapterTitleTemperature?: number | null;
  chapterTitleMaxTokens?: number | null;
  chapterTitleReasoningBudget?: number | null;
  chapterTitleReasoningLevel?: ReasoningLevel | null;
  ollamaNumCtx?: number | null;
  windowSizeSeconds?: number | null;
  windowOverlapSeconds?: number | null;
  omitTemperature?: boolean;
}

export type ReasoningLevel = 'none' | 'low' | 'medium' | 'high';

export interface StageTunableEntry<T = number | string | null> {
  value: T;
  isDefault: boolean;
  envOverride: string | null;
}

export interface StageTunables {
  detectionTemperature: StageTunableEntry<number | null>;
  detectionMaxTokens: StageTunableEntry<number | null>;
  detectionReasoningBudget: StageTunableEntry<number | null>;
  detectionReasoningLevel: StageTunableEntry<ReasoningLevel | null>;
  verificationTemperature: StageTunableEntry<number | null>;
  verificationMaxTokens: StageTunableEntry<number | null>;
  verificationReasoningBudget: StageTunableEntry<number | null>;
  verificationReasoningLevel: StageTunableEntry<ReasoningLevel | null>;
  reviewerTemperature: StageTunableEntry<number | null>;
  reviewerMaxTokens: StageTunableEntry<number | null>;
  reviewerReasoningBudget: StageTunableEntry<number | null>;
  reviewerReasoningLevel: StageTunableEntry<ReasoningLevel | null>;
  chapterBoundaryTemperature: StageTunableEntry<number | null>;
  chapterBoundaryMaxTokens: StageTunableEntry<number | null>;
  chapterBoundaryReasoningBudget: StageTunableEntry<number | null>;
  chapterBoundaryReasoningLevel: StageTunableEntry<ReasoningLevel | null>;
  chapterTargetSeconds: StageTunableEntry<number | null>;
  chapterWindowSeconds: StageTunableEntry<number | null>;
  chapterMaxBoundaries: StageTunableEntry<number | null>;
  chapterMinDurationSeconds: StageTunableEntry<number | null>;
  chapterTitleTemperature: StageTunableEntry<number | null>;
  chapterTitleMaxTokens: StageTunableEntry<number | null>;
  chapterTitleReasoningBudget: StageTunableEntry<number | null>;
  chapterTitleReasoningLevel: StageTunableEntry<ReasoningLevel | null>;
  ollamaNumCtx: StageTunableEntry<number | null>;
  windowSizeSeconds: StageTunableEntry<number | null>;
  windowOverlapSeconds: StageTunableEntry<number | null>;
}

export interface ClaudeModel {
  id: string;
  name: string;
  inputCostPerMtok?: number;
  outputCostPerMtok?: number;
  pricingSource?: string;
}

export interface WhisperModel {
  id: string;
  name: string;
  vram: string;
  speed: string;
  quality: string;
}

export interface SystemStatus {
  status: string;
  version: string;
  uptime: number;
  feeds: {
    total: number;
  };
  episodes: {
    total: number;
    byStatus: Record<string, number>;
  };
  storage: {
    usedMb: number;
    fileCount: number;
  };
  settings: {
    retentionDays: number;
    whisperModel: string;
    whisperDevice: string;
    baseUrl: string;
  };
  stats: {
    totalTimeSaved: number;
    totalInputTokens: number;
    totalOutputTokens: number;
    totalLlmCost: number;
  };
  security?: {
    cryptoReady: boolean;
    plaintextSecretsCount: number;
  };
}

export interface Sponsor {
  id: number;
  name: string;
  aliases: string[];
  category: string | null;
  common_ctas: string[];
  tags: string[];
  is_active: boolean;
  pattern_count: number;
  last_matched_at: string | null;
  created_at: string;
}

export type NormalizationCategory = 'sponsor' | 'url' | 'number' | 'phrase';

export interface SponsorNormalization {
  id: number;
  terms: string;
  canonical: string;
  category: NormalizationCategory;
  is_active: boolean;
  created_at: string;
}

export interface ProcessingHistoryEntry {
  id: number;
  podcastId: number;
  podcastSlug: string;
  podcastTitle: string;
  episodeId: string;
  episodeTitle: string;
  processedAt: string;
  processingDurationSeconds: number;
  status: 'completed' | 'failed';
  adsDetected: number;
  errorMessage?: string;
  reprocessNumber: number;
  inputTokens?: number;
  outputTokens?: number;
  llmCost?: number;
  // Duration of the downloaded copy this run processed; null for runs
  // recorded before 2.53.0 (#519).
  downloadedDuration?: number | null;
  // MinusPod version that produced this run; null for runs recorded
  // before 2.78.4.
  appVersion?: string | null;
}

export interface ProcessingHistoryResponse {
  history: ProcessingHistoryEntry[];
  total: number;
  page: number;
  limit: number;
  totalPages: number;
}

export interface BulkActionResult {
  queued: number;
  skipped: number;
  freedMb: number;
  errors: string[];
}

export interface RetentionSettings {
  retentionDays: number;
  originalRetentionDays: number;
  enabled: boolean;
}

export interface ProcessingTimeouts {
  softTimeoutSeconds: number;
  hardTimeoutSeconds: number;
  defaults: {
    softTimeoutSeconds: number;
    hardTimeoutSeconds: number;
  };
  limits: {
    softMin: number;
    hardMax: number;
  };
}

export interface ProcessingHistoryStats {
  totalProcessed: number;
  completedCount: number;
  failedCount: number;
  totalAdsDetected: number;
  avgProcessingTimeSeconds: number;
  totalProcessingTime: number;
  totalInputTokens?: number;
  totalOutputTokens?: number;
  totalLlmCost?: number;
}

export interface DashboardStats {
  totalEpisodesProcessed: number;
  avgTimeSavedSeconds: number;
  minTimeSavedSeconds: number;
  maxTimeSavedSeconds: number;
  totalTimeSavedSeconds: number;
  avgAdsRemoved: number;
  minAdsRemoved: number;
  maxAdsRemoved: number;
  totalAdsRemoved: number;
  avgCostPerEpisode: number;
  minCostPerEpisode: number;
  maxCostPerEpisode: number;
  avgProcessingTimeSeconds: number;
  minProcessingTimeSeconds: number;
  maxProcessingTimeSeconds: number;
  avgEpisodeLengthSeconds: number;
  minEpisodeLengthSeconds: number;
  maxEpisodeLengthSeconds: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalLlmCost: number;
  avgInputTokens: number;
  avgOutputTokens: number;
  // Audio cue detection experiment (#350); zero unless the experiment is enabled.
  avgAudioCuesDetected: number;
  minAudioCuesDetected: number;
  maxAudioCuesDetected: number;
  totalAudioCuesDetected: number;
}

export interface DayStats {
  day: string;
  dayIndex: number;
  count: number;
  avgAds: number;
}

export interface PodcastStats {
  podcastSlug: string;
  podcastTitle: string;
  episodeCount: number;
  totalAds: number;
  avgAds: number;
  avgEpisodeLengthSeconds: number;
  avgTimeSavedSeconds: number;
  totalCost: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  avgTokensPerEpisode: number;
}

// Ad reviewer stats (issue #197). Empty (zero counts) when reviewer hasn't run.
export interface ReviewerStats {
  totalReviews: number;
  verdictCounts: {
    confirmed: number;
    adjust: number;
    reject: number;
    resurrect: number;
    failure: number;
  };
  pass1AdjustmentCount: number;
  pass2AdjustmentCount: number;
  avgBoundaryShiftSeconds: number;
  resurrectionCount: number;
  failureCount: number;
}

export interface ReleaseInfo {
  version: string;
  releaseDate: string | null;
  url: string | null;
  notes: string;
}

export interface UpdateStatus {
  current: { version: string; releaseDate?: string };
  stable: ReleaseInfo | null;
  edge: ReleaseInfo | null;
  channel: 'stable' | 'edge';
  updateAvailable: boolean;
}

export interface UpdateCheckSettings {
  enabled: boolean;
  channel: 'stable' | 'edge';
}

export interface ReplacementAudio {
  source: 'default' | 'uploaded';
  canRevert: boolean;
  exists: boolean;
  sizeBytes: number | null;
  updatedAt: number | null;
  durationSeconds: number | null;
  channels: number | null;
  sampleRateHz: number | null;
  reverted?: boolean;
}

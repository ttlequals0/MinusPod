import { apiFileRequest, apiRequest, buildQueryString } from './client';
import { Feed, Episode, EpisodeDetail, BulkActionResult, AdDistribution, JobState, RunLogResponse, SbCandidatesResponse, SbSubmission, EpisodeRating, ProcessingMode } from './types';
import { apiUrl } from '../urlPrefix';
import { mediaUrl } from '../utils/mediaUrl';
import { IS_DEMO } from '../demo/flag';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';

export const CUE_SCORE_MIN = 0.30;
export const CUE_SCORE_MAX = 0.99;

// Direct URL for an episode's retained original audio; an <audio src> hits it
// with the session cookie (GET needs no CSRF).
export function episodeOriginalUrl(slug: string, episodeId: string): string | null {
  return mediaUrl({ kind: 'audio', url: apiUrl(`/feeds/${slug}/episodes/${episodeId}/original.mp3`) });
}

// Saves the original or the current cut. A HEAD preflight surfaces a 401
// (login redirect) or 404 as an error; the navigation itself then streams
// the attachment to disk without buffering the file in memory.
export async function downloadEpisodeAudio(
  slug: string, episodeId: string, kind: 'original' | 'cut',
): Promise<void> {
  if (IS_DEMO) throw new Error(DEMO_AUDIO_TOOLTIP);
  const file = kind === 'cut' ? 'processed' : 'original';
  const path = `/feeds/${slug}/episodes/${episodeId}/${file}.mp3?download=1`;
  await apiFileRequest(path, { method: 'HEAD' });
  window.location.assign(apiUrl(path));
}

// Direct URL for a run's raw JSONL log; the browser downloads it with the
// session cookie (GET needs no CSRF).
export function episodeRunLogDownloadUrl(
  slug: string, episodeId: string, runNumber: number,
): string {
  return apiUrl(`/feeds/${slug}/episodes/${episodeId}/runs/${runNumber}/log?format=raw`);
}

export async function getEpisodeRunLog(
  slug: string, episodeId: string, runNumber: number,
): Promise<RunLogResponse> {
  return apiRequest<RunLogResponse>(
    `/feeds/${slug}/episodes/${episodeId}/runs/${runNumber}/log`,
  );
}

// How long the editor waits for a waveform that is still being prepared.
const PEAKS_BUSY_BUDGET_MS = 10 * 60 * 1000;

interface PeaksResponse {
  episodeId: string;
  start: number;
  end: number | null;
  resolutionMs: number;
  peaks: number[];
}

export async function getEpisodePeaks(
  slug: string,
  episodeId: string,
  start: number,
  end: number,
  resolutionMs = 50,
  signal?: AbortSignal,
): Promise<PeaksResponse> {
  const qs = buildQueryString({ start, end, resolution_ms: resolutionMs });
  return apiRequest<PeaksResponse>(
    `/feeds/${slug}/episodes/${episodeId}/peaks${qs}`,
    { busyBudgetMs: PEAKS_BUSY_BUDGET_MS, signal },
  );
}


interface TranscriptSpanResponse {
  episodeId: string;
  start: number;
  end: number;
  text: string;
}

export async function getTranscriptSpan(
  slug: string,
  episodeId: string,
  start: number,
  end: number,
): Promise<TranscriptSpanResponse> {
  const qs = buildQueryString({ start, end });
  return apiRequest<TranscriptSpanResponse>(
    `/feeds/${slug}/episodes/${episodeId}/transcript-span${qs}`,
  );
}


export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
}

export interface OriginalSegment {
  start: number;
  end: number;
  text: string;
  words?: TranscriptWord[];
}

interface OriginalSegmentsResponse {
  episodeId: string;
  segments: OriginalSegment[];
}

export async function getOriginalSegments(
  slug: string,
  episodeId: string,
): Promise<OriginalSegmentsResponse> {
  return apiRequest<OriginalSegmentsResponse>(
    `/feeds/${slug}/episodes/${episodeId}/original-segments`,
  );
}

export async function getFinalSegments(
  slug: string,
  episodeId: string,
): Promise<OriginalSegmentsResponse> {
  return apiRequest<OriginalSegmentsResponse>(
    `/feeds/${slug}/episodes/${episodeId}/final-segments`,
  );
}

// Plain-text fallback for episodes whose original segments were never stored
// as JSON (pre-segment-storage). TranscriptViewer falls back to this on a
// 404 from getOriginalSegments.
export async function getOriginalTranscript(slug: string, episodeId: string): Promise<string> {
  const response = await apiRequest<{ originalTranscript: string }>(
    `/feeds/${slug}/episodes/${episodeId}/original-transcript`
  );
  return response.originalTranscript;
}

interface FeedsResponse {
  feeds: Feed[];
  // Stamped whenever an all-feeds refresh pass finishes (15-minute
  // scheduler or Refresh All); null until the first pass completes.
  lastRefreshCompletedAt: string | null;
  // Present only when the request passed page/limit; a bare request stays
  // unbounded (all feeds, no pagination metadata).
  total?: number;
  totalPages?: number;
  page?: number;
  limit?: number;
  offset?: number;
}

type FeedSortField = 'recent' | 'title';

interface GetFeedsParams {
  page?: number;
  limit?: number;
  // Adds a bounded per-feed `latestEpisodes` projection via one windowed
  // query rather than one request per feed.
  includeLatestEpisodes?: boolean;
  episodesPerFeed?: number;
  // Ordered server-side over the whole list before the page is sliced, so a
  // page must never be re-sorted in the browser.
  sortBy?: FeedSortField;
  sortDir?: 'asc' | 'desc';
}

export async function getFeedsResponse(params?: GetFeedsParams): Promise<FeedsResponse> {
  const qs = buildQueryString({
    page: params?.page,
    limit: params?.limit,
    includeLatestEpisodes: params?.includeLatestEpisodes,
    episodesPerFeed: params?.episodesPerFeed,
    sortBy: params?.sortBy,
    sortDir: params?.sortDir,
  });
  return apiRequest<FeedsResponse>(`/feeds${qs}`);
}

// Shared options so every consumer of the ['feeds'] cache stores the same
// FeedsResponse shape; spread and add `select` to derive a view. No-params
// shape: unbounded, no latestEpisodes projection.
export const feedsQueryOptions = {
  queryKey: ['feeds'],
  queryFn: () => getFeedsResponse(),
} as const;

// Paginated/projected variant (grouped dashboard view). Distinct queryKey
// per params so it doesn't collide with the unbounded feedsQueryOptions cache.
export function feedsQueryOptionsFor(params: GetFeedsParams) {
  return {
    queryKey: ['feeds', params] as const,
    queryFn: () => getFeedsResponse(params),
  };
}

export async function getFeeds(): Promise<Feed[]> {
  return (await getFeedsResponse()).feeds;
}

export async function getFeed(slug: string): Promise<Feed> {
  return apiRequest<Feed>(`/feeds/${slug}`);
}

interface SubscriberKey {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

interface CreatedSubscriberKey extends SubscriberKey {
  token: string;
  feedUrl: string;
}

export async function getSubscriberKeys(slug: string): Promise<SubscriberKey[]> {
  const response = await apiRequest<{ keys: SubscriberKey[] }>(
    `/feeds/${encodeURIComponent(slug)}/subscriber-keys`,
  );
  return response.keys;
}

// camelCase, unlike the snake_case per-feed SubscriberKey rows, because the all-feeds endpoint returns it that way.
export interface SubscriberKeyWithFeed {
  id: string;
  label: string;
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
  feedSlug: string;
  feedTitle: string;
}

export const ALL_SUBSCRIBER_KEYS_QUERY_KEY = ['subscriber-keys-all'] as const;

export async function getAllSubscriberKeys(): Promise<SubscriberKeyWithFeed[]> {
  const response = await apiRequest<{ keys: SubscriberKeyWithFeed[] }>('/feeds/subscriber-keys');
  return response.keys;
}

export async function createSubscriberKey(slug: string, label: string) {
  return apiRequest<CreatedSubscriberKey>(
    `/feeds/${encodeURIComponent(slug)}/subscriber-keys`,
    { method: 'POST', body: { label } },
  );
}

export async function revokeSubscriberKey(slug: string, id: string) {
  return apiRequest<{ revoked: boolean }>(
    `/feeds/${encodeURIComponent(slug)}/subscriber-keys/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
}

export async function deleteSubscriberKeyRecord(slug: string, id: string) {
  return apiRequest<{ deleted: boolean }>(
    `/feeds/${encodeURIComponent(slug)}/subscriber-keys/${encodeURIComponent(id)}/record`,
    { method: 'DELETE' },
  );
}

export async function getAdDistribution(slug: string): Promise<AdDistribution> {
  return apiRequest<AdDistribution>(`/feeds/${slug}/ad-distribution`);
}

interface FeedCreateOptions {
  slug?: string;
  autoProcessOverride?: boolean | null;
  maxEpisodes?: number;
  onlyExposeProcessedEpisodes?: boolean | null;
  processingMode?: ProcessingMode;
}

export async function addFeed(sourceUrl: string, options: FeedCreateOptions = {}): Promise<Feed> {
  const { slug, autoProcessOverride, maxEpisodes, onlyExposeProcessedEpisodes, processingMode } = options;
  return apiRequest<Feed>('/feeds', {
    method: 'POST',
    body: {
      sourceUrl,
      slug,
      ...(autoProcessOverride !== undefined && { autoProcessOverride }),
      ...(maxEpisodes != null && { maxEpisodes }),
      ...(onlyExposeProcessedEpisodes !== undefined && { onlyExposeProcessedEpisodes }),
      ...(processingMode && { processingMode }),
    },
  });
}

interface AddLocalFeedPayload {
  title: string;
  slug?: string;
  description?: string;
  author?: string;
  explicit?: boolean;
  categories?: string[];
}

interface AddLocalFeedResult {
  slug: string;
  feedType: 'local';
  feedUrl: string;
  message: string;
}

export async function addLocalFeed(payload: AddLocalFeedPayload): Promise<AddLocalFeedResult> {
  return apiRequest<AddLocalFeedResult>('/feeds', {
    method: 'POST',
    body: { feedType: 'local', ...payload },
  });
}

interface AddRecentsFeedPayload {
  title?: string;
  description?: string;
}

interface AddRecentsFeedResult {
  slug: string;
  feedType: 'recents';
  feedUrl: string;
  message: string;
}

// The single combined feed of newly published episodes (#721).
export async function addRecentsFeed(payload: AddRecentsFeedPayload): Promise<AddRecentsFeedResult> {
  return apiRequest<AddRecentsFeedResult>('/feeds', {
    method: 'POST',
    body: { feedType: 'recents', ...payload },
  });
}

interface AddYoutubeFeedPayload extends FeedCreateOptions {
  sourceKind: 'playlist' | 'channel' | 'video';
  sourceId: string;
  title: string;
  description?: string;
  author?: string;
  artworkUrl?: string | null;
  includeShorts?: boolean;
}

export async function addYoutubeFeed(
  payload: AddYoutubeFeedPayload,
): Promise<{ slug: string; feedType: 'youtube'; feedUrl: string }> {
  return apiRequest('/feeds', {
    method: 'POST',
    body: { feedType: 'youtube', ...payload },
  });
}

interface UploadFeedArtworkResult {
  message: string;
  artworkUrl: string;
  warning?: string;
}

export async function uploadFeedArtwork(slug: string, file: File): Promise<UploadFeedArtworkResult> {
  const formData = new FormData();
  formData.append('file', file);
  // skipRetry, as in importOpml: a retry after a timeout could process the same upload twice.
  return apiRequest<UploadFeedArtworkResult>(`/feeds/${slug}/artwork`, {
    method: 'POST',
    body: formData,
    skipRetry: true,
  });
}

interface DeleteFeedResult {
  message: string;
  slug: string;
  pending: boolean;
}

export async function deleteFeed(slug: string): Promise<DeleteFeedResult> {
  let status = 0;
  const result = await apiRequest<{ message: string; slug: string }>(`/feeds/${slug}`, {
    method: 'DELETE',
    onResponse: (responseStatus) => { status = responseStatus; },
  });
  return { ...result, pending: status === 202 };
}

export async function refreshFeed(
  slug: string,
  options?: { force?: boolean },
): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/refresh`, {
    method: 'POST',
    body: options?.force ? { force: true } : undefined,
  });
}

export async function refreshAllFeeds(
  options?: { force?: boolean },
): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/feeds/refresh', {
    method: 'POST',
    body: options?.force ? { force: true } : undefined,
  });
}

export async function refreshAllArtwork(): Promise<{ message: string; feedCount: number }> {
  return apiRequest<{ message: string; feedCount: number }>('/feeds/refresh-artwork', {
    method: 'POST',
  });
}

export async function regenerateAllFeeds(): Promise<{ message: string; feedCount: number }> {
  return apiRequest<{ message: string; feedCount: number }>('/feeds/regenerate', {
    method: 'POST',
  });
}

export type EpisodeSelection = Pick<Episode, 'id' | 'status' | 'jobState' | 'titleSkipped' | 'descriptionSkipped' | 'durationSkipped'>;

interface EpisodesResponse {
  selection?: EpisodeSelection[];
  // Present only with selection=true; true when the match count exceeded the 501-row cap.
  truncated?: boolean;
  episodes: Episode[];
  total: number;
  limit: number;
  offset: number;
}

export async function getEpisodes(
  slug: string,
  params?: { limit?: number; offset?: number; status?: string; sortBy?: string; sortDir?: string; search?: string; selection?: boolean }
): Promise<EpisodesResponse> {
  const qs = buildQueryString({
    limit: params?.limit,
    offset: params?.offset,
    status: params?.status,
    sort_by: params?.sortBy,
    sort_dir: params?.sortDir,
    search: params?.search,
    selection: params?.selection,
  });
  return apiRequest<EpisodesResponse>(`/feeds/${slug}/episodes${qs}`);
}

export async function getEpisode(slug: string, episodeId: string): Promise<EpisodeDetail> {
  return apiRequest<EpisodeDetail>(`/feeds/${slug}/episodes/${episodeId}`);
}

export async function approveEpisode(slug: string, episodeId: string): Promise<Episode> {
  return apiRequest<Episode>(`/feeds/${slug}/episodes/${episodeId}/approve`, { method: 'POST' });
}

interface ReprocessEpisodeResult {
  message: string;
  mode: string;
  jobState?: JobState;
}

export async function reprocessEpisode(
  slug: string,
  episodeId: string,
  mode: 'reprocess' | 'full' | 'llm' | 'recut' = 'reprocess'
): Promise<ReprocessEpisodeResult> {
  return apiRequest<ReprocessEpisodeResult>(
    `/episodes/${slug}/${episodeId}/reprocess`,
    { method: 'POST', body: { mode } },
  );
}

type FeedPatchKey =
  | 'sourceUrl' | 'title' | 'description' | 'p20' | 'networkId' | 'daiPlatform'
  | 'networkIdOverride' | 'autoProcessOverride' | 'autoProcessMaxAgeHoursOverride'
  | 'languageOverride' | 'downloadUserAgentOverride' | 'feedUserAgentOverride' | 'titleOverride'
  | 'descriptionOverride' | 'detectionNotes' | 'chapterNotes' | 'detectionMode' | 'chaptersMode'
  | 'differentialFetchMode' | 'chaptersInNotes' | 'reviewLinksInNotes' | 'queuePriority'
  | 'queueOrder' | 'lowAdYieldAction' | 'episodeLogs' | 'cueTemplateScoreOverride'
  | 'cueCreateFromPairsOverride' | 'cueCutMatchesOverride' | 'cuePairMinBreakOverride'
  | 'cuePairMaxBreakOverride' | 'cuePairMaxBreakFractionOverride' | 'cueSnapConfidenceOverride'
  | 'cueSnapLeadOverride' | 'cueSnapLagOverride' | 'silenceSnapEnabled' | 'spliceVetoEnabled'
  | 'transitionSnapEnabled' | 'maxAdDurationOverride' | 'youtubeIncludeShorts'
  | 'ytProcessDelayMinutesOverride' | 'ytProbeAttemptsOverride' | 'ytProbeIntervalMinutesOverride'
  | 'ytLlmFallbackOverride' | 'ytLlmOnTopOverride' | 'ytCaptionsOverride' | 'ytHidePrivateOverride'
  | 'ytTranscribeOverride' | 'maxAdDurationRejectOverride' | 'adDetectionExcludeStartOverride'
  | 'cueGatedApproval' | 'differentialFetchEnabled' | 'passthroughEnabled' | 'skipAdDetection'
  | 'processingMode' | 'cueOnlySafety' | 'skipTranscription' | 'maxEpisodes'
  | 'onlyExposeProcessedEpisodes' | 'requireManualApproval' | 'retentionDaysOverride'
  | 'keepOriginalAudioOverride' | 'audioReplacementSoundOverride' | 'audioMp3StreamCopyOverride'
  | 'titleSkipPatterns' | 'descriptionSkipPatterns' | 'descriptionTrimPattern'
  | 'descriptionTrimMode' | 'titleSkipAction' | 'minDurationSeconds' | 'maxDurationSeconds'
  | 'segmentCategoryActions' | 'detectShowSegments' | 'ownEpisodeGuids' | 'skipSecondPass'
  | 'transcriptDifferential';

// title and p20: local feeds only (the backend rejects them on a subscribed feed); author, explicit and
// categories also work on YouTube feeds. null clears a stored value; undefined drops the key from the JSON
// body and the old value survives. processingMode conflicts with detectionMode/skipAdDetection/passthroughEnabled,
// and cue-only fields need cue_only. segmentCategoryActions replaces the stored map (null clears every override).
export type UpdateFeedPayload = Partial<Pick<Feed, FeedPatchKey>> & {
  author?: string | null;
  explicit?: boolean;
  categories?: string[] | null;
};

export interface Network {
  id: string;
  name: string;
}

export async function getNetworks(): Promise<Network[]> {
  const response = await apiRequest<{ networks: Network[] }>('/networks');
  return response.networks;
}

export async function updateFeed(slug: string, data: UpdateFeedPayload): Promise<Feed> {
  return apiRequest<Feed>(`/feeds/${slug}`, {
    method: 'PATCH',
    body: data,
  });
}

export interface OpmlImportResult {
  imported: number;
  skipped: number;
  failed: number;
  feeds: {
    imported: Array<{ url: string; slug: string }>;
    skipped: Array<{ url: string; slug: string; reason: string }>;
    failed: Array<{ url: string; error: string }>;
  };
}

export async function importOpml(file: File): Promise<OpmlImportResult> {
  const formData = new FormData();
  formData.append('opml', file);
  // skipRetry: the import is non-idempotent; a retry after a timed-out first
  // attempt could add the same feeds twice.
  return apiRequest<OpmlImportResult>('/feeds/import-opml', {
    method: 'POST',
    body: formData,
    skipRetry: true,
  });
}

export interface ReprocessAllResult {
  message: string;
  queued: number;
  skipped: number;
  mode: string;
  episodes: {
    queued: Array<{ episodeId: string; title: string }>;
    skipped: Array<{ episodeId: string; reason: string }>;
  };
}

export async function reprocessAllEpisodes(
  slug: string,
  mode: 'reprocess' | 'full' | 'llm' = 'reprocess'
): Promise<ReprocessAllResult> {
  return apiRequest<ReprocessAllResult>(`/feeds/${slug}/reprocess-all`, {
    method: 'POST',
    body: { mode },
  });
}

// The run is started in the background; the episode reports its outcome.
interface RegenerateChaptersResult {
  message: string;
  episodeId: string;
  status: 'started';
}

export async function regenerateChapters(
  slug: string,
  episodeId: string
): Promise<RegenerateChaptersResult> {
  return apiRequest<RegenerateChaptersResult>(
    `/feeds/${slug}/episodes/${episodeId}/regenerate-chapters`,
    { method: 'POST' }
  );
}

export interface RerenderSegmentsResult {
  queued: number;
  skipped: number;
}

// Re-cut every processed episode of a feed against the current segment-
// category action maps (issue #565). Reuses the recut queue, so the result
// shape mirrors reprocessAllEpisodes/bulkEpisodeAction.
export async function rerenderSegments(slug: string): Promise<RerenderSegmentsResult> {
  return apiRequest<RerenderSegmentsResult>(`/feeds/${slug}/rerender-segments`, {
    method: 'POST',
  });
}

export type BulkAction = 'process' | 'reprocess' | 'reprocess_full' | 'reprocess_llm' | 'delete';

export async function bulkEpisodeAction(
  slug: string,
  episodeIds: string[],
  action: BulkAction
): Promise<BulkActionResult> {
  return apiRequest<BulkActionResult>(`/feeds/${slug}/episodes/bulk`, {
    method: 'POST',
    body: { episodeIds, action },
  });
}

interface SetEpisodesPassthroughResult {
  updated: number;
  // Episodes also enqueued for a reprocess so the flag applies this run.
  // Only set (>0) when enabled=true; clearing never forces a reprocess.
  queued: number;
  // Episodes the change applied to, and the ones it did not with a reason.
  accepted?: string[];
  rejected?: { episodeId: string; reason: 'not_found' | 'processing' }[];
  // Authoritative state for the accepted episodes; sent only when enabling.
  jobState?: JobState;
}

// Sets or clears the per-episode pass-through override (#746) for one or
// more episodes (single-episode toggle or bulk selection).
export async function setEpisodesPassthrough(
  slug: string,
  episodeIds: string[],
  enabled: boolean,
): Promise<SetEpisodesPassthroughResult> {
  return apiRequest<SetEpisodesPassthroughResult>(`/feeds/${slug}/episodes/passthrough`, {
    method: 'POST',
    body: { episodeIds, enabled },
  });
}

// ========== Local feed episode management (#625) ==========

interface LocalEpisodeUploadResult extends Episode {
  episodeNumber?: number;
  seasonNumber?: number;
  queued: boolean;
}

// form carries the multipart fields upload_local_episode expects: audio
// (required file), title, season, episode, publishedAt, description, artwork.
export async function uploadLocalEpisode(slug: string, form: FormData): Promise<LocalEpisodeUploadResult> {
  return apiRequest<LocalEpisodeUploadResult>(`/feeds/${slug}/episodes`, {
    method: 'POST',
    body: form,
    // Non-idempotent: a retried multipart upload after a timed-out first
    // attempt could mint the episode twice under different ids.
    skipRetry: true,
  });
}

export interface LocalEpisodePatch {
  title?: string | null;
  description?: string | null;
  season?: number;
  episode?: number;
  publishedAt?: string;
  p20?: Record<string, unknown> | null;
}

export async function updateLocalEpisode(
  slug: string, episodeId: string, payload: LocalEpisodePatch,
): Promise<Episode> {
  return apiRequest<Episode>(`/feeds/${slug}/episodes/${episodeId}`, {
    method: 'PATCH',
    body: payload,
  });
}

export async function uploadLocalEpisodeArtwork(
  slug: string, episodeId: string, file: File,
): Promise<{ message: string; episodeId: string }> {
  const formData = new FormData();
  formData.append('file', file);
  return apiRequest(`/feeds/${slug}/episodes/${episodeId}/artwork`, {
    method: 'POST',
    body: formData,
    skipRetry: true,
  });
}

// ========== Local feed bulk archive import (#625) ==========

export interface ImportRejectedFile {
  file: string;
  reason: string;
}

export interface ImportUploadResult {
  staged: string[];
  rejected: ImportRejectedFile[];
}

export async function importUpload(slug: string, files: File[]): Promise<ImportUploadResult> {
  const formData = new FormData();
  for (const file of files) formData.append('files', file);
  return apiRequest<ImportUploadResult>(`/feeds/${slug}/import/upload`, {
    method: 'POST',
    body: formData,
    skipRetry: true,
  });
}

export type ImportSource = 'staging' | 'directory' | 'both';

export interface ImportPlanEntry {
  episodeId: string;
  season: number;
  episode: number;
  title: string;
  audioFile: string;
  descriptionFile: string | null;
  artworkFile: string | null;
  sidecarFile: string | null;
  publishedAt: string | null;
  publishedAtSource: 'explicit' | 'synthesized';
  bytes: number;
  mtimeNs: number;
  warnings: string[];
  errors: string[];
  // True when this episodeId already exists in the feed, regardless of overwrite: a collision marker, not an
  // outcome. Only empty errors says whether the entry commits (overwrite off turns the collision into an error;
  // on, the entry overwrites the existing episode).
  replacesExisting: boolean;
  // The existing row's episode id when replacesExisting is true, else null. Can differ from episodeId for rows
  // imported before ids were canonicalized to minimal zero-padded width ('s01e0006' vs 's01e06'). Server-side
  // bookkeeping for commit; optional so older fixtures keep compiling.
  replacesExistingId?: string | null;
}

export interface ImportPlan {
  slug: string;
  overwrite: boolean;
  planHash: string;
  entries: ImportPlanEntry[];
  rejected: ImportRejectedFile[];
  // Batch-level errors (e.g. an out-of-order explicit publish-date pair) that block commit even when no entry
  // has errors; see local_import.build_import_plan.
  batchErrors: string[];
  totals: { importable: number; rejected: number; errors: number; bytes: number };
}

export async function importScan(
  slug: string, opts: { source: ImportSource; overwrite?: boolean },
): Promise<ImportPlan> {
  return apiRequest<ImportPlan>(`/feeds/${slug}/import/scan`, {
    method: 'POST',
    body: opts,
  });
}

export async function importCommit(
  slug: string, payload: { planHash: string; source: ImportSource; overwrite?: boolean },
): Promise<{ message: string }> {
  // Non-idempotent: starts a background commit job; a retried request could
  // start it twice (the server does guard concurrent runs with a 409, but a
  // retry after a lost response would otherwise risk a duplicate start).
  return apiRequest<{ message: string }>(`/feeds/${slug}/import/commit`, {
    method: 'POST',
    body: payload,
    skipRetry: true,
  });
}

interface ImportReportEntry {
  episodeId: string;
  audioFile?: string;
  warnings?: string[];
  errors?: string[];
  error?: string;
}

interface ImportReport {
  committed: ImportReportEntry[];
  skipped: ImportReportEntry[];
  failed: ImportReportEntry[];
  // The commit engine only records the episodeId for a queued entry.
  queued: string[];
  error?: string;
}

export interface ImportStatus {
  state: 'idle' | 'running' | 'done' | 'error';
  processed: number;
  total: number;
  startedAt: string | null;
  report?: ImportReport;
}

export async function importStatus(slug: string): Promise<ImportStatus> {
  return apiRequest<ImportStatus>(`/feeds/${slug}/import/status`);
}

// Empties the feed's upload staging directory. Staging accumulates across canceled or abandoned imports (it
// only clears on a successful commit), so this runs on cancel and from the "staged earlier" note's button.
// The server 409s while an import is running.
export async function clearImportStaging(slug: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/import/staging`, {
    method: 'DELETE',
  });
}

export async function getSponsorBlockCandidates(slug: string, episodeId: string): Promise<SbCandidatesResponse> {
  return apiRequest<SbCandidatesResponse>(`/feeds/${slug}/episodes/${episodeId}/sponsorblock/candidates`);
}

export async function submitSponsorBlock(
  slug: string, episodeId: string, segments: { start: number; end: number }[],
): Promise<{ results: SbSubmission[] }> {
  return apiRequest(`/feeds/${slug}/episodes/${episodeId}/sponsorblock/submit`, { method: 'POST', body: { segments } });
}

export async function rateEpisode(
  slug: string, episodeId: string, rating: number, notes: string | null,
): Promise<{ rating: EpisodeRating; confirmedMarkers: number; dismissedMarkers: number }> {
  return apiRequest(`/feeds/${slug}/episodes/${episodeId}/rating`, { method: 'PUT', body: { rating, notes } });
}

export async function clearEpisodeRating(slug: string, episodeId: string): Promise<void> {
  await apiRequest(`/feeds/${slug}/episodes/${episodeId}/rating`, { method: 'DELETE' });
}

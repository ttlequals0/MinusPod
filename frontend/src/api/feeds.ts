import { apiRequest, buildQueryString } from './client';
import { Feed, Episode, EpisodeDetail, BulkActionResult, AdDistribution, LowAdYieldAction, EpisodeLogsOverride, RunLogResponse } from './types';
import type { SegmentCategory, SegmentAction } from '../utils/segmentCategory';

export const CUE_SCORE_MIN = 0.30;
export const CUE_SCORE_MAX = 0.99;

// Direct URL for an episode's retained original audio; an <audio src> hits it
// with the session cookie (GET needs no CSRF).
export function episodeOriginalUrl(slug: string, episodeId: string): string {
  return `/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`;
}

// Direct URL for a run's raw JSONL log; the browser downloads it with the
// session cookie (GET needs no CSRF).
export function episodeRunLogDownloadUrl(
  slug: string, episodeId: string, runNumber: number,
): string {
  return `/api/v1/feeds/${slug}/episodes/${episodeId}/runs/${runNumber}/log?format=raw`;
}

export async function getEpisodeRunLog(
  slug: string, episodeId: string, runNumber: number,
): Promise<RunLogResponse> {
  return apiRequest<RunLogResponse>(
    `/feeds/${slug}/episodes/${episodeId}/runs/${runNumber}/log`,
  );
}

export interface PeaksResponse {
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
): Promise<PeaksResponse> {
  const qs = buildQueryString({ start, end, resolution_ms: resolutionMs });
  return apiRequest<PeaksResponse>(
    `/feeds/${slug}/episodes/${episodeId}/peaks${qs}`,
  );
}


export interface TranscriptSpanResponse {
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

export interface OriginalSegmentsResponse {
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

export interface FeedsResponse {
  feeds: Feed[];
  // Stamped whenever an all-feeds refresh pass finishes (15-minute
  // scheduler or Refresh All); null until the first pass completes.
  lastRefreshCompletedAt: string | null;
}

export async function getFeedsResponse(): Promise<FeedsResponse> {
  return apiRequest<FeedsResponse>('/feeds');
}

// Shared options so every consumer of the ['feeds'] cache stores the same
// FeedsResponse shape; spread and add `select` to derive a view.
export const feedsQueryOptions = {
  queryKey: ['feeds'],
  queryFn: getFeedsResponse,
} as const;

export async function getFeeds(): Promise<Feed[]> {
  return (await getFeedsResponse()).feeds;
}

export async function getFeed(slug: string): Promise<Feed> {
  return apiRequest<Feed>(`/feeds/${slug}`);
}

export async function getAdDistribution(slug: string): Promise<AdDistribution> {
  return apiRequest<AdDistribution>(`/feeds/${slug}/ad-distribution`);
}

export async function addFeed(sourceUrl: string, slug?: string, autoProcessOverride?: boolean | null, maxEpisodes?: number, onlyExposeProcessedEpisodes?: boolean | null): Promise<Feed> {
  return apiRequest<Feed>('/feeds', {
    method: 'POST',
    body: {
      sourceUrl,
      slug,
      ...(autoProcessOverride !== undefined && { autoProcessOverride }),
      ...(maxEpisodes != null && { maxEpisodes }),
      ...(onlyExposeProcessedEpisodes !== undefined && { onlyExposeProcessedEpisodes }),
    },
  });
}

export interface AddLocalFeedPayload {
  title: string;
  slug?: string;
  description?: string;
  author?: string;
  explicit?: boolean;
  categories?: string[];
}

export interface AddLocalFeedResult {
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

export interface UploadFeedArtworkResult {
  message: string;
  artworkUrl: string;
  warning?: string;
}

export async function uploadFeedArtwork(slug: string, file: File): Promise<UploadFeedArtworkResult> {
  const formData = new FormData();
  formData.append('file', file);
  // skipRetry: mirrors importOpml -- a retry after a timed-out first attempt
  // could re-process the same upload twice.
  return apiRequest<UploadFeedArtworkResult>(`/feeds/${slug}/artwork`, {
    method: 'POST',
    body: formData,
    skipRetry: true,
  });
}

export async function deleteFeed(slug: string): Promise<void> {
  await apiRequest(`/feeds/${slug}`, { method: 'DELETE' });
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

export interface EpisodesResponse {
  episodes: Episode[];
  total: number;
  limit: number;
  offset: number;
}

export async function getEpisodes(
  slug: string,
  params?: { limit?: number; offset?: number; status?: string; sortBy?: string; sortDir?: string }
): Promise<EpisodesResponse> {
  const qs = buildQueryString({
    limit: params?.limit,
    offset: params?.offset,
    status: params?.status,
    sort_by: params?.sortBy,
    sort_dir: params?.sortDir,
  });
  return apiRequest<EpisodesResponse>(`/feeds/${slug}/episodes${qs}`);
}

export async function getEpisode(slug: string, episodeId: string): Promise<EpisodeDetail> {
  return apiRequest<EpisodeDetail>(`/feeds/${slug}/episodes/${episodeId}`);
}

export async function getOriginalTranscript(slug: string, episodeId: string): Promise<string> {
  const response = await apiRequest<{ originalTranscript: string }>(
    `/feeds/${slug}/episodes/${episodeId}/original-transcript`
  );
  return response.originalTranscript;
}

export async function reprocessEpisode(
  slug: string,
  episodeId: string,
  mode: 'reprocess' | 'full' | 'llm' | 'recut' = 'reprocess'
): Promise<{ message: string; mode: string }> {
  return apiRequest<{ message: string; mode: string }>(`/episodes/${slug}/${episodeId}/reprocess`, {
    method: 'POST',
    body: { mode },
  });
}

export interface UpdateFeedPayload {
  sourceUrl?: string;
  // Local feeds only: the backend rejects these on a subscribed feed.
  title?: string;
  description?: string;
  // null clears the stored value (the backend distinguishes "clear" from
  // "field omitted"; sending undefined here drops the key from the JSON
  // body entirely and the old value survives untouched).
  author?: string | null;
  explicit?: boolean;
  categories?: string[] | null;
  p20?: Record<string, unknown>;
  networkId?: string;
  daiPlatform?: string;
  networkIdOverride?: string | null;
  autoProcessOverride?: boolean | null;
  languageOverride?: string | null;
  titleOverride?: string | null;
  detectionMode?: string | null;
  chaptersMode?: 'auto' | 'generate' | 'off' | null;
  queuePriority?: 'high' | 'normal' | 'low' | null;
  lowAdYieldAction?: LowAdYieldAction | null;
  episodeLogs?: EpisodeLogsOverride | null;
  cueTemplateScoreOverride?: number | null;
  cueCreateFromPairsOverride?: boolean | null;
  cuePairMinBreakOverride?: number | null;
  cuePairMaxBreakOverride?: number | null;
  cuePairMaxBreakFractionOverride?: number | null;
  cueSnapConfidenceOverride?: number | null;
  cueSnapLeadOverride?: number | null;
  cueSnapLagOverride?: number | null;
  silenceSnapEnabled?: boolean | null;
  spliceVetoEnabled?: boolean | null;
  transitionSnapEnabled?: boolean | null;
  maxAdDurationOverride?: number | null;
  maxAdDurationRejectOverride?: number | null;
  cueGatedApproval?: boolean | null;
  differentialFetchEnabled?: boolean | null;
  passthroughEnabled?: boolean | null;
  skipAdDetection?: boolean | null;
  // Single-preset replacement for detectionMode/skipAdDetection/passthroughEnabled.
  // Rejected by the backend if combined with any of those legacy fields.
  processingMode?: 'passthrough' | 'skip_detection' | 'keep_content' | 'standard' | 'cue_only';
  // Cue-only mode fields; the backend rejects them outside cue_only.
  cueOnlySafety?: 'hold_new' | 'auto_cut' | null;
  skipTranscription?: boolean | null;
  maxEpisodes?: number | null;
  onlyExposeProcessedEpisodes?: boolean | null;
  retentionDaysOverride?: number | null;
  keepOriginalAudioOverride?: boolean | null;
  titleSkipPatterns?: string[];
  titleSkipAction?: 'serve_original' | 'hide' | null;
  // Per-feed segment-action overrides (issue #565). The backend replaces
  // the stored map outright, so callers must send the full desired partial
  // map (not just the changed key); null clears every override.
  segmentCategoryActions?: Partial<Record<SegmentCategory, SegmentAction>> | null;
  detectShowSegments?: boolean | null;
  ownEpisodeGuids?: boolean | null;
  skipSecondPass?: boolean | null;
}

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

export interface RegenerateChaptersResult {
  message: string;
  chapterCount: number;
  chapters: Array<{
    title: string;
    startTime: number;
    endTime?: number;
  }>;
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

// ========== Local feed episode management (#625 Task 13) ==========

export interface LocalEpisodeUploadResult extends Episode {
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

export interface BulkLocalEpisodeEdit extends LocalEpisodePatch {
  episodeId: string;
}

export async function bulkUpdateLocalEpisodes(
  slug: string, entries: BulkLocalEpisodeEdit[],
): Promise<{ updated: number }> {
  return apiRequest<{ updated: number }>(`/feeds/${slug}/episodes`, {
    method: 'PATCH',
    body: entries,
  });
}

export async function deleteLocalEpisode(
  slug: string, episodeId: string,
): Promise<{ deleted: number; episodeId: string }> {
  return apiRequest(`/feeds/${slug}/episodes/${episodeId}`, { method: 'DELETE' });
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

// ========== Local feed bulk archive import (#625 Task 13) ==========

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
  // Retries ARE wanted here (default apiRequest behavior, so no
  // skipRetry): the UI calls this once per file, so a retry re-saves at
  // most one file under its original basename -- harmless -- and a bounded
  // retry is exactly what turns a transient 429 (e.g. a large batch
  // briefly hitting a rate limit) into a silent success instead of a
  // rejected row.
  return apiRequest<ImportUploadResult>(`/feeds/${slug}/import/upload`, {
    method: 'POST',
    body: formData,
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
  // True whenever this episodeId already exists in the feed, independent
  // of overwrite -- a collision marker, not an outcome. It's only errors
  // being empty that says whether this entry actually commits: with
  // overwrite off the collision itself becomes an error; with overwrite on
  // it doesn't, and this entry cleanly overwrites the existing episode.
  replacesExisting: boolean;
  // The actual existing row's episode id when replacesExisting is true,
  // else null. Usually identical to episodeId; can differ for a row
  // imported before episode ids were canonicalized to minimal
  // zero-padded width (e.g. 's01e0006' for what this entry mints as
  // 's01e06'). Server-only bookkeeping for commit -- not currently
  // surfaced anywhere in this UI. Optional so existing fixtures/tests
  // that predate this field keep compiling unchanged.
  replacesExistingId?: string | null;
}

export interface ImportPlan {
  slug: string;
  overwrite: boolean;
  planHash: string;
  entries: ImportPlanEntry[];
  rejected: ImportRejectedFile[];
  // Batch-level errors (currently just an out-of-order explicit publish-date
  // pair) that block commit even when individual entries show no errors of
  // their own -- see local_import.build_import_plan's docstring.
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

export interface ImportReportEntry {
  episodeId: string;
  audioFile?: string;
  warnings?: string[];
  errors?: string[];
  error?: string;
}

export interface ImportReport {
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

// Empties the feed's upload staging directory. Staging accumulates across
// canceled/abandoned import attempts (every upload lands there and only
// clears on a successful commit), so this is called both when the operator
// cancels a reviewed plan and from the "staged earlier" note's own button --
// 409s server-side while an import is running for this feed.
export async function clearImportStaging(slug: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/feeds/${slug}/import/staging`, {
    method: 'DELETE',
  });
}

import { apiRequest } from './client';

export const YOUTUBE_KIND_LABELS = { channel: 'Channel', playlist: 'Playlist', video: 'Video' } as const;

export interface YouTubeSettings {
  enabled: boolean;
  apiKeyConfigured: boolean;
  sbSubmitEnabled: boolean;
  sbUserIdConfigured: boolean;
  sbUserIdGenerated: boolean;
  processDelayMinutes: number;
  sbProbeAttempts: number;
  sbProbeIntervalMinutes: number;
  sbWatchIntervalHours: number;
  sbWatchMaxDays: number;
  llmFallback: boolean;
  captionsAsTranscript: boolean;
  hidePrivate: boolean;
  transcribeFallback: boolean;
  ytdlpExtraArgs: string;
  proxyUrl: string;
  ytdlp: {
    autoUpdate: boolean;
    version: string | null;
    lastUpdateAt: string | null;
    lastCheckAt: string | null;
    lastError: string | null;
    bakedVersion: string | null;
  };
  cookies: { set: boolean; lines: number; updatedAt: string | null };
  bounds: Record<
    'processDelayMinutes' | 'sbProbeAttempts' | 'sbProbeIntervalMinutes' | 'sbWatchIntervalHours' | 'sbWatchMaxDays',
    [number, number]
  >;
}

export type YouTubeSettingsUpdate = Partial<Pick<YouTubeSettings,
  'enabled' | 'processDelayMinutes' | 'sbProbeAttempts' | 'sbProbeIntervalMinutes'
  | 'sbWatchIntervalHours' | 'sbWatchMaxDays' | 'llmFallback' | 'captionsAsTranscript' | 'hidePrivate' | 'transcribeFallback' | 'ytdlpExtraArgs' | 'proxyUrl'>>
  & { apiKey?: string | null; ytdlpAutoUpdate?: boolean; sbSubmitEnabled?: boolean; sbUserId?: string | null };

export interface YouTubePreview {
  enabled: boolean;
  source: {
    kind: 'playlist' | 'channel' | 'video';
    id: string;
    title: string;
    description: string;
    author: string;
    artworkUrl: string | null;
    videoCount: number | null;
    alreadyAdded: string | null;
  } | null;
  rssMatches: { title: string; feedUrl: string; artworkUrl: string }[];
}

export interface YouTubeSearchResult {
  kind: 'playlist' | 'channel';
  id: string;
  title: string;
  channelTitle: string;
  videoCount: number | null;
  podcast: boolean | null;
  artworkUrl: string | null;
  url: string;
}

export async function searchYouTube(query: string, signal?: AbortSignal): Promise<YouTubeSearchResult[]> {
  const data = await apiRequest<{ enabled: boolean; results: YouTubeSearchResult[] }>(
    `/youtube/search?q=${encodeURIComponent(query)}`,
    { signal },
  );
  return data.enabled ? data.results : [];
}

export async function getYouTubeSettings(): Promise<YouTubeSettings> {
  return apiRequest<YouTubeSettings>('/settings/youtube');
}

export async function updateYouTubeSettings(payload: YouTubeSettingsUpdate): Promise<YouTubeSettings> {
  return apiRequest<YouTubeSettings>('/settings/youtube', { method: 'PUT', body: payload });
}

export async function previewYouTube(url: string, signal?: AbortSignal): Promise<YouTubePreview> {
  return apiRequest<YouTubePreview>('/youtube/preview', { method: 'POST', body: { url }, signal });
}

export async function testYouTubeKey(apiKey?: string): Promise<{ ok: boolean; channelTitle: string | null }> {
  return apiRequest('/youtube/test-key', { method: 'POST', body: apiKey ? { apiKey } : {} });
}

export async function saveYouTubeCookies(cookies: string): Promise<YouTubeSettings['cookies']> {
  return apiRequest('/youtube/cookies', { method: 'POST', body: { cookies } });
}

export async function clearYouTubeCookies(): Promise<YouTubeSettings['cookies']> {
  return apiRequest('/youtube/cookies', { method: 'DELETE' });
}

export async function updateYtDlpNow(): Promise<{ updated: boolean; version: string | null; error: string | null }> {
  return apiRequest('/youtube/ytdlp/update', { method: 'POST', body: {} });
}

export async function resetYouTubeSettings(): Promise<YouTubeSettings> {
  return apiRequest<YouTubeSettings>('/settings/youtube/reset', { method: 'POST' });
}

import { apiRequest } from './client';

export interface CommunitySyncSettings {
  enabled: boolean;
  cron: string;
  lastRun: string | null;
  lastError: string | null;
  manifestVersion: string | null;
  lastSummary: string | null;
}

export interface CommunitySyncSummary {
  inserted: number;
  updated: number;
  deleted: number;
  skipped: number;
  errors: number;
  manifest_version?: number | string | null;
  fetched_at?: string;
}

export async function getCommunitySyncSettings(): Promise<CommunitySyncSettings> {
  return apiRequest<CommunitySyncSettings>('/settings/community-sync');
}

export async function updateCommunitySyncSettings(args: {
  enabled?: boolean;
  cron?: string;
}): Promise<CommunitySyncSettings> {
  return apiRequest<CommunitySyncSettings>('/settings/community-sync', {
    method: 'PUT',
    body: args,
  });
}

export async function triggerCommunitySync(): Promise<CommunitySyncSummary> {
  return apiRequest<CommunitySyncSummary>('/community-patterns/sync', {
    method: 'POST',
  });
}

export async function getCommunitySyncStatus(): Promise<CommunitySyncSettings> {
  return apiRequest<CommunitySyncSettings>('/community-patterns/sync-status');
}

export interface ReviewerSettings {
  updatePatternsFromReviewerAdjustments: boolean;
  minTrimThreshold: number;
}

export async function getReviewerSettings(): Promise<ReviewerSettings> {
  return apiRequest<ReviewerSettings>('/settings/reviewer');
}

export async function updateReviewerSettings(args: Partial<ReviewerSettings>): Promise<ReviewerSettings> {
  return apiRequest<ReviewerSettings>('/settings/reviewer', {
    method: 'PUT',
    body: args,
  });
}

export interface FeedTagBreakdown {
  effective: string[];
  rss: string[];
  episode: string[];
  user: string[];
}

export async function getFeedTags(slug: string): Promise<FeedTagBreakdown> {
  return apiRequest<FeedTagBreakdown>(`/feeds/${encodeURIComponent(slug)}/tags`);
}

export async function setFeedUserTags(slug: string, userTags: string[]): Promise<FeedTagBreakdown> {
  return apiRequest<FeedTagBreakdown>(`/feeds/${encodeURIComponent(slug)}/tags`, {
    method: 'PUT',
    body: { user_tags: userTags },
  });
}

export async function updateSponsorTags(sponsorId: number, tags: string[]): Promise<{ sponsor_id: number; tags: string[] }> {
  return apiRequest(`/sponsors/${sponsorId}/tags`, {
    method: 'PUT',
    body: { tags },
  });
}

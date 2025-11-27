export interface Feed {
  slug: string;
  title: string;
  sourceUrl: string;
  feedUrl: string;
  description?: string;
  artworkUrl?: string;
  episodeCount: number;
  processedCount?: number;
  lastRefreshed?: string;
  createdAt?: string;
}

export interface Episode {
  id: string;
  title: string;
  published: string;
  duration?: number;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  ad_count?: number;
}

export interface EpisodeDetail extends Episode {
  description?: string;
  original_url?: string;
  processed_url?: string;
  transcript?: string;
  ad_segments?: AdSegment[];
  originalDuration?: number;
  newDuration?: number;
  timeSaved?: number;
}

export interface AdSegment {
  start: number;
  end: number;
  confidence: number;
  reason?: string;
}

export interface SettingValue {
  value: string;
  isDefault: boolean;
}

export interface Settings {
  systemPrompt: SettingValue;
  userPromptTemplate: SettingValue;
  claudeModel: SettingValue;
  retentionPeriodMinutes: number;
  defaults: {
    systemPrompt: string;
    userPromptTemplate: string;
    claudeModel: string;
  };
}

export interface UpdateSettingsPayload {
  systemPrompt?: string;
  userPromptTemplate?: string;
  claudeModel?: string;
}

export interface ClaudeModel {
  id: string;
  name: string;
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
    retentionPeriodMinutes: number;
    whisperModel: string;
    whisperDevice: string;
    baseUrl: string;
  };
  stats: {
    totalTimeSaved: number;
  };
}

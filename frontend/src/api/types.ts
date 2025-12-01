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
  originalUrl?: string;
  processedUrl?: string;
  transcript?: string;
  adMarkers?: AdSegment[];
  originalDuration?: number;
  newDuration?: number;
  timeSaved?: number;
  fileSize?: number;
  adsRemovedFirstPass?: number;
  adsRemovedSecondPass?: number;
  firstPassPrompt?: string;
  firstPassResponse?: string;
  secondPassPrompt?: string;
  secondPassResponse?: string;
}

export interface AdSegment {
  start: number;
  end: number;
  confidence: number;
  reason?: string;
  pass?: 1 | 2 | 'merged';
}

export interface SettingValue {
  value: string;
  isDefault: boolean;
}

export interface SettingValueBoolean {
  value: boolean;
  isDefault: boolean;
}

export interface Settings {
  systemPrompt: SettingValue;
  claudeModel: SettingValue;
  multiPassEnabled: SettingValueBoolean;
  retentionPeriodMinutes: number;
  defaults: {
    systemPrompt: string;
    claudeModel: string;
    multiPassEnabled: boolean;
  };
}

export interface UpdateSettingsPayload {
  systemPrompt?: string;
  claudeModel?: string;
  multiPassEnabled?: boolean;
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

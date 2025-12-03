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
  description?: string;
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
  rejectedAdMarkers?: AdSegment[];
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

export interface AdValidation {
  decision: 'ACCEPT' | 'REVIEW' | 'REJECT';
  adjusted_confidence: number;
  original_confidence?: number;
  flags: string[];
  corrections?: string[];
}

export interface AdSegment {
  start: number;
  end: number;
  confidence: number;
  reason?: string;
  pass?: 1 | 2 | 'merged';
  validation?: AdValidation;
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
  secondPassPrompt: SettingValue;
  claudeModel: SettingValue;
  secondPassModel: SettingValue;
  multiPassEnabled: SettingValueBoolean;
  whisperModel: SettingValue;
  retentionPeriodMinutes: number;
  defaults: {
    systemPrompt: string;
    secondPassPrompt: string;
    claudeModel: string;
    secondPassModel: string;
    multiPassEnabled: boolean;
    whisperModel: string;
  };
}

export interface UpdateSettingsPayload {
  systemPrompt?: string;
  secondPassPrompt?: string;
  claudeModel?: string;
  secondPassModel?: string;
  multiPassEnabled?: boolean;
  whisperModel?: string;
}

export interface ClaudeModel {
  id: string;
  name: string;
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
    retentionPeriodMinutes: number;
    whisperModel: string;
    whisperDevice: string;
    baseUrl: string;
  };
  stats: {
    totalTimeSaved: number;
  };
}
